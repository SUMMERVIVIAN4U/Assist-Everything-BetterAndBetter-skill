from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from .llm import OpenAICompatibleClient, build_llm_client, llm_configured, provider_configured, provider_env_key
from .intent import classify_confirmation_intent, should_classify_confirmation
from .schemas import HarnessSession, Message, TurnTrace
from .soul import load_soul_prompt
from .tools import MemoryToolbox
from assist_everything_betterandbetter_skill.memory import MemoryItem
from assist_everything_betterandbetter_skill.skill import DECISION, HISTORY


class HarnessAgent:
    """Conversation harness that lets an agent use the generic skill runtime."""

    def __init__(
        self,
        name: str = "assist-agent",
        toolbox: MemoryToolbox | None = None,
        llm_mode: str = "auto",
        llm_client: OpenAICompatibleClient | None = None,
        memory_dir: str | None = None,
        persist_memory: bool | None = None,
    ) -> None:
        self.name = name
        self.toolbox = toolbox or MemoryToolbox(memory_dir=memory_dir, persist=persist_memory)
        self.session = HarnessSession()
        self.llm_mode = llm_mode
        self.llm_client = llm_client
        self._context_start_index = 0

    def reply(self, user_text: str, *, stage: str = "chat") -> TurnTrace:
        context = self._recent_context(include_pre_reset=True)
        rewrite_context = self._recent_context(include_pre_reset=False)
        response, call = self.toolbox.process_message(user_text, context=context)
        semantic_actions = self._maybe_classify_confirmation(user_text, context, response.to_dict())
        if semantic_actions:
            response.memory_actions.extend(semantic_actions)
        if not _authoritative_memory_operation(call):
            response.text = self._maybe_llm_rewrite(
                user_text,
                stage,
                response.text,
                response.applied_memories,
                response.memory_actions,
                rewrite_context,
            )

        user = Message(role="user", content=user_text)
        assistant = Message(role="assistant", content=response.text)
        self.session.messages.extend([user, assistant])
        turn = TurnTrace(
            id=f"turn_{len(self.session.turns) + 1:03d}",
            stage=stage,
            user=user,
            assistant=assistant,
            tool_calls=[call],
            applied_memories=response.applied_memories,
            memory_snapshot=self.toolbox.snapshot(),
            notes=_notes_from_response(response.to_dict()),
        )
        self.session.turns.append(turn)
        if _has_reset_action(call):
            self.mark_memory_reset_boundary()
        return turn

    def mark_memory_reset_boundary(self) -> None:
        self._context_start_index = len(self.session.messages)

    def _maybe_llm_rewrite(
        self,
        user_text: str,
        stage: str,
        draft: str,
        applied_memories: list[str],
        memory_actions: list[dict[str, Any]],
        context: str,
    ) -> str:
        if not self._use_remote_llm():
            return draft
        client = self.llm_client or _workbench_llm_client(self.llm_mode)
        snapshot = self.toolbox.snapshot()
        soul = load_soul_prompt()
        messages = [
            {
                "role": "system",
                "content": _system_prompt(soul),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "stage": stage,
                        "user_message": user_text,
                        "conversation_context": context,
                        "tool_draft": draft,
                        "memory_actions": memory_actions,
                        "can_claim_memory_saved": bool(memory_actions),
                        "applied_memory_ids": applied_memories,
                        "memory_snapshot": snapshot,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            rewritten = client.chat(messages, temperature=0.3).strip()
            if not memory_actions and _claims_memory_saved(rewritten):
                return draft
            return rewritten
        except Exception as exc:
            return f"{draft}\n\n远端 LLM 改写超时或失败，已回退到本地工具草稿：{exc}"

    def _recent_context(self, *, include_pre_reset: bool = False) -> str:
        messages = self.session.messages if include_pre_reset else self.session.messages[self._context_start_index :]
        return "\n".join(f"{message.role}: {message.content}" for message in messages[-8:])

    def _use_remote_llm(self) -> bool:
        if self.llm_mode == "local":
            return False
        if self.llm_mode == "auto":
            return llm_configured()
        return provider_configured(self.llm_mode)

    def _maybe_classify_confirmation(self, user_text: str, context: str, response: dict[str, Any]) -> list[dict[str, Any]]:
        if "女朋友" not in f"{user_text}\n{context}":
            return []
        actions = response.get("memory_actions", [])
        if any(action.get("detail", "").startswith("本次给女朋友的礼物已选定为") for action in actions):
            return []
        if not should_classify_confirmation(user_text, context):
            return []
        client = self.llm_client or (_workbench_llm_client(self.llm_mode) if self._use_remote_llm() else None)
        intent = classify_confirmation_intent(user_text, context, client=client)
        if not intent.is_confirmation:
            return []
        created = []
        decision = MemoryItem(
            DECISION,
            f"本次给女朋友的礼物已选定为{intent.gift_object}",
            scope="relationship_gift",
            subject="user",
            target="girlfriend",
            object=intent.gift_object,
            predicate="selected",
            source="semantic_confirmation_classifier",
            evidence=[user_text],
            applies_when=["relationship_gift"],
            tags=["礼物", "选定", intent.gift_object, "满意"],
            validity={"time_scope": "current_task", "status": "accepted"},
        )
        if not self.toolbox.skill._is_duplicate(decision):
            self.toolbox.skill.memory.add(decision)
            created.append(self.toolbox.skill.memory.events[-1])
        if intent.action == "sent":
            history = MemoryItem(
                HISTORY,
                f"已经给女朋友送过{intent.gift_object}",
                scope="relationship_gift",
                subject="user",
                target="girlfriend",
                object=intent.gift_object,
                predicate="gave",
                source="semantic_confirmation_classifier",
                evidence=[user_text],
                applies_when=["relationship_gift"],
                tags=["礼物", "送过", intent.gift_object],
                validity={"time_scope": "past"},
            )
            if not self.toolbox.skill._is_duplicate(history):
                self.toolbox.skill.memory.add(history)
                created.append(self.toolbox.skill.memory.events[-1])
        return created


def _workbench_llm_client(mode: str) -> OpenAICompatibleClient:
    provider = provider_env_key(mode)
    timeout = float(
        os.getenv(
            f"EVALHARNESS_AGENT_{provider}_TIMEOUT",
            os.getenv(f"EVALHARNESS_{provider}_TIMEOUT", os.getenv("EVALHARNESS_AGENT_TIMEOUT", "120")),
        )
    )
    return build_llm_client(mode, timeout=timeout)


def _system_prompt(soul: str) -> str:
    base = (
        "你是安装了 assist-everything-betterandbetter-skill 的 agent。"
        "记忆工具已经完成提取、更新、删除和检索。"
        "必须尊重 tool_draft 和 memory_snapshot，不要虚构或使用 deleted/superseded 记忆。"
        "只有 memory_actions 非空时，才允许说“记住了”“记下了”“已保存”等保存承诺；"
        "如果 memory_actions 为空，绝不能声称本轮写入了记忆。"
        "注意主体归属：如果上下文是在给女朋友选礼物，预算通常是用户的送礼预算，"
        "颜色/喜好通常归属女朋友，不要误写成用户本人喜欢。"
        "默认用中文短答，像正常人说话；不要主动解释记忆工具和推理链路。"
    )
    if not soul:
        return base
    return f"{base}\n\n{soul}"


def _memory_actions_from_call(call: Any) -> list[dict[str, Any]]:
    output = getattr(call, "output", {}) or {}
    return output.get("memory_actions", [])


def _has_reset_action(call: Any) -> bool:
    return any(action.get("action") == "reset" for action in _memory_actions_from_call(call))


def _authoritative_memory_operation(call: Any) -> bool:
    if getattr(call, "name", "") in {"reset_memory", "show_memory", "manage_memory"}:
        return True
    return any(action.get("action") in {"reset", "delete", "downgrade", "archive"} for action in _memory_actions_from_call(call))


def _claims_memory_saved(text: str) -> bool:
    return any(token in text for token in ["记住了", "记下了", "记好了", "已保存", "我记住", "我记下"])


class ExternalCommandAgent:
    """Optional adapter: run a real LLM/agent command with trace JSON over stdin."""

    def __init__(self, command: str | None = None) -> None:
        self.command = command or os.getenv("EVALHARNESS_AGENT_CMD", "")
        if not self.command:
            raise ValueError("EVALHARNESS_AGENT_CMD is not configured")

    def reply(self, payload: dict[str, Any]) -> dict[str, Any]:
        completed = subprocess.run(
            self.command,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            shell=True,
            check=True,
            capture_output=True,
        )
        return json.loads(completed.stdout)


def _notes_from_response(response: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    actions = response.get("memory_actions", [])
    if any(action.get("action") == "add" for action in actions):
        notes.append("本轮从自然语言中提取并保存了通用记忆。")
    if any(action.get("action") in {"downgrade", "archive", "delete"} for action in actions):
        notes.append("本轮处理了记忆更新、淘汰或删除。")
    if response.get("applied_memories"):
        notes.append("本轮检索并应用了 active 记忆。")
    return notes
