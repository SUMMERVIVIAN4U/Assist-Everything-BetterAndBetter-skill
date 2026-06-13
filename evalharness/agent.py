from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .llm import OpenAICompatibleClient, any_llm_configured, default_configured_provider, llm_client_from_env, llm_configured, normalize_llm_provider
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
        llm_client: OpenAICompatibleClient | None = None,
        memory_dir: str | None = None,
        persist_memory: bool | None = None,
        mem0_config: Mem0Config | None = None,
        memory_enabled: bool | None = None,
        memory_backend: str | None = None,
        require_llm: bool = False,
    ) -> None:
        self.name = name
        self.toolbox = toolbox or MemoryToolbox(
            memory_dir=memory_dir,
            persist=persist_memory,
            mem0_config=mem0_config,
            memory_enabled=memory_enabled,
            memory_backend=memory_backend,
        )
        self.session = HarnessSession()
        self.llm_mode = llm_mode
        self.llm_client = llm_client
        self.require_llm = require_llm
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
        if not self._use_llm():
            if self.require_llm:
                raise RuntimeError("真实 LLM provider 未配置或未启用，Workbench 不允许回退到本地草稿。")
            return draft
        provider = self._provider()
        client = self.llm_client or _workbench_llm_client(provider)
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
            rewritten = client.chat(messages, temperature=0.3).strip()
            if _rewrite_is_usable(user_text, draft, rewritten, context=context):
                return rewritten
            retry_messages = [
                messages[0],
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
                            "previous_rewrite": rewritten,
                            "instruction": (
                                "上一次改写没有保留 tool_draft 的可执行内容。"
                                "如果 user_message 是对 conversation_context 中上一轮任务的补充约束，必须直接更新上一轮任务结果；"
                                "必须直接给用户完整可用方案，保留具体路线/步骤/推荐/结论；"
                                "可以在最后补一个确认问题，但不能只追问，不能说“这个草案”却不展示草案。"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            rewritten = client.chat(retry_messages, temperature=0.1).strip()
            return rewritten if _rewrite_is_usable(user_text, draft, rewritten, context=context) else draft
        except Exception as exc:
            if self.require_llm:
                raise RuntimeError(f"真实 LLM 改写失败，Workbench 不允许回退到本地草稿（provider={provider}）：{exc}") from exc
            return f"{draft}\n\n远端 LLM 改写超时或失败，已回退到本地工具草稿：{exc}"

    def _recent_context(self, *, include_pre_reset: bool = False) -> str:
        messages = self.session.messages if include_pre_reset else self.session.messages[self._context_start_index :]
        return "\n".join(f"{message.role}: {message.content}" for message in messages[-8:])

    def _provider(self) -> str:
        if self.llm_mode == "auto":
            return default_configured_provider()
        return normalize_llm_provider(self.llm_mode)

    def _use_llm(self) -> bool:
        if self.llm_client is not None:
            return True
        if self.llm_mode == "local":
            return False
        if self.llm_mode == "auto":
            return any_llm_configured()
        return llm_configured(self._provider())


def _workbench_llm_client(provider: str) -> OpenAICompatibleClient:
    timeout = float(
        os.getenv(
            "EVALHARNESS_AGENT_LLM_TIMEOUT",
            os.getenv("EVALHARNESS_AGENT_MIMO_TIMEOUT", os.getenv("EVALHARNESS_MIMO_TIMEOUT", "120")),
        )
    )
    return llm_client_from_env(provider, timeout=timeout)


def _system_prompt() -> str:
    base = (
        "你是安装了 assist-everything-betterandbetter-skill 的 agent。"
        "记忆工具已经完成提取、更新、删除和检索。"
        "必须尊重 tool_draft 和 memory_snapshot，不要虚构或使用 deleted/superseded 记忆。"
        "你的回复不能为空；对任务请求必须保留 tool_draft 的可用草案或结果，不能只说需要更多材料。"
        "不要使用“这个草案”“上面的方案”这类空指代，除非你已经把草案主体写出来。"
        "默认用中文短答，像正常人说话；不要主动解释记忆工具、记忆变更和推理链路。"
    )
    persona_dir = Path(__file__).resolve().parent / "persona"
    persona = []
    for name in ["identity.md", "soul.md"]:
        path = persona_dir / name
        if path.exists():
            persona.append(path.read_text(encoding="utf-8").strip())
    return base + ("\n\n" + "\n\n".join(persona) if persona else "")


def _memory_actions_from_call(call: Any) -> list[dict[str, Any]]:
    output = getattr(call, "output", {}) or {}
    return output.get("memory_actions", [])


def _has_reset_action(call: Any) -> bool:
    return any(action.get("action") == "reset" for action in _memory_actions_from_call(call))


def _authoritative_memory_operation(call: Any) -> bool:
    if getattr(call, "name", "") in {"reset_memory", "show_memory", "manage_memory"}:
        return True
    return any(
        action.get("ok") is False or action.get("action") in {"reset", "delete", "downgrade", "archive"}
        for action in _memory_actions_from_call(call)
    )


def _rewrite_is_usable(user_text: str, draft: str, rewritten: str, *, context: str = "") -> bool:
    text = str(rewritten or "").strip()
    if not text:
        return False
    if not _draft_has_deliverable(draft):
        return True
    if _contains_any(text, ["这个草案", "该草案", "上面的方案"]) and len(text) < max(120, len(draft) // 2):
        return False
    if _is_clarification_only(text):
        return False
    if _contains_any(draft, ["亲子行程", "半日亲子路线", "执行约束"]) and _contains_any(text, ["要不要", "需要我", "我可以", "是否要"]):
        if not _contains_any(text, ["第 1 天", "第1天", "上午", "下午", "点位", "执行约束"]):
            return False
    required_markers = _required_delivery_markers(user_text, context, draft)
    if required_markers and not _contains_any(text, required_markers):
        return False
    if len(text) < 80 and len(draft) >= 120 and not _has_deliverable_marker(text):
        return False
    return True


def _draft_has_deliverable(draft: str) -> bool:
    return len(str(draft or "").strip()) >= 80 and _has_deliverable_marker(draft)


def _has_deliverable_marker(text: str) -> bool:
    return _contains_any(
        text,
        ["第 1 天", "第1天", "上午", "下午", "路线", "行程", "方案", "推荐方向", "结论", "计划", "步骤", "执行约束", "落地建议"],
    )


def _required_delivery_markers(user_text: str, context: str, draft: str) -> list[str]:
    signal = f"{context}\n{user_text}\n{draft}"
    if _contains_any(draft, ["亲子行程", "半日亲子路线"]) or (
        _contains_any(signal, ["旅行", "行程", "路线", "亲子", "家庭出行"])
        and _contains_any(draft, ["第 1 天", "半日亲子路线", "执行约束"])
    ):
        return ["第 1 天", "第1天", "上午", "下午", "点位", "执行约束"]
    if _contains_any(draft, ["推荐方向", "预算", "已送", "礼物"]):
        return ["推荐方向", "理由", "预算", "备选", "避开"]
    if _contains_any(draft, ["同步草案", "结论", "风险", "负责人", "下一步"]):
        return ["结论", "风险", "负责人", "下一步", "同步"]
    if _contains_any(draft, ["复习计划", "自测", "例题", "考点"]):
        return ["第 1 天", "第1天", "计划", "自测", "例题", "考点"]
    if _contains_any(draft, ["综述草案", "研究问题", "方法", "数据集", "局限"]):
        return ["方法", "数据集", "局限", "研究问题", "问题"]
    if _contains_any(signal, ["帮我", "安排", "写", "做", "推荐", "规划"]) and _draft_has_deliverable(draft):
        return ["第 1 天", "第1天", "上午", "下午", "路线", "行程", "方案", "推荐方向", "结论", "计划", "步骤", "执行约束", "落地建议"]
    return []


def _is_clarification_only(text: str) -> bool:
    if not _contains_any(text, ["再确认", "需要确认", "我需要", "有没有", "大概多大", "信息不足", "为了把"]):
        return False
    return not _has_deliverable_marker(text)


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


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
    if any(action.get("action") == "add" and action.get("ok", True) is not False for action in actions):
        notes.append("本轮从自然语言中提取并保存了通用记忆。")
    if any(action.get("action") in {"downgrade", "archive", "delete"} for action in actions):
        notes.append("本轮处理了记忆更新、淘汰或删除。")
    if response.get("applied_memories"):
        notes.append("本轮检索并应用了 active 记忆。")
    return notes
