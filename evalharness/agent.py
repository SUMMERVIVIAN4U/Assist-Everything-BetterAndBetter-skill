from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .llm import OpenAICompatibleClient, any_llm_configured, default_configured_provider, llm_client_from_env, llm_configured, normalize_llm_provider
from .schemas import HarnessSession, Message, TurnTrace
from .tools import MemoryToolbox
from assist_everything_betterandbetter_skill.mem0_backend import Mem0Config
from assist_everything_betterandbetter_skill.memory import MemoryItem
from assist_everything_betterandbetter_skill.skill import CONSTRAINT, CONTEXT_FACT, DECISION, HISTORY, PREFERENCE, WORKFLOW


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
        self.session = HarnessSession()
        self.llm_mode = llm_mode
        self.llm_client = llm_client
        self.require_llm = require_llm
        self._context_start_index = 0
        semantic_extractor = self._semantic_extractor() if toolbox is None else None
        self.toolbox = toolbox or MemoryToolbox(
            memory_dir=memory_dir,
            persist=persist_memory,
            mem0_config=mem0_config,
            memory_enabled=memory_enabled,
            memory_backend=memory_backend,
            semantic_extractor=semantic_extractor,
        )

    def reply(self, user_text: str, *, stage: str = "chat") -> TurnTrace:
        context = self._recent_context(include_pre_reset=True)
        rewrite_context = self._recent_context(include_pre_reset=False)
        response, call = self.toolbox.process_message(user_text, context=context)
        if not _authoritative_memory_operation(call, stage=stage):
            response.text = self._maybe_llm_rewrite(
                user_text,
                stage,
                response.text,
                response.applied_memories,
                rewrite_context,
                (response.diagnostics or {}).get("memory_pack", {}),
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
        context: str,
        memory_pack: dict[str, Any] | None = None,
    ) -> str:
        if not self._use_llm():
            if self.require_llm:
                raise RuntimeError("真实 LLM provider 未配置或未启用，Workbench 不允许回退到本地草稿。")
            return draft
        provider = self._provider()
        client = self.llm_client or _workbench_llm_client(provider)
        snapshot = self.toolbox.snapshot()
        memory_context = _rewrite_memory_context(snapshot, memory_pack or {}, applied_memories)
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
                        "memory_context": memory_context,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            rewritten = _sanitize_llm_output(client.chat(messages, temperature=0.3))
            rewritten = _remove_trailing_generic_question(rewritten)
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
                            "memory_context": memory_context,
                            "previous_rewrite": rewritten,
                            "instruction": (
                                "上一次改写没有保留 tool_draft 的可执行内容。"
                                "如果 user_message 是对 conversation_context 中上一轮任务的补充约束，必须直接更新上一轮任务结果；"
                                "必须直接给用户完整可用方案，保留具体路线/步骤/推荐/结论；"
                                "不要在可执行结果后追加泛泛追问，不能只追问，不能说“这个草案”却不展示草案。"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            rewritten = _sanitize_llm_output(client.chat(retry_messages, temperature=0.1))
            rewritten = _remove_trailing_generic_question(rewritten)
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

    def _semantic_extractor(self) -> "LLMSemanticMemoryExtractor | None":
        if os.getenv("ASSIST_MEMORY_LLM_EXTRACTOR", "1").strip().lower() in {"0", "false", "off", "no"}:
            return None
        if not self._use_llm():
            return None
        if self.llm_client is not None and not hasattr(self.llm_client, "json_chat"):
            return None
        return LLMSemanticMemoryExtractor(lambda: self.llm_client or _workbench_llm_client(self._provider()))


class LLMSemanticMemoryExtractor:
    """LLM semantic candidate extractor. It proposes MemoryItem objects; the skill still validates and writes."""

    def __init__(self, client_factory: Any) -> None:
        self.client_factory = client_factory

    def __call__(self, text: str, context: str, scope: str, active_items: list[MemoryItem]) -> list[MemoryItem]:
        payload = {
            "user_message": text,
            "conversation_context": _compact_context(context),
            "scope_hint": scope,
            "active_memories": [
                {
                    "id": item.id,
                    "type": item.type,
                    "content": item.content,
                    "scope": item.scope,
                    "target": item.target,
                    "predicate": item.predicate,
                    "time_scope": item.validity.get("time_scope"),
                }
                for item in active_items[:8]
            ],
            "schema": {
                "memories": [
                    {
                        "type": "preference|constraint|workflow|decision|history|context_fact",
                        "content": "short concrete memory in Chinese",
                        "scope": "gift_planning|life_family_travel|study_plan|work_report|research_review|general",
                        "subject": "optional",
                        "target": "optional recipient/object",
                        "object": "optional",
                        "predicate": "likes|must_avoid|selected|budget_limit|previously_given|...",
                        "time_scope": "current_task|scene_memory|long_term|past",
                        "confidence": 0.0,
                        "evidence": ["quoted user evidence"],
                        "tags": ["short keywords"],
                    }
                ]
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是结构化记忆提取器，只返回 JSON。"
                    "任务：判断 user_message 是否表达了应该保存的记忆候选。"
                    "只提取用户表达或用户明确确认的事实、偏好、约束、历史、决策；不要把 assistant 的推荐当作用户事实，"
                    "除非用户说“就这个/选这个/选拍立得/买了/下单了”等确认语。"
                    "如果只是考虑、询问、泛泛任务请求，不要写 decision。"
                    "current_task 用于本次任务决策或临时约束；long_term 用于稳定偏好；"
                    "scene_memory 用于同类场景下需下次确认的条件；past 用于历史已发生事项。"
                    "如果用户说“选拍立得”，在送礼上下文中应提取为 decision/selected，内容写明具体礼物。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        data = self.client_factory().json_chat(messages, temperature=0.0)
        memories = data.get("memories", [])
        if not isinstance(memories, list):
            return []
        output: list[MemoryItem] = []
        for raw in memories[:5]:
            if isinstance(raw, dict):
                item = _memory_item_from_semantic_payload(raw, text, scope)
                if item:
                    output.append(item)
        return output


def _compact_context(context: str, *, max_chars: int = 2400) -> str:
    lines = [line for line in str(context or "").splitlines() if line.strip()]
    compact = "\n".join(lines[-10:])
    return compact[-max_chars:]


def _memory_item_from_semantic_payload(raw: dict[str, Any], user_text: str, scope_hint: str) -> MemoryItem | None:
    content = str(raw.get("content") or "").strip()
    if not content:
        return None
    memory_type = str(raw.get("type") or "").strip()
    allowed_types = {PREFERENCE, CONSTRAINT, WORKFLOW, DECISION, HISTORY, CONTEXT_FACT}
    if memory_type not in allowed_types:
        memory_type = CONTEXT_FACT
    scope = str(raw.get("scope") or scope_hint).strip() or scope_hint
    allowed_scopes = {"gift_planning", "life_family_travel", "study_plan", "work_report", "research_review", "general"}
    if scope not in allowed_scopes:
        scope = scope_hint
    confidence = _safe_float(raw.get("confidence"), 0.75)
    evidence = raw.get("evidence")
    tags = raw.get("tags")
    time_scope = str(raw.get("time_scope") or "").strip()
    if time_scope not in {"current_task", "scene_memory", "long_term", "past"}:
        time_scope = "long_term"
    return MemoryItem(
        memory_type,
        content,
        scope=scope,
        subject=str(raw.get("subject") or "").strip(),
        target=str(raw.get("target") or "").strip(),
        object=str(raw.get("object") or "").strip(),
        predicate=str(raw.get("predicate") or "").strip(),
        source="llm_semantic_extractor",
        confidence=max(0.0, min(confidence, 1.0)),
        evidence=[str(item).strip() for item in evidence if str(item).strip()] if isinstance(evidence, list) else [user_text],
        applies_when=[scope],
        tags=[str(item).strip() for item in tags if str(item).strip()] if isinstance(tags, list) else [],
        validity={"time_scope": time_scope},
    )


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
        "必须尊重 tool_draft 和 memory_context，不要虚构或使用 deleted/superseded 记忆。"
        "只能默认使用 memory_context.apply_now 里的记忆；memory_context.confirm_first 只能作为需要确认的提示，不能直接当作已生效约束。"
        "你的回复不能为空；对任务请求必须保留 tool_draft 的可用草案或结果，不能只说需要更多材料。"
        "不要使用“这个草案”“上面的方案”这类空指代，除非你已经把草案主体写出来。"
        "不要在已交付方案末尾追加“需要我帮你查”“选A还是B”这类泛泛追问。"
        "默认用中文短答，像正常人说话；不要主动解释记忆工具、记忆变更和推理链路。"
    )
    persona_dir = Path(__file__).resolve().parent / "persona"
    persona = []
    for name in ["identity.md", "soul.md"]:
        path = persona_dir / name
        if path.exists():
            persona.append(path.read_text(encoding="utf-8").strip())
    return base + ("\n\n" + "\n\n".join(persona) if persona else "")


def _sanitize_llm_output(text: str) -> str:
    cleaned = re.sub(r"<think\b[^>]*>.*?</think>", "", str(text or ""), flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"^\s*(?:思考|推理)\s*[:：].*?(?=\n\n|\n[^。\n]{0,20}[:：]|\Z)", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


def _remove_trailing_generic_question(text: str) -> str:
    lines = str(text or "").rstrip().splitlines()
    optional_terms = [
        "需要我",
        "要我",
        "要不要",
        "需要推荐",
        "帮你查",
        "补充、修改或删除",
        "选A还是B",
        "两个都要",
        "具体餐厅",
        "酒店",
        "门票",
        "倾向",
        "侧重",
    ]
    while lines:
        tail = lines[-1].strip()
        if tail and (
            (tail.endswith(("?", "？")) and _contains_any(tail, optional_terms))
            or _contains_any(tail, ["想选下午A还是B", "选A还是B", "A还是B"])
        ):
            lines.pop()
            continue
        break
    return "\n".join(lines).strip()


def _rewrite_memory_context(snapshot: dict[str, Any], memory_pack: dict[str, Any], applied_memories: list[str]) -> dict[str, Any]:
    return {
        "version": snapshot.get("version", "M0"),
        "active_count": len(snapshot.get("active", [])),
        "applied_memory_ids": applied_memories,
        "apply_now": memory_pack.get("apply_now", []),
        "confirm_first": memory_pack.get("confirm_first", []),
        "suppressed": memory_pack.get("suppressed", []),
    }


def _memory_actions_from_call(call: Any) -> list[dict[str, Any]]:
    output = getattr(call, "output", {}) or {}
    return output.get("memory_actions", [])


def _has_reset_action(call: Any) -> bool:
    return any(action.get("action") == "reset" for action in _memory_actions_from_call(call))


def _authoritative_memory_operation(call: Any, *, stage: str = "") -> bool:
    if stage in {"reset", "show_memory"}:
        return True
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
    if _looks_truncated(text):
        return False
    if _has_unresolved_choice(text):
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
    if not _contains_any(text, ["再确认", "需要确认", "我需要", "需要一个关键信息", "有没有", "大概多大", "信息不足", "为了把"]):
        return False
    return not _has_deliverable_marker(text)


def _looks_truncated(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    if value[-1] in "，、：；（(":
        return True
    dangling = ("就", "并", "和", "或", "以及", "然后", "优先", "建议", "可以", "如果", "但", "同时", "例如")
    return value.endswith(dangling)


def _has_unresolved_choice(text: str) -> bool:
    value = str(text or "")
    return _contains_any(value, ["二选一", "A还是B", "选A还是B"]) and not _contains_any(value, ["我建议选", "优先选", "直接选"])


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
