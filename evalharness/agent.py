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
from assist_everything_betterandbetter_skill.runtime_config import load_runtime_config
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
        retrieval_intent_classifier = self._retrieval_intent_classifier() if toolbox is None else None
        self.toolbox = toolbox or MemoryToolbox(
            memory_dir=memory_dir,
            persist=persist_memory,
            mem0_config=mem0_config,
            memory_enabled=memory_enabled,
            memory_backend=memory_backend,
            semantic_extractor=semantic_extractor,
            retrieval_intent_classifier=retrieval_intent_classifier,
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
                response.memory_actions,
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
        current_memory_actions: list[dict[str, Any]] | None = None,
    ) -> str:
        if not self._use_llm():
            if self.require_llm:
                raise RuntimeError("真实 LLM provider 未配置或未启用，Workbench 不允许使用本地业务回答。")
            return "真实 LLM provider 未配置或未启用，无法生成业务回答。"
        provider = self._provider()
        client = self.llm_client or _workbench_llm_client(provider)
        snapshot = self.toolbox.snapshot()
        memory_context = _rewrite_memory_context(snapshot, memory_pack or {}, applied_memories)
        memory_actions = _compact_memory_actions(current_memory_actions or self.toolbox.skill.memory.events[-8:], snapshot=snapshot)
        memory_write_result = _memory_write_result(user_text, memory_actions)
        selected_gift = _selected_gift_from_actions(memory_actions) or _gift_selection_phrase(user_text)
        suppression_context = _suppression_context_from_actions(memory_actions, snapshot=snapshot, user_text=user_text, context=context)
        def finish(text: str) -> str:
            return _enforce_memory_write_consistency(_finalize_user_visible_output(text), memory_write_result)

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
                        "applied_memory_ids": applied_memories,
                        "memory_context": memory_context,
                        "memory_actions": memory_actions,
                        "memory_write_result": memory_write_result,
                        "suppression_context": suppression_context,
                        "response_directives": _response_directives(
                            user_text,
                            memory_context,
                            selected_gift=selected_gift,
                            suppression_context=suppression_context,
                        ),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            rewritten = _sanitize_llm_output(client.chat(messages, temperature=0.3))
            rewritten = _remove_trailing_generic_question(rewritten)
            if _llm_response_is_usable(
                rewritten,
                user_text=user_text,
                selected_gift=selected_gift,
                suppression_context=suppression_context,
                memory_context=memory_context,
                conversation_context=context,
            ):
                return finish(rewritten)
            retry_messages = [
                messages[0],
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "stage": stage,
                            "user_message": user_text,
                            "conversation_context": context,
                            "applied_memory_ids": applied_memories,
                            "memory_context": memory_context,
                            "memory_actions": memory_actions,
                            "memory_write_result": memory_write_result,
                            "suppression_context": suppression_context,
                            "response_directives": _response_directives(
                                user_text,
                                memory_context,
                                selected_gift=selected_gift,
                                suppression_context=suppression_context,
                            ),
                            "previous_rewrite": rewritten,
                            "instruction": (
                                "上一次回复不可用。必须基于 user_message、conversation_context 和 memory_context 直接回答用户；"
                                "如果用户是在确认、纠正或选择候选项，先承认并执行这个意图；"
                                "如果 memory_actions 已经写入 selected 决策，必须确认该已选礼物，不能继续推荐其他方向；"
                                "如果 suppression_context 标出本轮删除/降级的记忆语义，后续方案不得继续把这些语义作为推荐依据或主方案核心；"
                                "任务请求必须给出完整可用结果，不能只追问，不能说“这个草案/上面的方案”却不展示主体；"
                                "如果用户要求安排行程/路线，必须给一个默认主路线和时间安排，不能只给多个方向让用户选。"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            rewritten = _sanitize_llm_output(client.chat(retry_messages, temperature=0.1))
            rewritten = _remove_trailing_generic_question(rewritten)
            if _llm_response_is_usable(
                rewritten,
                user_text=user_text,
                selected_gift=selected_gift,
                suppression_context=suppression_context,
                memory_context=memory_context,
                conversation_context=context,
            ):
                return finish(rewritten)
            blocked_terms = (
                _suppression_blocked_terms(suppression_context)
                + _memory_context_blocked_terms(memory_context)
                + _conversation_context_blocked_terms(user_text, context)
            )
            if not blocked_terms and not _has_unresolved_choice(rewritten):
                return finish(rewritten) or "真实 LLM 返回空内容，已拒绝回退到本地业务回答。"
            final_retry_messages = [
                messages[0],
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "stage": stage,
                            "user_message": user_text,
                            "conversation_context": context,
                            "applied_memory_ids": applied_memories,
                            "memory_context": memory_context,
                            "memory_actions": memory_actions,
                            "memory_write_result": memory_write_result,
                            "suppression_context": suppression_context,
                            "blocked_terms": blocked_terms,
                            "response_directives": _response_directives(
                                user_text,
                                memory_context,
                                selected_gift=selected_gift,
                                suppression_context=suppression_context,
                            ),
                            "previous_rewrite": rewritten,
                            "instruction": (
                                "上一次回复仍不可用。请重新给出最终答案，只输出可直接交付给用户的答案。"
                                "必须遵守 blocked_terms：这些词和对应语义不能作为方案、活动、推荐理由或互动项目出现。"
                                "如果用户要求安排行程/路线，直接选择一个默认主路线并给出时间表，不要让用户再选方向。"
                                "如果用户要求删除记忆，先用一句话确认删除状态，再继续完成同一句里的任务。"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            rewritten = _sanitize_llm_output(client.chat(final_retry_messages, temperature=0.0))
            rewritten = _remove_trailing_generic_question(rewritten)
            if _llm_response_is_usable(
                rewritten,
                user_text=user_text,
                selected_gift=selected_gift,
                suppression_context=suppression_context,
                memory_context=memory_context,
                conversation_context=context,
            ):
                return finish(rewritten)
            for _ in range(2):
                violation_terms = _blocked_terms_used_in_answer(rewritten, blocked_terms)
                if (
                    not violation_terms
                    and not _has_unresolved_choice(rewritten)
                    and _llm_response_is_usable(
                        rewritten,
                        user_text=user_text,
                        selected_gift=selected_gift,
                        suppression_context=suppression_context,
                        memory_context=memory_context,
                        conversation_context=context,
                    )
                ):
                    break
                repair_messages = [
                    messages[0],
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "stage": stage,
                                "user_message": user_text,
                                "conversation_context": context,
                                "memory_context": memory_context,
                                "memory_actions": memory_actions,
                                "memory_write_result": memory_write_result,
                                "suppression_context": suppression_context,
                                "blocked_terms": blocked_terms,
                                "previous_rewrite": rewritten,
                                "violations": violation_terms,
                                "instruction": (
                                    "上一次回复违反了 blocked_terms 或仍让用户选方向。"
                                    "请完全重写，输出最终答案。不能出现 violations 中的词，"
                                    "也不能用近义活动替代被删除的语义。"
                                    "如果是路线/行程请求，给一个默认主路线和时间表。"
                                ),
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
                rewritten = _sanitize_llm_output(client.chat(repair_messages, temperature=0.0))
                rewritten = _remove_trailing_generic_question(rewritten)
                if _llm_response_is_usable(
                    rewritten,
                    user_text=user_text,
                    selected_gift=selected_gift,
                    suppression_context=suppression_context,
                    memory_context=memory_context,
                    conversation_context=context,
                ):
                    return finish(rewritten)
            return finish(rewritten) or "真实 LLM 返回空内容，已拒绝回退到本地业务回答。"
        except Exception as exc:
            if self.require_llm:
                raise RuntimeError(f"真实 LLM 调用失败，Workbench 不允许回退到本地业务回答（provider={provider}）：{exc}") from exc
            return f"真实 LLM 调用失败，已拒绝回退到本地业务回答：{exc}"

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
        extractor_enabled = bool(load_runtime_config()["memory"].get("llm_extractor", True))
        env_disabled = os.getenv("ASSIST_MEMORY_LLM_EXTRACTOR", "1").strip().lower() in {"0", "false", "off", "no"}
        if not extractor_enabled or env_disabled:
            return None
        if not self._use_llm():
            return None
        if self.llm_client is not None and not hasattr(self.llm_client, "json_chat"):
            return None
        return LLMSemanticMemoryExtractor(lambda: self.llm_client or _workbench_llm_client(self._provider()))

    def _retrieval_intent_classifier(self) -> "LLMRetrievalIntentClassifier | None":
        extractor_enabled = bool(load_runtime_config()["memory"].get("llm_extractor", True))
        env_disabled = os.getenv("ASSIST_MEMORY_LLM_EXTRACTOR", "1").strip().lower() in {"0", "false", "off", "no"}
        if not extractor_enabled or env_disabled:
            return None
        if not self._use_llm():
            return None
        if self.llm_client is not None and not hasattr(self.llm_client, "json_chat"):
            return None
        return LLMRetrievalIntentClassifier(lambda: self.llm_client or _workbench_llm_client(self._provider()))


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
                    "如果用户在多候选推荐后复述某个候选名称，即使没有“选”字，也应按上下文提取为 decision/selected。"
                    "如果用户在纠正你的交互方式，例如“我说某个选项就代表选好了”，应提取为 workflow 记忆，"
                    "内容写成可复用交互规则，不要把“明白吗/懂吗”当作礼物。"
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


class LLMRetrievalIntentClassifier:
    """LLM-assisted memory recall intent classifier. It never writes memory."""

    def __init__(self, client_factory: Any) -> None:
        self.client_factory = client_factory

    def __call__(self, text: str, context: str, active_items: list[MemoryItem]) -> dict[str, Any]:
        payload = {
            "user_message": text,
            "conversation_context": _compact_context(context, max_chars=1200),
            "active_memory_summary": [
                {
                    "type": item.type,
                    "scope": item.scope,
                    "target": item.target,
                    "predicate": item.predicate,
                    "time_scope": item.validity.get("time_scope"),
                    "content": item.content,
                }
                for item in active_items[:12]
            ],
            "schema": {
                "intent": "gift_history_lookup|memory_lookup|task_request|other",
                "scope": "gift_planning|life_family_travel|study_plan|work_report|research_review|general",
                "target": "optional target such as 女朋友",
                "include_types": ["history", "decision"],
                "include_expired_current_task": False,
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是记忆召回意图分类器，只返回 JSON。"
                    "判断 user_message 是不是在查询、盘点或回顾已保存的记忆。"
                    "如果用户问最近/以前/已经送过、买过、选过哪些礼物，或者问给某个收礼人送过什么礼物，"
                    "intent 必须是 gift_history_lookup，scope=gift_planning，include_types 必须包含 history 和 decision，"
                    "include_expired_current_task=true，因为已选定的礼物可能以 decision/current_task 保存。"
                    "如果只是要新推荐，intent=task_request。不要编造记忆内容。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        data = self.client_factory().json_chat(messages, temperature=0.0)
        return data if isinstance(data, dict) else {}


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
            os.getenv(
                "EVALHARNESS_AGENT_MINIMAX_TIMEOUT",
                os.getenv(
                    "EVALHARNESS_MINIMAX_TIMEOUT",
                    os.getenv(
                        "EVALHARNESS_AGENT_MIMO_TIMEOUT",
                        os.getenv("EVALHARNESS_MIMO_TIMEOUT", "120"),
                    ),
                ),
            ),
        )
    )
    return llm_client_from_env(provider, timeout=timeout)


def _system_prompt() -> str:
    base = (
        "你是安装了 assist-everything-betterandbetter-skill 的 agent。"
        "记忆工具已经完成提取、更新、删除和检索。"
        "memory_actions 和 memory_write_result 是记忆状态的唯一事实源；只有 committed=true 或存在成功 add/update/downgrade/delete/reset/dedupe 时，才允许说已记住、已保存或已更新。"
        "如果用户要求写入/更新记忆但 memory_write_result.committed=false，必须友好说明没有成功写入，并提示用户补充要保存的具体内容；不要口头承诺已经记住。"
        "必须尊重 memory_context，不要虚构或使用 deleted/superseded 记忆。"
        "如果用户本轮删除或否定了某条偏好，该删除/否定优先级高于 conversation_context 中更早的说法，后续回答不得继续使用被删除偏好。"
        "如果用户显式要求删除/忘掉某条记忆，回复开头要用一句话确认删除或说明此前已删除，然后继续完成用户同一句里的任务。"
        "如果 payload 里有 suppression_context，里面的 do_not_assume 是本轮刚删除/降级的语义，优先级高于历史对话和通用经验；"
        "不得把这些语义作为推荐理由、主方案核心或默认假设。"
        "如果被删除的是颜色偏好，后续不要推荐该颜色、不要把该颜色作为搭配理由；即使历史已选礼物名称含该颜色，也只称“已选礼物/已选方巾”，不要复述该颜色词。"
        "只能默认使用 memory_context.apply_now 里的记忆；memory_context.confirm_first 只能作为需要确认的提示，不能直接当作已生效约束。"
        "如果 confirm_first 里有上次同类任务的预算、临时约束或上下文，不要空问用户有没有这些信息；应该说“我看到上次是...，如果这次仍沿用，我先按这个给方案”，然后直接给可执行结果。"
        "你的回复不能为空；对任务请求必须直接给完整可用结果，不能只说需要更多材料。"
        "信息不足时也要先基于合理假设给 2-4 个可执行候选，并明确默认假设；不要把第一反应变成追问。"
        "处理偏好时要区分广义偏好和窄域/条件偏好：窄域条件优先于广义偏好。"
        "例如“喜欢紫色；如果是首饰喜欢玫瑰金”表示首饰优先玫瑰金，不要为了紫色强行选择紫色首饰。"
        "如果用户纠正“首饰不需要硬凹紫色”，后续首饰推荐不得再叠加紫色。"
        "如果用户复述或点名 conversation_context 里你刚推荐过的候选项，默认视为用户已经选定该候选，不要继续发散推荐。"
        "送礼场景中，memory_context.gift_selected_exclusions 是已经选定或确认过的礼物。"
        "如果用户后续说“给我一个礼物推荐”“再给一个推荐”“换个方向/品类”，除非明确要求购买渠道、包装或继续推进该已选礼物，"
        "必须把这些已选礼物及其同品类作为排除项，不要重复推荐。"
        "如果用户纠正你，优先承认纠正并按纠正后的约束继续，不要重复已经被否定的方向。"
        "送礼场景中，如果用户已经选过其他品类、纠正过推荐方向，或正在要求不重复的新方向，"
        "不要把香氛/香水/香薰/扩香/蜡烛作为默认兜底推荐，除非用户明确要求香味类礼物。"
        "不要使用“这个草案”“上面的方案”这类空指代，除非你已经把草案主体写出来。"
        "不要在已交付方案末尾追加“需要我帮你查”“选A还是B”这类泛泛追问。"
        "如果用户要求安排行程/路线，必须直接选择一个默认主路线并给出时间安排；备选可以简短列出，但不能把主任务变成“哪个方向”的选择题。"
        "默认用中文短答，像正常人说话；不要主动解释记忆工具、记忆变更和推理链路。"
        "不要预设用户或收礼人的年龄、性别气质、身份标签、消费风格；只能使用用户已给出的关系和偏好。"
        "不要使用 Markdown 加粗符号 **，不要用过度模板化的 AI 口吻。"
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


def _finalize_user_visible_output(text: str) -> str:
    cleaned = _strip_memory_tool_section(str(text or ""))
    cleaned = _strip_generated_markdown_bold(cleaned)
    return cleaned.strip()


def _strip_memory_tool_section(text: str) -> str:
    lines = str(text or "").splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("记忆处理"):
            skipping = True
            continue
        if skipping:
            if not stripped or stripped.startswith("- ") or "记忆" in stripped:
                continue
            skipping = False
        kept.append(line)
    return "\n".join(kept)


def _strip_generated_markdown_bold(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        if "`" in line or "Markdown" in line or "markdown" in line:
            lines.append(line)
        else:
            lines.append(line.replace("**", ""))
    return "\n".join(lines)


def _remove_trailing_generic_question(text: str) -> str:
    lines = str(text or "").rstrip().splitlines()
    optional_terms = [
        "需要我",
        "要我",
        "要不要",
        "需要推荐",
        "帮你查",
        "要记住",
        "告诉我",
        "补充、修改或删除",
        "选A还是B",
        "哪个方向",
        "确认后给",
        "具体交通",
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
            or _contains_any(tail, ["想选下午A还是B", "选A还是B", "A还是B", "哪个方向", "确认后给具体"])
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
        "gift_selected_exclusions": _gift_selected_exclusions(snapshot),
    }


def _response_directives(
    user_text: str,
    memory_context: dict[str, Any],
    *,
    selected_gift: str = "",
    suppression_context: dict[str, Any] | None = None,
) -> list[str]:
    directives: list[str] = []
    if _contains_any(user_text, ["删除", "删掉", "忘掉", "不再记"]):
        directives.append("用户显式要求删除/忘掉记忆；如果本轮还有任务，先用一句话确认删除状态，再直接完成任务。")
    if _contains_any(user_text, ["安排", "行程", "路线", "半日游"]):
        directives.append("行程/路线任务必须直接给一个默认主路线和时间安排；备选只可简短列出，不能追问用户选哪个方向。")
    if (suppression_context or {}).get("items") and _contains_any(user_text, ["安排", "行程", "路线", "半日游", "然后"]):
        directives.append("这是删除/降级后的继续任务；必须选择保守主路线，不要把已删除语义换成近义活动重新推荐。")
    if _memory_context_contains(memory_context, "网红"):
        directives.append("当前约束包含避开网红点；不要推荐热门游客街区、网红打卡街区或以打卡为核心的点位，尤其避开夫子庙、老门东、新街口这类高人流街区。")
    selection = selected_gift or _gift_selection_phrase(user_text)
    if selection:
        directives.append(f"用户正在确认候选礼物“{selection}”；必须确认已选定这个礼物，不要改推其他礼物。")
    if _is_gift_new_recommendation_request(user_text) and memory_context.get("gift_selected_exclusions"):
        directives.append(
            "用户正在要新的礼物推荐；必须避开 memory_context.gift_selected_exclusions 中已选礼物及同品类，直接给新候选，不要总结当前记忆或说无需推荐。"
        )
        if not _contains_any(user_text, ["香氛", "香水", "香薰", "扩香", "蜡烛", "香味"]):
            directives.append("本轮不要推荐香氛、香水、香薰、扩香或蜡烛类礼物。")
    if _is_gift_history_lookup(user_text):
        directives.append(
            "用户在盘点最近送过/选过的礼物；必须同时使用 memory_context.apply_now 中的 history/previously_given 和 decision/selected，"
            "以及 memory_context.gift_selected_exclusions 中的已选礼物。不要只回答以前送过的历史项。"
        )
    confirm_budget = _confirm_first_budget(memory_context)
    if _is_gift_task_request(user_text) and confirm_budget:
        directives.append(
            f"confirm_first 里有上次同类送礼预算：{confirm_budget}。不要再问“有预算吗”；请用“如果这次还沿用这个预算”来确认，并直接按该预算给一个推荐。"
        )
    confirm_constraints = _confirm_first_constraints(memory_context)
    if confirm_constraints:
        directives.append(
            "confirm_first 里有上次同类任务的临时约束："
            + "；".join(confirm_constraints[:3])
            + "。不要把它当成永久事实；请用“如果这次仍沿用”来轻确认，并先按这些约束给可执行结果。"
        )
        if any(_contains_any(item, ["不要首饰", "非首饰", "不考虑首饰"]) for item in confirm_constraints):
            directives.append("如果这次仍沿用非首饰约束，本轮不得推荐首饰、耳钉、耳环、项链、手链或戒指。")
    return directives


def _selected_gift_from_actions(actions: list[dict[str, Any]]) -> str:
    for action in reversed(actions or []):
        if action.get("action") not in {"add", "dedupe", "update"}:
            continue
        detail = str(action.get("detail") or "")
        if "已选定" not in detail and "selected" not in str(action.get("predicate") or ""):
            continue
        match = re.search(r"已选定为(.+?)(?:。|；|$)", detail)
        selected = match.group(1).strip() if match else detail
        selected = re.sub(r"^本次给.*?的礼物", "", selected).strip(" ：:，,。")
        selected = selected.removeprefix("已选定为").strip(" ：:，,。")
        if selected and selected not in {"这个", "这个礼物", "它", "这款"}:
            return selected
    return ""


def _memory_context_contains(memory_context: dict[str, Any], term: str) -> bool:
    for key in ["apply_now", "confirm_first"]:
        for item in memory_context.get(key, []) or []:
            if isinstance(item, dict) and term in str(item.get("content") or ""):
                return True
    return False


def _confirm_first_budget(memory_context: dict[str, Any]) -> str:
    for item in memory_context.get("confirm_first", []) or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        if item.get("type") == "constraint" and "预算" in content:
            return content
    return ""


def _confirm_first_constraints(memory_context: dict[str, Any]) -> list[str]:
    constraints: list[str] = []
    for item in memory_context.get("confirm_first", []) or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        if item.get("type") == "constraint" and "预算" not in content:
            constraints.append(content)
        elif item.get("time_scope") == "current_task" and content and "预算" not in content:
            constraints.append(content)
    return constraints


def _memory_context_blocked_terms(memory_context: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    if _memory_context_contains(memory_context, "网红"):
        terms.extend(["夫子庙", "老门东", "新街口", "网红", "打卡街区", "热门游客街区"])
    if _memory_context_has_non_jewelry_constraint(memory_context):
        terms.extend(["首饰", "耳钉", "耳环", "项链", "手链", "戒指", "吊坠"])
    return terms


def _memory_context_has_non_jewelry_constraint(memory_context: dict[str, Any]) -> bool:
    for key in ["apply_now", "confirm_first"]:
        for item in memory_context.get(key, []) or []:
            if isinstance(item, dict) and _contains_any(str(item.get("content") or ""), ["不要首饰", "非首饰", "不考虑首饰"]):
                return True
    return False


def _conversation_context_blocked_terms(user_text: str, conversation_context: str) -> list[str]:
    combined = f"{user_text}\n{conversation_context}"
    if (
        _contains_any(combined, ["避开网红", "不喜欢人挤人", "不喜欢人挤人的网红点"])
        and not _contains_any(combined, ["取消避开网红", "不用避开网红", "网红点可以"])
    ):
        return ["夫子庙", "老门东", "新街口", "网红", "打卡街区", "热门游客街区"]
    return []


def _gift_selected_exclusions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    exclusions: list[dict[str, Any]] = []
    for item in snapshot.get("active", []):
        if not isinstance(item, dict):
            continue
        if item.get("scope") != "gift_planning":
            continue
        if item.get("type") != DECISION and item.get("predicate") != "selected":
            continue
        content = str(item.get("content") or "")
        if not content:
            continue
        exclusions.append(
            {
                "content": content,
                "target": item.get("target", ""),
                "category": _gift_category_hint(content),
            }
        )
    return exclusions[:8]


def _gift_category_hint(text: str) -> str:
    if _contains_any(text, ["项链", "手链", "耳钉", "耳环", "戒指", "首饰", "珠宝"]):
        return "首饰"
    if _contains_any(text, ["方巾", "丝巾", "围巾", "披肩"]):
        return "丝巾/围巾"
    if _contains_any(text, ["包", "小包", "手提包", "斜挎", "腋下包"]):
        return "包袋"
    if _contains_any(text, ["拍立得", "相机"]):
        return "影像设备"
    if _contains_any(text, ["香氛", "香水", "蜡烛", "扩香"]):
        return "香氛"
    return ""


def _compact_memory_actions(actions: list[dict[str, Any]], *, snapshot: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    memory_lookup = _memory_lookup(snapshot or {})
    output: list[dict[str, Any]] = []
    for action in actions:
        memory_id = action.get("memory_id")
        memory_item = memory_lookup.get(str(memory_id or ""))
        output.append(
            {
                "action": action.get("action"),
                "detail": action.get("detail") or action.get("memory_id"),
                "memory_id": memory_id,
                "memory_content": memory_item.get("content") if memory_item else action.get("content"),
                "scope": memory_item.get("scope") if memory_item else action.get("scope"),
                "predicate": memory_item.get("predicate") if memory_item else action.get("predicate"),
                "ok": action.get("ok", True),
                "reason": action.get("reason"),
                "error": action.get("error"),
                "extractor": action.get("extractor"),
            }
        )
    return output


def _memory_write_result(user_text: str, memory_actions: list[dict[str, Any]]) -> dict[str, Any]:
    committed_names = {"add", "update", "downgrade", "delete", "reset", "archive", "dedupe"}
    blocking_names = {"reject", "ask", "propose"}
    committed = [
        action
        for action in memory_actions
        if action.get("action") in committed_names and action.get("ok", True) is not False
    ]
    blocked = [action for action in memory_actions if action.get("action") in blocking_names or action.get("ok") is False]
    write_intent = _memory_write_intent(user_text) or bool(committed) or bool(blocked)
    return {
        "write_intent": write_intent,
        "committed": bool(committed),
        "committed_actions": [
            {
                "action": action.get("action"),
                "detail": action.get("detail") or action.get("memory_content") or action.get("memory_id"),
                "memory_id": action.get("memory_id"),
            }
            for action in committed[:6]
        ],
        "blocked_actions": [
            {
                "action": action.get("action"),
                "detail": action.get("detail") or action.get("memory_content") or action.get("memory_id"),
                "reason": action.get("reason") or action.get("error"),
            }
            for action in blocked[:4]
        ],
    }


def _memory_write_intent(text: str) -> bool:
    value = str(text or "")
    return _contains_any(
        value,
        [
            "记住",
            "保存",
            "加入记忆",
            "写入记忆",
            "更新记忆",
            "补上",
            "删除记忆",
            "清除记忆",
            "忘掉",
            "不再记",
            "降级",
            "归档",
        ],
    )


def _enforce_memory_write_consistency(text: str, memory_write_result: dict[str, Any]) -> str:
    output = str(text or "").strip()
    if not memory_write_result.get("write_intent"):
        return output
    if memory_write_result.get("committed"):
        return output
    return _memory_write_failure_message(memory_write_result)


def _claims_memory_committed(text: str) -> bool:
    return _contains_any(
        str(text or ""),
        [
            "已记住",
            "记住了",
            "已经记住",
            "已保存",
            "已经保存",
            "已更新记忆",
            "更新记忆",
            "已更新",
            "已加入记忆",
            "加入记忆",
            "已写入",
            "已经写入",
            "下次会记住",
            "我会记住",
            "后续会按",
        ],
    )


def _memory_write_failure_message(memory_write_result: dict[str, Any]) -> str:
    blocked = memory_write_result.get("blocked_actions") or []
    reason = ""
    if blocked:
        reason = str(blocked[0].get("reason") or "")
    if reason == "temporary_instruction":
        return "我理解你想更新记忆，但这条没有成功写入。请把要保存的内容直接说完整一点，例如：“记住：本次给谁的礼物已选定为哪个具体商品”。"
    if reason == "private_or_sensitive":
        return "这条没有写入记忆，因为可能包含隐私或敏感信息。你可以换成不含敏感细节的版本让我保存。"
    if blocked:
        return "我理解你想更新记忆，但这条没有成功写入。请直接说出要保存的具体内容，我会再写入。"
    return "我理解你想更新记忆，但这轮没有识别出可写入的具体内容，所以还没有保存。请直接说：“记住：……”后面接要保存的内容。"


def _memory_lookup(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for bucket in ["active", "superseded", "archived", "deleted"]:
        for item in snapshot.get(bucket, []) or []:
            if isinstance(item, dict) and item.get("id"):
                lookup[str(item["id"])] = item
    return lookup


def _suppression_context_from_actions(
    actions: list[dict[str, Any]],
    *,
    snapshot: dict[str, Any] | None = None,
    user_text: str = "",
    context: str = "",
) -> dict[str, Any]:
    suppressed: list[dict[str, Any]] = []
    requested_terms = _delete_request_suppression_terms(user_text)
    if requested_terms:
        suppressed.append(
            {
                "action": "delete",
                "memory_id": "",
                "content": user_text,
                "scope": next(iter(_relevant_suppression_scopes(user_text, context)), ""),
                "do_not_assume": requested_terms,
                "duration": "current_task",
            }
        )
    for action in actions or []:
        if action.get("ok", True) is False:
            continue
        if action.get("action") not in {"delete", "downgrade", "archive"}:
            continue
        content = str(action.get("memory_content") or action.get("detail") or "").strip()
        if not content or content in {"user_requested_delete", "user_requested_downgrade", "user_requested_archive"}:
            continue
        terms = _suppression_terms(content)
        if not terms:
            continue
        suppressed.append(
            {
                "action": action.get("action"),
                "memory_id": action.get("memory_id"),
                "content": content,
                "scope": action.get("scope") or "",
                "do_not_assume": terms,
                "duration": "current_task",
            }
        )
    relevant_scopes = _relevant_suppression_scopes(user_text, context)
    for item in (snapshot or {}).get("deleted", []) or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        scope = str(item.get("scope") or "").strip()
        if not content or (relevant_scopes and scope and scope not in relevant_scopes):
            continue
        terms = _suppression_terms(content)
        if not terms:
            continue
        key = (item.get("id"), content)
        if any((existing.get("memory_id"), existing.get("content")) == key for existing in suppressed):
            continue
        suppressed.append(
            {
                "action": "delete",
                "memory_id": item.get("id"),
                "content": content,
                "scope": scope,
                "do_not_assume": terms,
                "duration": "current_task",
            }
        )
    return {"items": suppressed}


def _delete_request_suppression_terms(user_text: str) -> list[str]:
    text = str(user_text or "")
    if not _contains_any(text, ["删除", "删掉", "忘掉", "不再记", "清除"]):
        return []
    before_then = re.split(r"然后|并且|接着|再|重新", text, maxsplit=1)[0]
    terms = _suppression_terms(before_then or text)
    return terms[:24]


def _suppression_blocked_terms(suppression_context: dict[str, Any]) -> list[str]:
    items = suppression_context.get("items") if isinstance(suppression_context, dict) else None
    if not isinstance(items, list):
        return []
    terms: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for term in item.get("do_not_assume", []) or []:
            value = str(term).strip()
            if value and value not in terms:
                terms.append(value)
    return terms[:24]


def _blocked_terms_used_in_answer(text: str, blocked_terms: list[str]) -> list[str]:
    body = _answer_body_without_management_ack(text)
    used: list[str] = []
    for term in blocked_terms or []:
        value = str(term or "").strip()
        if value and _suppressed_term_used_as_plan(body, value) and value not in used:
            used.append(value)
    return used[:12]


def _relevant_suppression_scopes(user_text: str, context: str = "") -> set[str]:
    combined = f"{user_text}\n{context}"
    scopes: set[str] = set()
    if _contains_any(combined, ["礼物", "送", "选", "买", "女朋友", "男朋友", "老公", "老婆", "妈妈", "爸爸"]):
        scopes.add("gift_planning")
    if _contains_any(combined, ["旅行", "行程", "路线", "半日游", "亲子", "家庭", "父亲", "孩子", "小孩", "网红", "步行", "南京", "上海", "杭州", "北京", "景点", "自然", "动物"]):
        scopes.add("life_family_travel")
    if _contains_any(combined, ["学习", "复习", "考试", "例题", "自测"]):
        scopes.add("study_plan")
    if _contains_any(combined, ["老板", "周报", "项目", "同步", "风险"]):
        scopes.add("work_report")
    if _contains_any(combined, ["文献", "综述", "研究", "RAG", "可复现"]):
        scopes.add("research_review")
    return scopes


def _suppression_terms(content: str) -> list[str]:
    text = re.sub(r"[，,。；;：:（）()【】\\[\\]\"'“”‘’]", " ", str(content or ""))
    raw_parts = re.split(r"\s+|和|与|及|、|/|或", text)
    stopwords = {
        "用户",
        "收礼人",
        "女朋友",
        "男朋友",
        "老公",
        "老婆",
        "孩子",
        "父亲",
        "母亲",
        "喜欢",
        "偏好",
        "约束",
        "记忆",
        "礼物",
        "本次",
        "这次",
        "家庭",
        "旅行",
        "出行",
        "需要",
        "下次",
        "确认",
        "同类",
        "场景",
    }
    terms: list[str] = []
    for part in raw_parts:
        token = part.strip()
        if len(token) < 2 or token in stopwords:
            continue
        if token.endswith(("偏好", "约束")) and len(token) > 2:
            token = token[:-2]
        if token and token not in stopwords and token not in terms:
            terms.append(token)
    for marker in ["紫色", "动物", "自然", "网红", "首饰", "少步行", "步行", "玫瑰金"]:
        if marker in content and marker not in terms:
            terms.append(marker)
    if "动物" in terms or "动物" in content:
        for marker in ["动物园", "红山森林", "熊猫", "考拉", "长臂猿", "鸽子", "喂鸽子", "鸟", "湖鸥", "喂湖鸥", "鱼", "喂鱼", "捞鱼", "海洋馆", "水族馆"]:
            if marker not in terms:
                terms.append(marker)
    return terms[:24]


def _memory_actions_from_call(call: Any) -> list[dict[str, Any]]:
    output = getattr(call, "output", {}) or {}
    return output.get("memory_actions", [])


def _has_reset_action(call: Any) -> bool:
    return any(action.get("action") == "reset" for action in _memory_actions_from_call(call))


def _authoritative_memory_operation(call: Any, *, stage: str = "") -> bool:
    if stage in {"reset", "show_memory"}:
        return True
    raw_input = getattr(call, "input", {}) or {}
    input_text = str(raw_input.get("command") or raw_input.get("message") or "")
    if _contains_any(input_text, ["然后", "继续", "再给", "推荐", "安排", "写", "做"]):
        return False
    if _contains_any(input_text, ["展示当前记忆", "展示记忆", "查看记忆", "show memory", "profile", "画像", "snapshot", "快照", "layers", "三层记忆", "隐私报告", "privacy"]):
        return True
    if getattr(call, "name", "") in {"reset_memory", "show_memory"}:
        return True
    if getattr(call, "name", "") == "manage_memory":
        return True
    return any(
        action.get("ok") is False or action.get("action") in {"reset", "delete", "downgrade", "archive"}
        for action in _memory_actions_from_call(call)
    )


def _llm_response_is_usable(
    rewritten: str,
    *,
    user_text: str = "",
    selected_gift: str = "",
    suppression_context: dict[str, Any] | None = None,
    memory_context: dict[str, Any] | None = None,
    conversation_context: str = "",
) -> bool:
    text = str(rewritten or "").strip()
    if not text:
        return False
    if _looks_truncated(text):
        return False
    if _has_unresolved_choice(text):
        return False
    if _violates_suppression_context(text, suppression_context or {}, user_text=user_text):
        return False
    if _violates_memory_context_constraints(
        text,
        memory_context or {},
        user_text=user_text,
        conversation_context=conversation_context,
    ):
        return False
    if _contains_any(text, ["这个草案", "该草案", "上面的方案"]) and not _contains_any(text, ["第 1 天", "第1天", "上午", "下午", "推荐方向", "方案："]):
        return False
    if _is_route_task_request(user_text) and not _is_route_answer_deliverable(text):
        return False
    if _is_gift_new_recommendation_request(user_text) and _contains_any(text, ["无需重新推荐", "已展示过", "不用重新推荐", "剩下唯一待确认"]):
        return False
    if (
        _is_gift_new_recommendation_request(user_text)
        and not _contains_any(user_text, ["香氛", "香水", "香薰", "扩香", "蜡烛", "香味"])
        and _contains_any(text, ["香氛", "香水", "香薰", "扩香", "香薰礼盒", "香氛礼盒", "蜡烛"])
    ):
        return False
    selection = selected_gift or _gift_selection_phrase(user_text)
    if selection:
        required_terms = _selection_required_terms(selection)
        if required_terms and not any(term in text for term in required_terms):
            return False
        if not _contains_any(text, ["选定", "锁定", "定下", "就这个", "已选", "确认"]):
            return False
        if not _contains_any(selection, ["香氛", "香水", "香薰", "扩香", "蜡烛"]) and _contains_any(
            text,
            ["香氛", "香水", "香薰", "扩香", "蜡烛"],
        ):
            return False
    if _is_clarification_only(text):
        return False
    return True


def _violates_memory_context_constraints(
    text: str,
    memory_context: dict[str, Any],
    *,
    user_text: str = "",
    conversation_context: str = "",
) -> bool:
    body = _answer_body_without_management_ack(text)
    if not body:
        return False
    combined_context = f"{user_text}\n{conversation_context}"
    avoid_influencer_spots = _memory_context_contains(memory_context, "网红") or (
        _contains_any(combined_context, ["避开网红", "不喜欢人挤人", "不喜欢人挤人的网红点"])
        and not _contains_any(combined_context, ["取消避开网红", "不用避开网红", "网红点可以"])
    )
    if avoid_influencer_spots and not _contains_any(user_text, ["夫子庙", "老门东", "新街口"]):
        tourist_terms = ["夫子庙", "老门东", "新街口", "网红", "打卡街区", "热门游客"]
        for term in tourist_terms:
            if term in body and not _contains_any(body, [f"避开{term}", f"不去{term}", f"不要{term}", f"不安排{term}", f"非{term}"]):
                return True
    if _memory_context_has_non_jewelry_constraint(memory_context):
        jewelry_terms = ["首饰", "耳钉", "耳环", "项链", "手链", "戒指", "吊坠"]
        for term in jewelry_terms:
            if _suppressed_term_used_as_plan(body, term):
                return True
    return False


def _is_route_task_request(text: str) -> bool:
    return _contains_any(text, ["安排", "行程", "路线", "半日游", "一日游", "1 天", "1天", "2 天", "2天"])


def _is_route_answer_deliverable(text: str) -> bool:
    stripped = str(text or "").strip()
    if len(stripped) < 40:
        return False
    if _contains_any(stripped, ["继续适用", "沿用上", "按刚才", "按前面"]) and not _contains_any(
        stripped,
        ["09:", "9:", "14:", "上午", "下午", "时间", "路线"],
    ):
        return False
    return _contains_any(stripped, ["09:", "9:", "10:", "14:", "15:", "上午", "下午", "时间", "路线", "半日"])


def _violates_suppression_context(text: str, suppression_context: dict[str, Any], *, user_text: str = "") -> bool:
    items = suppression_context.get("items") if isinstance(suppression_context, dict) else None
    if not isinstance(items, list) or not items:
        return False
    body = _answer_body_without_management_ack(text)
    if not body:
        return False
    for item in items:
        if not isinstance(item, dict):
            continue
        terms = [str(term).strip() for term in item.get("do_not_assume", []) if str(term).strip()]
        if not terms:
            continue
        if any(term in user_text and not _contains_any(user_text, ["删除", "删掉", "忘掉", "不再"]) for term in terms):
            continue
        if any(_suppressed_term_used_as_plan(body, term) for term in terms):
            return True
    return False


def _answer_body_without_management_ack(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append(line)
            continue
        if _contains_any(stripped, ["已删除", "删除成功", "记忆已删除", "已降级", "已归档"]):
            continue
        if _contains_any(stripped, ["不再按", "不基于", "不把", "不按"]) and _contains_any(stripped, ["作为依据", "作为推荐理由", "偏好", "主题"]):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _suppressed_term_used_as_plan(body: str, term: str) -> bool:
    if not term or term not in body:
        return False
    plan_markers = [
        "推荐",
        "方案",
        "安排",
        "路线",
        "上午",
        "下午",
        "第",
        "Day",
        "亮点",
        "理由",
        "适合",
        "优先",
        "选择",
        "去",
    ]
    if not _contains_any(body, plan_markers):
        return False
    negated_patterns = [
        f"不要{term}",
        f"不用{term}",
        f"不选{term}",
        f"不碰{term}",
        f"不考虑{term}",
        f"不推荐{term}",
        f"不再推荐{term}",
        f"不安排{term}",
        f"不按{term}",
        f"不再按{term}",
        f"避开{term}",
        f"不去{term}",
        f"排除{term}",
        f"删除{term}",
        f"非{term}",
    ]
    if any(pattern in body for pattern in negated_patterns):
        return False
    return True


def _is_gift_new_recommendation_request(text: str) -> bool:
    value = str(text or "")
    return _contains_any(value, ["礼物推荐", "再给一个推荐", "推荐一个", "一个推荐", "换个方向", "不重复的礼物方向"]) and not _contains_any(
        value,
        ["购买渠道", "链接", "包装", "尺寸", "下单", "贺卡"],
    )


def _is_gift_task_request(text: str) -> bool:
    value = str(text or "")
    if _contains_any(value, ["购买渠道", "链接", "包装", "尺寸", "下单", "贺卡"]):
        return False
    return _contains_any(value, ["礼物", "生日礼物", "送礼", "选礼", "买礼物", "挑礼物"])


def _is_gift_history_lookup(text: str) -> bool:
    value = str(text or "")
    return _contains_any(value, ["礼物", "送", "买", "女朋友", "男朋友", "老公", "老婆", "妈妈", "爸爸"]) and _contains_any(
        value,
        [
            "最近送",
            "最近买",
            "已经送过",
            "已经买过",
            "送过什么",
            "买过什么",
            "送过哪些",
            "买过哪些",
            "送了哪些",
            "买了哪些",
            "礼物有哪些",
            "什么礼物",
            "已选礼物",
            "选过哪些",
        ],
    )


def _gift_selection_phrase(text: str) -> str:
    value = str(text or "").strip(" 。！？!?，,")
    if not value or len(value) > 80:
        return ""
    if _contains_any(value, ["删除", "再给", "推荐", "为什么", "怎么", "不是", "不要", "不用", "明白吗", "懂吗"]):
        return ""
    if re.match(r"^(?:就)?(?:选|买|送|定|下单)", value):
        return value
    if _contains_any(value, ["拍立得", "万事利", "Wensli", "潘多拉", "Pandora", "方巾", "手链", "耳钉", "小包", "包", "音箱", "相册套装"]):
        return value
    return ""


def _selection_required_terms(selection: str) -> list[str]:
    terms = []
    for token in ["拍立得", "相册", "万事利", "Wensli", "潘多拉", "Pandora", "方巾", "手链", "小包", "音箱"]:
        if token in selection:
            terms.append(token)
    return terms or [selection]


def _has_deliverable_marker(text: str) -> bool:
    return _contains_any(
        text,
        ["第 1 天", "第1天", "上午", "下午", "路线", "行程", "方案", "推荐方向", "结论", "计划", "步骤", "执行约束", "落地建议"],
    )


def _is_clarification_only(text: str) -> bool:
    if not _contains_any(text, ["再确认", "需要确认", "我需要", "需要一个关键信息", "有没有", "大概多大", "信息不足", "为了把", "为了更准", "需要知道"]):
        return False
    if len(str(text or "").strip()) < 120:
        return True
    if _contains_any(text, ["我需要再确认", "还需要知道", "需要知道", "有没有"]) and text.endswith(("?", "？", "。")):
        return True
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
    if _contains_any(value, ["我建议选", "优先选", "直接选", "默认推荐"]):
        return False
    if _contains_any(value, ["二选一", "A还是B", "选A还是B", "哪个方向", "确认后给具体"]):
        return True
    return bool(re.search(r"方案\s*[ABCＡＢＣ].*方案\s*[ABCＡＢＣ]", value, flags=re.DOTALL))


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
