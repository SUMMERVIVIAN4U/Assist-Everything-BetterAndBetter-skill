from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from .llm import MimoClient, MimoConfig, mimo_configured
from .schemas import HarnessSession, Message, TurnTrace
from .tools import MemoryToolbox
from assist_everything_betterandbetter_skill.mem0_backend import Mem0Config


class HarnessAgent:
    """Conversation harness that lets an agent use the generic skill runtime."""

    def __init__(
        self,
        name: str = "assist-agent",
        toolbox: MemoryToolbox | None = None,
        llm_mode: str = "auto",
        llm_client: MimoClient | None = None,
        memory_dir: str | None = None,
        persist_memory: bool | None = None,
        mem0_config: Mem0Config | None = None,
        memory_enabled: bool | None = None,
    ) -> None:
        self.name = name
        self.toolbox = toolbox or MemoryToolbox(
            memory_dir=memory_dir,
            persist=persist_memory,
            mem0_config=mem0_config,
            memory_enabled=memory_enabled,
        )
        self.session = HarnessSession()
        self.llm_mode = llm_mode
        self.llm_client = llm_client
        self._context_start_index = 0

    def reply(self, user_text: str, *, stage: str = "chat") -> TurnTrace:
        context = self._recent_context(include_pre_reset=True)
        rewrite_context = self._recent_context(include_pre_reset=False)
        response, call = self.toolbox.process_message(user_text, context=context)
        if not _authoritative_memory_operation(call):
            response.text = self._maybe_llm_rewrite(user_text, stage, response.text, response.applied_memories, rewrite_context)

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
        context: str,
    ) -> str:
        if not self._use_mimo():
            return draft
        client = self.llm_client or _workbench_mimo_client()
        snapshot = self.toolbox.snapshot()
        messages = [
            {
                "role": "system",
                "content": _system_prompt(),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "stage": stage,
                        "user_message": user_text,
                        "conversation_context": context,
                        "tool_draft": draft,
                        "applied_memory_ids": applied_memories,
                        "memory_snapshot": snapshot,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            return client.chat(messages, temperature=0.3).strip()
        except Exception as exc:
            return f"{draft}\n\n远端 LLM 改写超时或失败，已回退到本地工具草稿：{exc}"

    def _recent_context(self, *, include_pre_reset: bool = False) -> str:
        messages = self.session.messages if include_pre_reset else self.session.messages[self._context_start_index :]
        return "\n".join(f"{message.role}: {message.content}" for message in messages[-8:])

    def _use_mimo(self) -> bool:
        if self.llm_mode == "mimo":
            return True
        if self.llm_mode == "local":
            return False
        return mimo_configured()

def _workbench_mimo_client() -> MimoClient:
    config = MimoConfig.from_env()
    timeout = float(os.getenv("EVALHARNESS_AGENT_MIMO_TIMEOUT", os.getenv("EVALHARNESS_MIMO_TIMEOUT", "120")))
    return MimoClient(MimoConfig(config.api_key, config.base_url, config.model, timeout))


def _system_prompt() -> str:
    return (
        "你是安装了 assist-everything-betterandbetter-skill 的 agent。"
        "记忆工具已经完成提取、更新、删除和检索。"
        "必须尊重 tool_draft 和 memory_snapshot，不要虚构或使用 deleted/superseded 记忆。"
        "默认用中文短答，像正常人说话；不要主动解释记忆工具和推理链路。"
    )


def _memory_actions_from_call(call: Any) -> list[dict[str, Any]]:
    output = getattr(call, "output", {}) or {}
    return output.get("memory_actions", [])


def _has_reset_action(call: Any) -> bool:
    return any(action.get("action") == "reset" for action in _memory_actions_from_call(call))


def _authoritative_memory_operation(call: Any) -> bool:
    if getattr(call, "name", "") in {"reset_memory", "show_memory", "manage_memory"}:
        return True
    return any(action.get("action") in {"reset", "delete", "downgrade", "archive"} for action in _memory_actions_from_call(call))


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
