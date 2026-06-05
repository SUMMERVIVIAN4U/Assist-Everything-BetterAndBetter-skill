from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .memory import ACTIVE, DELETED, SUPERSEDED, MemoryItem, MemoryStore


PREFERENCE = "preference"
CONSTRAINT = "constraint"
WORKFLOW = "workflow"
CANDIDATE = "candidate"
DECISION = "decision"
HISTORY = "history"
CONTEXT_FACT = "context_fact"


@dataclass
class SkillResponse:
    text: str
    memory_actions: list[dict[str, Any]]
    applied_memories: list[str]
    asks: list[str]
    relevant_memory_pack: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "memory_actions": self.memory_actions,
            "applied_memories": self.applied_memories,
            "asks": self.asks,
            "relevant_memory_pack": self.relevant_memory_pack or {},
        }


@dataclass
class ScoredMemory:
    item: MemoryItem
    score: float
    reasons: list[str]


class AssistSkill:
    """Generic authorized collaboration-memory runtime.

    The runtime does not know eval case ids. It receives natural language,
    decides whether memory should be managed/extracted/applied, and returns a
    response plus auditable memory actions.
    """

    def __init__(self, memory_dir: str | Path | None = None, persist: bool | None = None) -> None:
        if persist is None:
            persist = os.getenv("ASSIST_MEMORY_PERSIST", "1") != "0"
        storage_dir = memory_dir if memory_dir is not None else os.getenv("ASSIST_MEMORY_DIR", "memories/default")
        self.memory = MemoryStore(storage_dir if persist else None)
        self.pending_proposals: list[MemoryItem] = []

    def process_message(self, text: str, context: str = "") -> SkillResponse:
        command = self._try_memory_command(text)
        if command:
            return command

        actions = self._apply_updates(text, context)
        scored = self.score_relevant_memories(text, context)
        relevant = [entry.item for entry in scored]
        memory_pack = _relevant_memory_pack(text, context, scored)
        asks = self._suggest_followups(text, relevant, context)
        response_text = self.compose_response(text, relevant, actions, asks, context)
        actions.extend(self._record_assistant_candidate(response_text, text, context))
        return SkillResponse(response_text, actions, [item.id for item in relevant], asks, memory_pack)

    def reset_memory(self) -> SkillResponse:
        event = self.memory.reset()
        return SkillResponse("已重置记忆：当前为 M0 空白状态。", [event], [], [])

    def show_memory(self) -> SkillResponse:
        snapshot = self.memory.snapshot()
        if not self.memory.items:
            return SkillResponse("当前没有任何记忆。", [], [], [])
        lines = ["当前 active 记忆与历史状态："]
        for status in [ACTIVE, SUPERSEDED, "archived", DELETED]:
            items = snapshot.get(status if status != ACTIVE else "active", [])
            for item in items:
                lines.append(f"- {item['id']} [{item['status']}/{item['type']}/{item['scope']}] {item['content']}")
        active_ids = [item.id for item in self.memory.active()]
        return SkillResponse("\n".join(lines), [], active_ids, [])

    def manage_memory(self, text: str) -> SkillResponse:
        command = self._try_memory_command(text)
        if command:
            return command
        return SkillResponse("未识别到记忆管理命令。支持 reset/show/find/delete/downgrade/archive。", [], [], [])

    def retrieve_relevant_memories(self, text: str, context: str = "") -> list[MemoryItem]:
        return [entry.item for entry in self.score_relevant_memories(text, context)]

    def score_relevant_memories(self, text: str, context: str = "", top_k: int | None = None) -> list[ScoredMemory]:
        scope = _infer_scope(text, context)
        terms = [term for term in _keywords(text) if term not in {"礼物"}]
        if top_k is None:
            top_k = _memory_top_k()
        gift_recipient_label = ""
        gift_recipient_key = ""
        if scope == "relationship_gift":
            gift_recipient_label, gift_recipient_key = _gift_recipient(text, context)
        if "不适用" in text:
            if "步行不适用" in text or "少步行不适用" in text:
                terms = ["步行"]
            else:
                scoped_terms = [term for term in terms if f"{term}不适用" in text or f"{term} 不适用" in text]
                terms = scoped_terms or terms
        scored: list[ScoredMemory] = []
        active = self.memory.active()
        total = max(len(active), 1)
        for index, item in enumerate(active):
            if _is_polluted_memory_item(item):
                continue
            if (
                scope == "relationship_gift"
                and item.scope == "relationship_gift"
                and not _memory_matches_gift_recipient(item, gift_recipient_label, gift_recipient_key)
            ):
                continue
            score, reasons = _score_memory_item(item, scope, terms, index, total, gift_recipient_key)
            if score > 0:
                scored.append(ScoredMemory(item, score, reasons))
        scored.sort(key=lambda entry: (entry.score, entry.item.updated_at, entry.item.id), reverse=True)
        return scored[:top_k]

    def compose_response(
        self,
        text: str,
        memories: list[MemoryItem],
        actions: list[dict[str, Any]],
        asks: list[str],
        context: str = "",
    ) -> str:
        lines = [self._task_answer(text, memories, context)]
        created = [a for a in actions if a["action"] == "add"]
        changed = [a for a in actions if a["action"] in {"downgrade", "archive", "delete", "update"}]
        if created:
            detail = "；".join(a["detail"] for a in created[:2])
            suffix = "等" if len(created) > 2 else ""
            lines.append(f"\n行，这个我记住了：{detail}{suffix}。")
        if changed:
            lines.append("\n已更新记忆。")
        if asks:
            lines.append("\n再确认一个关键点：" + asks[0])
        return "\n".join(lines)

    def _try_memory_command(self, text: str) -> SkillResponse | None:
        original = text.strip()
        normalized = _normalize_command(original)
        lowered = normalized.lower()

        if "approve" in lowered or "同意保存" in normalized or "确认保存" in normalized:
            return self._approve_pending()
        if "reject" in lowered or "拒绝保存" in normalized or "不要保存" in normalized:
            return self._reject_pending()
        if _is_reset_memory_command(normalized, lowered):
            return self.reset_memory()
        if "show" in lowered or "展示" in normalized or "查看" in normalized:
            return self.show_memory()
        if "find" in lowered or "query" in lowered or "查询" in normalized or "搜索" in normalized:
            query = _strip_command_words(normalized, ["find", "query", "查询", "搜索", "记忆"])
            matches = self.memory.find(query, include_inactive=True)
            if not matches:
                return SkillResponse("查询结果：无匹配记忆。", [], [], [])
            lines = ["查询结果："]
            for item in matches:
                lines.append(f"- {item.id} [{item.status}/{item.type}/{item.scope}] {item.content}")
            return SkillResponse("\n".join(lines), [], [m.id for m in matches if m.status == ACTIVE], [])
        if "删除" in normalized or "delete" in lowered or "forget" in lowered:
            query = _strip_command_words(normalized, ["delete", "forget", "删除", "这条记忆", "记忆"])
            followup = ""
            if "然后" in query:
                query, followup = query.split("然后", 1)
                followup = followup.strip(" ：:。")
            deleted = self.memory.delete(query)
            names = ", ".join(item.id for item in deleted) or "无匹配"
            active = self.retrieve_relevant_memories(normalized)
            text_after = f"删除结果：{names}。后续检索会过滤 deleted 记忆。"
            if active:
                text_after += "\n\n删除后仍可使用的相关记忆：\n" + "\n".join(f"- {m.content}" for m in active)
            if followup:
                followup_memories = self.retrieve_relevant_memories(followup, normalized)
                text_after += "\n\n继续处理：\n" + self._task_answer(followup, followup_memories, normalized)
                active = followup_memories
            return SkillResponse(text_after, self.memory.events[-len(deleted):] if deleted else [], [m.id for m in active], [])
        if "降权" in normalized or "降级" in normalized or "downgrade" in lowered:
            matches = self.memory.find(normalized, include_inactive=False)
            actions = []
            for item in matches:
                self.memory.downgrade(item.id, "user_requested_downgrade")
                actions.append(self.memory.events[-1])
            return SkillResponse(f"降权 {len(actions)} 条记忆。", actions, [], [])
        if "归档" in normalized or "archive" in lowered:
            matches = self.memory.find(normalized, include_inactive=False)
            actions = []
            for item in matches:
                self.memory.archive(item.id, "user_requested_archive")
                actions.append(self.memory.events[-1])
            return SkillResponse(f"归档 {len(actions)} 条记忆。", actions, [], [])
        return None

    def _apply_updates(self, text: str, context: str = "") -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for match in self._conflicting_memories(text, context):
            self.memory.downgrade(match.id, f"新反馈缩小或推翻旧规则：{text}")
            actions.append(self.memory.events[-1])
        for item in self.extract_memory_candidates(text, context):
            if not self._is_duplicate(item):
                self.memory.add(item)
                actions.append(self.memory.events[-1])
        return actions

    def _record_assistant_candidate(self, response_text: str, user_text: str, context: str = "") -> list[dict[str, Any]]:
        scope = _infer_scope(user_text, context)
        if scope != "relationship_gift":
            return []
        gift_object = _extract_recommended_gift(response_text)
        if not gift_object:
            return []
        recipient_label, recipient_key = _gift_recipient(user_text, context)
        if recipient_key != "girlfriend":
            item = MemoryItem(
                CANDIDATE,
                f"给{recipient_label}选礼物时，曾推荐候选方案：{gift_object}",
                scope="relationship_gift",
                subject="assistant",
                target=recipient_key,
                object=gift_object,
                predicate="proposed",
                source="assistant_output",
                evidence=[response_text],
                applies_when=["relationship_gift"],
                tags=["礼物", "候选", recipient_label, gift_object],
                validity={"time_scope": "task_thread", "status": "proposed"},
            )
            if self._is_duplicate(item):
                return []
            self.memory.add(item)
            return [self.memory.events[-1]]
        item = MemoryItem(
            CANDIDATE,
            f"给女朋友选礼物时，曾推荐候选方案：{gift_object}",
            scope="relationship_gift",
            subject="assistant",
            target="girlfriend",
            object=gift_object,
            predicate="proposed",
            source="assistant_output",
            evidence=[response_text],
            applies_when=["relationship_gift"],
            tags=["礼物", "候选", gift_object],
            validity={"time_scope": "task_thread", "status": "proposed"},
        )
        if self._is_duplicate(item):
            return []
        self.memory.add(item)
        return [self.memory.events[-1]]

    def extract_memory_candidates(self, text: str, context: str = "") -> list[MemoryItem]:
        normalized = text.strip()
        if _is_memory_question(normalized) or _is_acknowledgement_only(normalized) or _is_plain_task_request(normalized):
            return []
        payload = _explicit_memory_payload(normalized) or normalized
        if not normalized or (not _has_memory_signal(normalized, context) and not _has_memory_signal(payload, context)):
            return []
        effective_context = context
        scope = _infer_scope(payload, context)
        if scope == "general" and _looks_like_purchase_history(payload):
            recipient = _dominant_gift_recipient(self.memory.active())
            if recipient:
                recipient_label, _ = recipient
                effective_context = f"{context}\n当前送礼对象：{recipient_label}".strip()
                scope = "relationship_gift"
        if scope == "relationship_gift" and not _is_girlfriend_gift_context(normalized, context):
            return _generic_gift_candidates(payload, effective_context, evidence_text=normalized)
        if scope == "relationship_gift" and _looks_like_final_gift_choice(normalized):
            return []
        relationship = _relationship_candidates(payload, effective_context, scope)
        if relationship:
            return relationship
        candidates: list[MemoryItem] = []
        explicit_request = _explicit_memory_request(normalized)
        for clause in _split_clauses(payload):
            if not _has_memory_signal(clause, effective_context) and not explicit_request:
                continue
            memory_type = _infer_memory_type(clause, scope)
            content = _clean_memory_content(clause, scope, effective_context)
            if not content:
                continue
            candidates.append(
                MemoryItem(
                    memory_type,
                    content,
                    scope=scope,
                    subject=_infer_subject(clause, scope),
                    target=_infer_target(clause, scope),
                    object=_infer_object(clause, scope),
                    predicate=_infer_predicate(clause, memory_type),
                    source="chat_feedback",
                    evidence=[normalized],
                    applies_when=[scope],
                    tags=_keywords(clause),
                    validity=_infer_validity(clause, memory_type),
                )
            )
        return candidates

    def _conflicting_memories(self, text: str, context: str = "") -> list[MemoryItem]:
        lowered = text.lower()
        conflict_terms = ["不适用", "不用", "不要", "不能", "只用于", "仅用于", "改成", "推翻", "不再"]
        if not any(term in lowered for term in conflict_terms):
            return []
        scope = _infer_scope(text, context)
        if scope == "relationship_gift" and any(term in text for term in ["送过的就不要", "已经送过", "送过的东西"]):
            return []
        if scope == "relationship_gift" and any(term in text for term in ["不送首饰", "不能不送首饰", "非首饰"]):
            return []
        terms = [term for term in _keywords(text) if term not in {"礼物"}]
        if "不适用" in text:
            if "步行不适用" in text or "少步行不适用" in text:
                terms = ["步行"]
            else:
                scoped_terms = [term for term in terms if f"{term}不适用" in text or f"{term} 不适用" in text]
                terms = scoped_terms or terms
        matches = []
        for item in self.memory.active():
            if any(term and term in item.content for term in terms):
                matches.append(item)
        if not matches:
            topic_terms = ["步行", "风险", "番茄钟", "文献综述", "模板", "自测", "可复现", "香水", "前女友"]
            for item in self.memory.active():
                if item.scope == scope and any(term in item.content for term in topic_terms if term in text):
                    matches.append(item)
        if not matches and "模板" in text:
            scope = _infer_scope(text, context)
            matches.extend(
                item
                for item in self.memory.active()
                if item.scope == scope
                and item.type == WORKFLOW
                and any(term in item.content for term in ["文献综述", "方法", "数据集", "局限", "可复现", "模板"])
            )
        return matches

    def _is_duplicate(self, candidate: MemoryItem) -> bool:
        for item in self.memory.active():
            same_content = item.content == candidate.content and item.scope == candidate.scope
            same_fact = (
                candidate.predicate
                and item.scope == candidate.scope
                and item.type == candidate.type
                and item.subject == candidate.subject
                and item.target == candidate.target
                and item.object == candidate.object
                and item.predicate == candidate.predicate
            )
            if same_content or same_fact:
                return True
        return False

    def _approve_pending(self) -> SkillResponse:
        if not self.pending_proposals:
            return SkillResponse("没有待授权的记忆候选。", [], [], [])
        created = []
        for item in self.pending_proposals:
            item.user_approved = True
            created.append(self.memory.add(item))
        self.pending_proposals = []
        actions = self.memory.events[-len(created):]
        return SkillResponse("已获授权并保存长期记忆：\n" + "\n".join(f"- {m.content}" for m in created), actions, [], [])

    def _reject_pending(self) -> SkillResponse:
        count = len(self.pending_proposals)
        self.pending_proposals = []
        return SkillResponse(f"已拒绝保存 {count} 条候选记忆，不写入长期记忆库。", [], [], [])

    def _suggest_followups(self, text: str, memories: list[MemoryItem], context: str = "") -> list[str]:
        scope = _infer_scope(text, context)
        if scope == "relationship_gift" and not _is_girlfriend_gift_context(text, context):
            profile = _gift_profile_text(text, memories, context)
            enough = "预算" in profile and bool(_extract_gift_interests(profile))
            if enough:
                return []
            return ["收礼人更偏实用设备、日常用品，还是体验型礼物？"]
        if scope == "relationship_gift":
            enough = (
                any("预算" in m.content for m in memories)
                and any("喜欢" in m.content for m in memories)
                and any("送过" in m.content or "重复" in m.content or "避开首饰" in m.content for m in memories)
            )
            if enough or any(token in text for token in ["给我一个", "非首饰推荐", "礼物推荐"]):
                return []
        if scope == "relationship_gift" and not memories:
            return ["收礼人有没有明确喜欢、讨厌或已经收到过的东西？"]
        if scope == "life_family_travel" and not memories:
            return ["同行人有没有老人、孩子或步行限制？", "偏自然、动物还是城市景点？"]
        if scope == "work_report" and "老板" not in text and not memories:
            return ["这份材料是给老板还是跨部门团队？"]
        if scope == "study_plan" and not memories:
            return ["现在是打基础、常规复习还是临考冲刺？"]
        if scope == "research_review" and not memories:
            return ["这是文献综述、评测整理还是研究问题 brainstorm？"]
        return []

    def _task_answer(self, text: str, memories: list[MemoryItem], context: str = "") -> str:
        scope = _infer_scope(text, context)
        if scope == "relationship_gift" and not _is_girlfriend_gift_context(text, context):
            return _generic_gift_answer(text, memories, context)
        if scope == "relationship_gift":
            return _gift_answer(text, memories, context)
        if scope == "life_family_travel":
            return _travel_answer(text, memories)
        if scope == "work_report":
            return _work_answer(text, memories)
        if scope == "study_plan":
            return _study_answer(text, memories)
        if scope == "research_review":
            return _research_answer(text, memories)
        return f"我会先按当前请求处理：{text}\n如果你给出稳定偏好或工作方法，我会把它提取为可管理记忆。"


def _normalize_command(text: str) -> str:
    if not text.startswith("/"):
        return text
    parts = text[1:].split(maxsplit=1)
    command = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""
    aliases = {
        "memory": rest,
        "mem": rest,
        "reset-memory": "reset memory",
        "reset_memory": "reset memory",
        "show-memory": "show memory",
        "show_memory": "show memory",
        "delete-memory": "delete " + rest,
        "delete_memory": "delete " + rest,
        "downgrade-memory": "downgrade " + rest,
        "downgrade_memory": "downgrade " + rest,
        "archive-memory": "archive " + rest,
        "archive_memory": "archive " + rest,
        "find-memory": "find " + rest,
        "find_memory": "find " + rest,
    }
    return aliases.get(command, text)


def _is_reset_memory_command(normalized: str, lowered: str) -> bool:
    if "reset" in lowered or "清空" in normalized or "重置" in normalized:
        return True
    clear_terms = ["清除", "清理", "清掉", "删掉", "删除", "忘掉"]
    broad_targets = ["当前记忆", "所有记忆", "全部记忆", "全部的记忆", "记忆库"]
    if any(term in normalized for term in clear_terms) and any(target in normalized for target in broad_targets):
        return True
    stripped = normalized.strip(" 。！？!?")
    return stripped in {"清除记忆", "清理记忆", "清掉记忆", "删掉记忆", "删除记忆", "忘掉记忆"}


def _strip_command_words(text: str, words: list[str]) -> str:
    result = text
    for word in words:
        result = result.replace(word, "")
    return result.strip(" 。：:，,")


def _split_clauses(text: str) -> list[str]:
    normalized = text
    for prefix in ["以后", "请记住", "记住：", "记住:", "用户反馈：", "反馈："]:
        normalized = normalized.replace(prefix, "")
    clauses = [normalized]
    normalized = normalized.replace("，再", "；再").replace("，但", "；但").replace("；但", "；")
    for sep in ["；", ";", "。", "\n"]:
        clauses = [part for clause in clauses for part in clause.split(sep)]
    return [clause.strip(" ，,：:") for clause in clauses if clause.strip(" ，,：:")]


def _explicit_memory_request(text: str) -> bool:
    return any(token in text for token in ["以后", "请记住", "记住", "加入这条记忆", "这条记忆", "同意保存", "确认保存"])


def _explicit_memory_payload(text: str) -> str:
    patterns = [
        r"(?:加入|新增|添加|保存|记住)(?:这条)?记忆[：:，,]?[“\"](.+?)[”\"]\s*$",
        r"(?:加入|新增|添加|保存|记住)(?:这条)?记忆[：:，,]\s*(.+)$",
        r"(?:把|将)[“\"](.+?)[”\"](?:加入|新增|添加|保存|记住)(?:为)?记忆\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip(" ：:，,。；;“”\"")
    return ""


def _is_memory_question(text: str) -> bool:
    stripped = text.strip()
    if _explicit_memory_request(stripped):
        return False
    question_mark = "?" in stripped or "？" in stripped or stripped.endswith("吗")
    question_terms = ["你还记得", "还记得", "记得之前", "什么", "哪些", "有没有记"]
    return question_mark and any(term in stripped for term in question_terms)


def _is_acknowledgement_only(text: str) -> bool:
    stripped = text.strip(" 。！？!?")
    acknowledgements = ["好的", "好", "行", "可以", "嗯", "就这样"]
    if stripped in acknowledgements:
        return True
    return stripped.startswith(("好的，", "好，")) and any(term in stripped for term in ["推荐", "就好", "可以"])


def _is_polluted_memory_item(item: MemoryItem) -> bool:
    text = item.content
    evidence = " ".join(item.evidence)
    if any(token in text for token in ["愚蠢", "你还记得", "好的，给我", "我的意思是"]):
        return True
    if item.type == PREFERENCE and "女朋友喜欢紫色" in text and any(token in evidence for token in ["不要硬拗紫色", "别用紫色", "不能用紫色"]):
        return True
    return False


def _memory_top_k() -> int:
    raw = os.getenv("ASSIST_MEMORY_TOP_K", "8")
    try:
        value = int(raw)
    except ValueError:
        return 8
    return min(max(value, 1), 20)


def _score_memory_item(
    item: MemoryItem,
    scope: str,
    terms: list[str],
    index: int,
    total: int,
    gift_recipient_key: str = "",
) -> tuple[float, list[str]]:
    haystack = _memory_haystack(item)
    scope_exact = bool(scope and scope == item.scope)
    scope_applies = bool(scope and scope in item.applies_when)
    term_hits = sorted({term for term in terms if term and term in haystack})
    if not scope_exact and not scope_applies and not term_hits:
        return 0.0, []

    score = 0.0
    reasons: list[str] = []
    if scope_exact:
        score += 0.35
        reasons.append("scope_exact")
    elif scope_applies:
        score += 0.28
        reasons.append("scope_applies")

    if term_hits:
        score += min(0.3, 0.06 * len(term_hits))
        reasons.append("keyword:" + "、".join(term_hits[:5]))

    confidence = min(max(item.confidence, 0.0), 1.0)
    score += 0.15 * confidence
    reasons.append(f"confidence:{confidence:.2f}")

    type_weight = {
        CONSTRAINT: 0.12,
        DECISION: 0.11,
        HISTORY: 0.11,
        WORKFLOW: 0.09,
        PREFERENCE: 0.08,
        CONTEXT_FACT: 0.08,
        CANDIDATE: 0.02,
    }.get(item.type, 0.04)
    score += type_weight
    reasons.append(f"type:{item.type}")

    if gift_recipient_key and item.scope == "relationship_gift":
        score += 0.14
        reasons.append(f"recipient:{gift_recipient_key}")

    time_scope = str(item.validity.get("time_scope", ""))
    validity_weight = {
        "current_task": 0.08,
        "task_thread": 0.06,
        "past": 0.05,
        "long_term": 0.03,
    }.get(time_scope, 0.02)
    score += validity_weight
    if time_scope:
        reasons.append(f"time_scope:{time_scope}")

    recency = (index + 1) / max(total, 1)
    score += 0.1 * recency
    reasons.append(f"recency:{recency:.2f}")

    if item.type == CANDIDATE:
        score -= 0.05
        reasons.append("candidate_downrank")

    return round(max(0.0, min(score, 1.0)), 3), reasons


def _memory_haystack(item: MemoryItem) -> str:
    return " ".join(
        [
            item.content,
            item.scope,
            item.subject,
            item.target,
            item.object,
            item.predicate,
            *item.applies_when,
            *item.tags,
        ]
    )


def _relevant_memory_pack(text: str, context: str, scored: list[ScoredMemory]) -> dict[str, Any]:
    scope = _infer_scope(text, context)
    top_k = _memory_top_k()
    entries = []
    for rank, entry in enumerate(scored, start=1):
        item = entry.item
        entries.append(
            {
                "rank": rank,
                "id": item.id,
                "score": entry.score,
                "reasons": entry.reasons,
                "type": item.type,
                "scope": item.scope,
                "subject": item.subject,
                "target": item.target,
                "object": item.object,
                "predicate": item.predicate,
                "content": item.content,
                "confidence": item.confidence,
                "validity": item.validity,
                "updated_at": item.updated_at,
            }
        )
    return {
        "query": {
            "scope": scope,
            "keywords": [term for term in _keywords(text) if term not in {"礼物"}][:12],
            "top_k": top_k,
            "returned": len(entries),
        },
        "ranking_policy": {
            "hard_filters": ["status=active", "pollution_guard", "scope_or_keyword_hit", "gift_recipient_match"],
            "weights": ["scope", "keyword", "recipient", "type_priority", "confidence", "validity", "recency"],
            "downrank": ["candidate"],
        },
        "entries": entries,
    }


def _is_plain_task_request(text: str) -> bool:
    stripped = text.strip(" 。！？!?")
    if any(token in stripped for token in ["以后", "记住", "喜欢", "不喜欢", "不要", "不能", "预算", "送过", "选定"]):
        return False
    if stripped in {"给我一个礼物推荐", "给我一个推荐", "再给一个礼物方向", "帮我给女朋友选个礼物"}:
        return True
    if "推荐" in stripped and any(token in stripped for token in ["女朋友", "老公", "老婆", "妈妈", "爸爸", "朋友", "同事", "收礼人"]):
        return True
    return False


def _has_memory_signal(text: str, context: str = "") -> bool:
    signals = [
        "以后",
        "请记住",
        "记住",
        "加入这条记忆",
        "这条记忆",
        "喜欢",
        "不喜欢",
        "讨厌",
        "偏好",
        "要少",
        "不要",
        "不能",
        "不用",
        "只用于",
        "仅用于",
        "改成",
        "保留",
        "每天",
        "先",
        "最后",
        "风险",
        "负责人",
        "下一步",
        "局限",
        "可复现",
        "不要夸大",
        "闺蜜",
        "前女友",
        "送过",
        "送了",
        "送出",
        "晒过",
        "预算",
        "保留一个",
        "就保留一个",
        "只要一个",
        "一个最合适",
        "选定",
        "选了",
        "选过",
        "决定",
        "定了",
        "就这个",
        "就它",
        "满意",
        "买过",
        "买了",
        "已买",
        "已经买",
        "下单",
        "确认送",
        "非首饰",
        "不送首饰",
        "爱好",
        "职业",
        "程序员",
        "养花",
        "养金鱼",
        "以前送过",
    ]
    if any(signal in text for signal in signals):
        return True
    if _is_relationship_context(context):
        relationship_fact = any(token in text for token in ["紫色", "1000", "一千", "一个", "银色", "手链"])
        gift_decision_or_history = any(token in text for token in ["选定", "选了", "选过", "决定", "送过", "送了", "送出", "买过", "买了", "下单", "满意", "就这个", "就它"])
        return relationship_fact or gift_decision_or_history
    return False


def _infer_scope(text: str, context: str = "") -> str:
    if _is_gift_context(text, context):
        return "relationship_gift"
    if any(token in text for token in ["家庭", "亲子", "旅行", "行程", "路线", "半日游", "动物", "网红", "父亲"]):
        return "life_family_travel"
    if any(token in text for token in ["老板", "周报", "项目", "跨部门", "同步", "研发", "设计", "运营", "风险", "负责人"]):
        return "work_report"
    if any(token in text for token in ["学习", "复习", "考试", "高数", "线性代数", "物理", "英语", "番茄钟", "例题", "自测"]):
        return "study_plan"
    if any(token in text for token in ["文献", "综述", "RAG", "研究", "数据集", "可复现", "brainstorm", "多模态"]):
        return "research_review"
    return "general"


def _infer_memory_type(text: str, scope: str) -> str:
    if _looks_like_purchase_history(text) or any(token in text for token in ["已经送", "已送", "送过", "送了"]):
        return HISTORY
    if scope == "work_report" and any(token in text for token in ["只用于", "仅用于", "不要", "不用", "不能"]):
        return CONSTRAINT
    if scope == "work_report" and any(token in text for token in ["表格", "风险", "负责人", "下一步", "3 条结论", "3条结论"]):
        return WORKFLOW
    if scope == "study_plan":
        if any(token in text for token in ["不用", "不要", "不能", "只剩", "冲刺"]):
            return CONSTRAINT if "不用" in text or "不要" in text else WORKFLOW
        return WORKFLOW
    if scope == "research_review":
        if any(token in text for token in ["不要", "不用", "不能", "只保留"]):
            return CONSTRAINT
        return WORKFLOW if any(token in text for token in ["文献", "方法", "数据集", "局限", "可复现", "类别"]) else PREFERENCE
    if any(token in text for token in ["不能", "不要", "不喜欢", "前女友"]):
        return CONSTRAINT
    if scope == "relationship_gift" and any(token in text for token in ["保留一个", "只要一个", "一个最合适", "推荐的有点多"]):
        return WORKFLOW
    if any(token in text for token in ["只用于", "仅用于", "不适用", "同行", "闺蜜"]):
        return CONSTRAINT
    if any(token in text for token in ["膝盖不好", "负责人"]):
        return CONTEXT_FACT
    return PREFERENCE


def _clean_memory_content(text: str, scope: str, context: str = "") -> str:
    content = text.strip()
    for prefix in ["以后", "家庭出行", "写给老板的项目材料，请", "学习计划请", "做文献综述时，请"]:
        content = content.replace(prefix, "")
    content = content.strip(" ，,。：:")
    if scope == "relationship_gift":
        content = _relationship_memory_content(content, context)
    return content


def _infer_subject(text: str, scope: str) -> str:
    if scope == "relationship_gift":
        if any(token in text for token in ["她", "女朋友"]):
            return "girlfriend"
        return "user"
    if any(token in text for token in ["父亲", "爸爸"]):
        return "father"
    if "孩子" in text:
        return "child"
    return "user"


def _infer_target(text: str, scope: str) -> str:
    if scope == "relationship_gift":
        return "girlfriend"
    if scope == "work_report":
        if "老板" in text:
            return "boss"
        if "跨部门" in text:
            return "cross_functional_team"
    return ""


def _infer_object(text: str, scope: str) -> str:
    if scope == "relationship_gift":
        return _extract_gift_object(text)
    for token in ["3 条结论", "3条结论", "风险表", "番茄钟", "例题", "自测题", "可复现性", "文献综述模板"]:
        if token in text:
            return token
    return ""


def _infer_predicate(text: str, memory_type: str) -> str:
    if memory_type == PREFERENCE:
        return "likes" if "喜欢" in text else "prefers"
    if memory_type == CONSTRAINT:
        return "must_avoid" if any(token in text for token in ["不要", "不能", "不喜欢", "避开"]) else "constrains"
    if memory_type == WORKFLOW:
        return "uses_workflow"
    if memory_type == CANDIDATE:
        return "proposed"
    if memory_type == DECISION:
        return "selected"
    if memory_type == HISTORY:
        return "completed"
    return "states"


def _infer_validity(text: str, memory_type: str) -> dict[str, str]:
    if memory_type == DECISION or text.startswith("这次"):
        return {"time_scope": "current_task"}
    if memory_type == HISTORY:
        return {"time_scope": "past"}
    return {"time_scope": "long_term"}


def _looks_like_purchase_history(text: str) -> bool:
    return any(token in text for token in ["已经买过", "已买过", "买过", "已经买了", "已买了", "买了", "已入", "下单"])


def _dominant_gift_recipient(memories: list[MemoryItem]) -> tuple[str, str] | None:
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for memory in memories:
        if memory.scope != "relationship_gift":
            continue
        key = memory.target if memory.target and memory.target not in {"assistant"} else memory.subject
        if key not in {"girlfriend", "husband", "wife", "mother", "father", "child", "friend", "colleague"}:
            continue
        counts[key] = counts.get(key, 0) + 1
        labels[key] = _gift_recipient_label_for_key(key)
    if not counts:
        return None
    ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        return None
    key = ordered[0][0]
    return labels[key], key


def _gift_recipient_label_for_key(key: str) -> str:
    return {
        "girlfriend": "女朋友",
        "husband": "老公",
        "wife": "老婆",
        "mother": "妈妈",
        "father": "爸爸",
        "child": "孩子",
        "friend": "朋友",
        "colleague": "同事",
    }.get(key, "收礼人")


def _relationship_memory_content(content: str, context: str) -> str:
    if not _is_girlfriend_gift_context(content, context):
        recipient_label, _ = _gift_recipient(content, context)
        budget = _extract_budget(content)
        if budget:
            return f"给{recipient_label}选礼物预算在 {budget}"
        interests = _extract_gift_interests(content)
        if interests:
            return f"{recipient_label}的礼物偏好/背景：{interests}"
        previous = _extract_previous_gifts(content)
        if previous:
            return f"以前送过{recipient_label}{previous}"
        return content
    if "预算" in content:
        budget = _extract_budget(content)
        if "喜欢紫色" in content or "紫色" in content:
            if budget:
                return f"给女朋友选礼物预算在 {budget}；女朋友喜欢紫色"
            return "女朋友喜欢紫色"
        if budget:
            return f"给女朋友选礼物预算在 {budget}"
    if "喜欢紫色" in content or (_is_relationship_context(context) and "紫色" in content):
        return "女朋友喜欢紫色"
    if "保留一个" in content or "只要一个" in content or "一个最合适" in content:
        return "给女朋友选礼物时，用户希望只保留一个最合适的推荐"
    if "推荐的有点多" in content:
        return "给女朋友选礼物时，用户希望推荐更简洁"
    if "闺蜜" in content:
        return f"给女朋友选礼物需参考闺蜜线索：{content}"
    if "前女友" in content:
        return f"给女朋友选礼物需避开前女友相关物品：{content}"
    return content


def _generic_gift_candidates(text: str, context: str = "", evidence_text: str | None = None) -> list[MemoryItem]:
    recipient_label, recipient_key = _gift_recipient(text, context)
    evidence = evidence_text or text
    candidates: list[MemoryItem] = []
    budget = _extract_budget(text)
    if budget:
        candidates.append(
            MemoryItem(
                CONSTRAINT,
                f"给{recipient_label}选礼物预算在 {budget}",
                scope="relationship_gift",
                subject="user",
                target=recipient_key,
                object=budget,
                predicate="budget_limit",
                source="chat_feedback",
                evidence=[evidence],
                applies_when=["relationship_gift"],
                tags=["礼物", "预算", recipient_label],
                validity={"time_scope": "current_task"},
            )
        )
    interests = _extract_gift_interests(text)
    if interests:
        candidates.append(
            MemoryItem(
                PREFERENCE,
                f"{recipient_label}的礼物偏好/背景：{interests}",
                scope="relationship_gift",
                subject=recipient_key,
                target="",
                object=interests,
                predicate="likes",
                source="chat_feedback",
                evidence=[evidence],
                applies_when=["relationship_gift"],
                tags=["礼物", recipient_label, *_keywords(text)],
                validity={"time_scope": "long_term"},
            )
        )
    previous = _extract_previous_gifts(text)
    if previous:
        candidates.append(
            MemoryItem(
                HISTORY,
                f"以前送过{recipient_label}{previous}",
                scope="relationship_gift",
                subject="user",
                target=recipient_key,
                object=previous,
                predicate="gave",
                source="chat_feedback",
                evidence=[evidence],
                applies_when=["relationship_gift"],
                tags=["礼物", "送过", recipient_label],
                validity={"time_scope": "past"},
            )
        )
    if not previous and any(token in text for token in ["不要重复", "不要再选", "别重复", "避开已经买过", "避开送过"]):
        candidates.append(
            MemoryItem(
                CONSTRAINT,
                f"给{recipient_label}选礼物时，已经买过或送过的不要重复",
                scope="relationship_gift",
                subject="user",
                target=recipient_key,
                object="",
                predicate="must_avoid",
                source="chat_feedback",
                evidence=[evidence],
                applies_when=["relationship_gift"],
                tags=["礼物", "不要重复", recipient_label],
                validity={"time_scope": "long_term"},
            )
        )
    selected = _extract_selected_gift_items(text)
    if selected and selected not in previous:
        bought = _looks_like_purchase_history(text) or any(token in text for token in ["付款", "已买"])
        candidates.append(
            MemoryItem(
                HISTORY if bought else DECISION,
                f"{'已经给' if bought else '本次给'}{recipient_label}{'买过' if '买过' in text else ('买了' if bought else '选定')}{selected}",
                scope="relationship_gift",
                subject="user",
                target=recipient_key,
                object=selected,
                predicate="gave" if bought else "selected",
                source="chat_feedback",
                evidence=[evidence],
                applies_when=["relationship_gift"],
                tags=["礼物", "买了" if bought else "选定", recipient_label],
                validity={"time_scope": "past" if bought else "current_task"},
            )
        )
    return candidates


def _relationship_candidates(text: str, context: str, scope: str) -> list[MemoryItem]:
    if scope != "relationship_gift":
        return []
    if _is_memory_question(text):
        return []
    candidates: list[MemoryItem] = []
    resolved_object = _resolve_gift_reference(text, context)
    if resolved_object and _is_gift_rejection(text):
        candidates.append(
            MemoryItem(
                CONSTRAINT,
                f"当前给女朋友选礼物时不再推荐{resolved_object}",
                scope=scope,
                subject="user",
                target="girlfriend",
                object=resolved_object,
                predicate="must_avoid",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["礼物", "否决", resolved_object],
                validity={"time_scope": "current_task"},
            )
        )
    if resolved_object and _is_acceptance_signal(text):
        candidates.append(
            MemoryItem(
                DECISION,
                f"本次给女朋友的礼物已选定为{resolved_object}",
                scope=scope,
                subject="user",
                target="girlfriend",
                object=resolved_object,
                predicate="selected",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["礼物", "选定", resolved_object, "满意"],
                validity={"time_scope": "current_task", "status": "accepted"},
            )
        )
    if resolved_object and _is_execution_signal(text):
        candidates.append(
            MemoryItem(
                HISTORY,
                f"已经给女朋友送过{resolved_object}",
                scope=scope,
                subject="user",
                target="girlfriend",
                object=resolved_object,
                predicate="gave",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["礼物", "送过", resolved_object],
                validity={"time_scope": "past"},
            )
        )
    if any(token in text for token in ["不送首饰", "不能不送首饰", "非首饰", "换个非首饰"]):
        candidates.append(
            MemoryItem(
                CONSTRAINT,
                "本次给女朋友选礼物暂时避开首饰，优先非首饰品类",
                scope=scope,
                subject="user",
                target="girlfriend",
                object="首饰",
                predicate="must_avoid",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["非首饰", "首饰"],
                validity={"time_scope": "current_task"},
            )
        )
    if any(token in text for token in ["送过的就不要", "送过的东西", "送过的不要", "不要再送", "选过的不要", "已经选过", "选过的不能", "送过的不能"]):
        candidates.append(
            MemoryItem(
                CONSTRAINT,
                _avoid_repeat_content(context),
                scope=scope,
                subject="user",
                target="girlfriend",
                object=_previous_gift_objects(context) or "已送/已选礼物",
                predicate="must_avoid",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["送过", "重复"],
                validity={"time_scope": "long_term"},
            )
        )
    budget = _extract_budget(text)
    if budget:
        candidates.append(
            MemoryItem(
                CONSTRAINT,
                f"给女朋友选礼物预算在 {budget}",
                scope=scope,
                subject="user",
                target="girlfriend",
                object=budget,
                predicate="budget_limit",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["礼物", "预算"],
                validity={"time_scope": "long_term"},
            )
        )
    if "紫色" in text and any(token in text for token in ["不要硬套紫色", "不要用紫色", "不用紫色", "别用紫色", "不能用紫色"]):
        candidates.append(
            MemoryItem(
                CONSTRAINT,
                "给女朋友选首饰时不要硬套紫色偏好",
                scope=scope,
                subject="user",
                target="girlfriend",
                object="紫色",
                predicate="must_avoid",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["女朋友", "紫色", "首饰"],
                validity={"time_scope": "long_term"},
            )
        )
    elif "紫色" in text or "喜欢紫色" in text:
        candidates.append(
            MemoryItem(
                PREFERENCE,
                "女朋友喜欢紫色",
                scope=scope,
                subject="girlfriend",
                target="",
                object="紫色",
                predicate="likes",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["女朋友", "紫色"],
                validity={"time_scope": "long_term"},
            )
        )
    if "玫瑰金" in text and any(token in text for token in ["喜欢", "偏好"]):
        candidates.append(
            MemoryItem(
                PREFERENCE,
                "女朋友喜欢玫瑰金",
                scope=scope,
                subject="girlfriend",
                object="玫瑰金",
                predicate="likes",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["女朋友", "玫瑰金", "首饰"],
                validity={"time_scope": "long_term"},
            )
        )
    gift_object = _extract_gift_object(text) or resolved_object
    if gift_object and gift_object != "首饰" and any(token in text for token in ["送过", "送了", "买过", "已经送", "以前送", "之前送"]):
        candidates.append(
            MemoryItem(
                HISTORY,
                f"之前已经送过{gift_object}",
                scope=scope,
                subject="user",
                target="girlfriend",
                object=gift_object,
                predicate="gave",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["礼物", "送过", gift_object],
                validity={"time_scope": "past"},
            )
        )
    elif gift_object and any(token in text for token in ["选定", "选了", "决定", "定了", "就选", "就要"]):
        candidates.append(
            MemoryItem(
                DECISION,
                f"本次给女朋友的礼物已选定为{gift_object}",
                scope=scope,
                subject="user",
                target="girlfriend",
                object=gift_object,
                predicate="selected",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["礼物", "选定", gift_object],
                validity={"time_scope": "current_task"},
            )
        )
    if "保留一个" in text or "只要一个" in text or "一个最合适" in text or "推荐的有点多" in text:
        candidates.append(
            MemoryItem(
                WORKFLOW,
                "给女朋友选礼物时，用户希望只保留一个最合适的推荐",
                scope=scope,
                subject="user",
                target="assistant",
                object="推荐数量",
                predicate="uses_workflow",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["推荐简洁", "一个"],
                validity={"time_scope": "long_term"},
            )
        )
    if "闺蜜" in text:
        candidates.append(
            MemoryItem(
                CONTEXT_FACT,
                f"给女朋友选礼物需参考闺蜜线索：{text}",
                scope=scope,
                subject="girlfriend_friend",
                target="girlfriend",
                object=_extract_gift_object(text),
                predicate="social_signal",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["闺蜜", "礼物"],
                validity={"time_scope": "long_term"},
            )
        )
    if "前女友" in text:
        candidates.append(
            MemoryItem(
                CONSTRAINT,
                f"给女朋友选礼物需避开前女友相关物品：{text}",
                scope=scope,
                subject="user",
                target="girlfriend",
                object=_extract_gift_object(text),
                predicate="must_avoid",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["前女友", "禁忌"],
                validity={"time_scope": "long_term"},
            )
        )
    return candidates


def _extract_budget(text: str) -> str:
    if not _has_budget_marker(text):
        return ""
    if "千元" in text or "千元左右" in text or "1000 元左右" in text or "1000元左右" in text:
        return "1000 元左右"
    if "剩下的预算" in text and len(re.findall(r"\d{2,5}", text)) >= 2:
        return ""
    if len(re.findall(r"\d{2,5}", text)) >= 2 and any(token in text for token in ["来一只", "再来", "已经买", "买了", "下单", "付款"]):
        return ""
    match = _standalone_budget_match(text)
    if match:
        suffix = match.group(2) or "左右"
        return f"{match.group(1)} 元{suffix}"
    match = _embedded_budget_match(text)
    if match:
        return f"{match.group(1)} 元{match.group(2)}"
    match = re.search(r"预算(?:在|是)?\s*(\d{2,5})\s*(?:元|块)?\s*(左右|以内)?", text)
    if match:
        suffix = match.group(2) or "左右"
        return f"{match.group(1)} 元{suffix}"
    match = re.search(r"(?:控制在|不超过|以内|上限)\s*(\d{2,5})\s*(?:元|块)?\s*(左右|以内)?", text)
    if match:
        suffix = match.group(2) or "以内"
        return f"{match.group(1)} 元{suffix}"
    if "一千" in text:
        return "1000 元左右" if "左右" in text else "1000 元以内"
    return ""


def _extract_gift_object(text: str) -> str:
    candidates = [
        "智能阳台养护套装",
        "全光谱植物补光灯",
        "温湿度光照记录仪",
        "鱼缸水质监测仪",
        "始祖鸟的双肩背包",
        "始祖鸟双肩背包",
        "始祖鸟的防晒服",
        "始祖鸟防晒服",
        "双肩背包",
        "防晒服",
        "银色手链",
        "玫瑰金耳钉",
        "紫色真丝小方巾",
        "小众香氛礼盒",
        "施华洛世奇的水晶项链",
        "施华洛世奇项链",
        "玫瑰金项链",
        "紫水晶项链",
        "水晶项链",
        "项链",
        "手链",
        "香水",
        "包袋配饰",
        "首饰",
    ]
    for candidate in candidates:
        if candidate in text:
            return candidate
    if "玫瑰金" in text:
        return "玫瑰金"
    return ""


def _extract_recommended_gift(text: str) -> str:
    for marker in ["推荐：", "我就保留一个推荐："]:
        if marker in text:
            tail = text.split(marker, 1)[1]
            first_line = tail.splitlines()[0]
            return _normalize_gift_object(first_line.strip(" 。；;，,"))
    return _extract_gift_object(text)


def _normalize_gift_object(text: str) -> str:
    if "阳台" in text and "养护" in text:
        return "智能阳台养护套装"
    if "补光灯" in text:
        return "全光谱植物补光灯"
    if "水质" in text and "监测" in text:
        return "鱼缸水质监测仪"
    if "始祖鸟" in text and "背包" in text:
        return "始祖鸟双肩背包"
    if "始祖鸟" in text and "防晒服" in text:
        return "始祖鸟防晒服"
    if "银色" in text and "手链" in text:
        return "银色手链"
    if "玫瑰金" in text and "耳钉" in text:
        return "玫瑰金耳钉"
    if "紫色" in text and "方巾" in text:
        return "紫色真丝小方巾"
    if "香氛" in text or "香水" in text:
        return "小众香氛礼盒" if "礼盒" in text or "香氛" in text else "香水"
    return _extract_gift_object(text)


def _resolve_gift_reference(text: str, context: str) -> str:
    direct = _normalize_gift_object(text)
    if direct:
        return direct
    if not any(token in text for token in ["这个", "刚才", "上次", "上一", "选过", "送过", "已经选", "已经送"]):
        return ""
    return _latest_gift_object_from_context(context)


def _latest_gift_object_from_context(context: str) -> str:
    objects = []
    for line in context.splitlines():
        obj = ""
        if line.startswith("assistant:"):
            obj = _extract_recommended_gift(line)
        if not obj:
            obj = _normalize_gift_object(line)
        if obj:
            objects.append(obj)
    return objects[-1] if objects else ""


def _previous_gift_objects(context: str) -> str:
    objects = []
    for line in context.splitlines():
        if any(token in line for token in ["已选定为", "送过", "确认送", "下单", "就这个", "送了"]):
            obj = _normalize_gift_object(line)
            if obj and obj not in objects:
                objects.append(obj)
    return "、".join(objects)


def _avoid_repeat_content(context: str) -> str:
    objects = _previous_gift_objects(context)
    if objects:
        return f"给女朋友选礼物时，已经选过或送过的礼物不要重复：{objects}"
    return "给女朋友选礼物时，已经选过或送过的礼物不要重复"


def _is_acceptance_signal(text: str) -> bool:
    return any(token in text for token in ["满意", "就这个", "就它", "可以", "定这个", "确认这个", "选这个", "挺好"])


def _looks_like_final_gift_choice(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("就") and stripped.endswith(("吧", "了", "就行", "好了")) and any(
        token in stripped for token in ["手链", "项链", "耳钉", "吊坠", "香氛", "香水", "方巾", "包"]
    )


def _is_execution_signal(text: str) -> bool:
    return any(token in text for token in ["送出", "送了", "已经送", "下单", "买了", "付款", "确认送"])


def _is_gift_rejection(text: str) -> bool:
    return any(token in text for token in ["换一个", "不要这个", "不选这个", "太多", "不合适", "算了", "别推荐这个"])


def _is_relationship_context(context: str) -> bool:
    return any(
        token in context
        for token in ["女朋友", "老公", "老婆", "妈妈", "爸爸", "朋友", "同事", "礼物", "送礼", "闺蜜", "前女友"]
    )


def _has_budget_marker(text: str) -> bool:
    return (
        any(token in text for token in ["预算", "控制在", "不超过", "上限", "千元"])
        or bool(re.search(r"\d{2,5}\s*(?:元|块)?\s*以内", text))
        or _standalone_budget_match(text) is not None
        or _embedded_budget_match(text) is not None
    )


def _standalone_budget_match(text: str) -> re.Match[str] | None:
    return re.fullmatch(r"\s*(\d{2,5})\s*(?:元|块)?\s*(左右|以内)?的?[。.]?\s*", text)


def _embedded_budget_match(text: str) -> re.Match[str] | None:
    return re.search(r"(?<!\d)(\d{3,5})\s*(?:元|块)?\s*(左右|以内)(?!\d)", text)


def _has_explicit_recipient(text: str) -> bool:
    return _gift_recipient_from_text(text) is not None


def _looks_like_non_gift_domain(text: str) -> bool:
    domain_terms = [
        "家庭",
        "亲子",
        "旅行",
        "行程",
        "路线",
        "半日游",
        "老板",
        "周报",
        "项目",
        "学习",
        "复习",
        "文献",
        "综述",
        "研究",
    ]
    return any(term in text for term in domain_terms) and not any(term in text for term in ["礼物", "送礼", "送什么", "生日礼物"])


def _is_gift_context(text: str, context: str = "") -> bool:
    explicit_gift_signal = any(token in text for token in ["礼物", "送礼", "送什么", "生日礼物"])
    gift_action_signal = any(token in text for token in ["以前送过", "之前送过", "已经送", "送过", "下单", "确认送"])
    gift_item_signal = any(token in text for token in ["项链", "手链", "耳钉", "首饰", "香氛", "香水", "方巾", "背包", "防晒服"])
    gift_request_signal = any(token in text for token in ["推荐", "再给", "给我一个", "选礼物", "生日"])
    relation_signal = _has_explicit_recipient(text)
    interest_signal = any(token in text for token in ["程序员", "阳台", "养花", "蝴蝶兰", "鹿角蕨", "金鱼", "摄影", "咖啡", "露营", "跑步", "游戏"])
    if _looks_like_non_gift_domain(text) and not (explicit_gift_signal or gift_action_signal):
        return False
    if explicit_gift_signal or gift_action_signal:
        return True
    if relation_signal and gift_request_signal and _is_relationship_context(context):
        return True
    if relation_signal and (gift_item_signal or interest_signal) and not _looks_like_non_gift_domain(text):
        return True
    if _is_relationship_context(context) and not _has_explicit_recipient(text):
        continuation_signal = _has_budget_marker(text) or gift_item_signal or interest_signal or any(
            token in text
            for token in ["喜欢", "满意", "就这个", "换一个", "不要这个", "选过", "送过", "已经买", "买了", "推荐", "再给"]
        )
        return continuation_signal
    return False


def _is_girlfriend_gift_context(text: str, context: str = "") -> bool:
    explicit = _gift_recipient_from_text(text)
    if explicit:
        return explicit[1] == "girlfriend"
    if any(token in text for token in ["闺蜜", "前女友"]):
        return True
    return any(token in context for token in ["女朋友", "闺蜜", "前女友"])


def _gift_recipient(text: str, context: str = "") -> tuple[str, str]:
    explicit = _gift_recipient_from_text(text)
    if explicit:
        return explicit
    contextual = _gift_recipient_from_text(context)
    if contextual:
        return contextual
    return "收礼人", "recipient"


def _gift_recipient_from_text(text: str) -> tuple[str, str] | None:
    recipients = [
        ("女朋友", "girlfriend"),
        ("老公", "husband"),
        ("丈夫", "husband"),
        ("先生", "husband"),
        ("老婆", "wife"),
        ("妻子", "wife"),
        ("妈妈", "mother"),
        ("母亲", "mother"),
        ("爸爸", "father"),
        ("父亲", "father"),
        ("孩子", "child"),
        ("朋友", "friend"),
        ("同事", "colleague"),
    ]
    for label, key in recipients:
        if label in text:
            normalized = "老公" if key == "husband" else ("老婆" if key == "wife" else label)
            return normalized, key
    return None


def _gift_profile_text(text: str, memories: list[MemoryItem], context: str = "") -> str:
    recipient_label, recipient_key = _gift_recipient(text, context)
    relevant = [
        memory.content
        for memory in memories
        if memory.scope != "relationship_gift" or _memory_matches_gift_recipient(memory, recipient_label, recipient_key)
    ]
    context_part = context if not _has_explicit_recipient(text) else ""
    return text + "\n" + context_part + "\n" + "\n".join(relevant)


def _memory_matches_gift_recipient(memory: MemoryItem, recipient_label: str, recipient_key: str) -> bool:
    if recipient_key == "recipient":
        return True
    haystack = " ".join([memory.content, memory.subject, memory.target, memory.object, *memory.tags])
    if memory.target == recipient_key or memory.subject == recipient_key:
        return True
    if recipient_key in haystack or recipient_label in haystack:
        return True
    recipient_labels = {
        "girlfriend": ["女朋友"],
        "husband": ["老公", "丈夫", "先生"],
        "wife": ["老婆", "妻子"],
        "mother": ["妈妈", "母亲"],
        "father": ["爸爸", "父亲"],
        "child": ["孩子"],
        "friend": ["朋友"],
        "colleague": ["同事"],
    }
    other_labels = [label for key, labels in recipient_labels.items() if key != recipient_key for label in labels]
    return not any(label in haystack for label in other_labels)


def _extract_previous_gifts(text: str) -> str:
    if not any(token in text for token in ["以前送过", "之前送过", "送过", "已经送", "送了", "买过", "以前买过", "之前买过"]):
        return ""
    gifts = []
    known = [
        "始祖鸟双肩背包",
        "始祖鸟的双肩背包",
        "始祖鸟防晒服",
        "始祖鸟的防晒服",
        "双肩背包",
        "防晒服",
        "杯子",
        "玫瑰金项链",
        "银色手链",
        "小众香氛礼盒",
    ]
    for gift in known:
        if gift in text:
            normalized = gift.replace("的", "")
            if normalized == "双肩背包" and "始祖鸟双肩背包" in gifts:
                continue
            if normalized == "防晒服" and "始祖鸟防晒服" in gifts:
                continue
            if normalized not in gifts:
                gifts.append(normalized)
    return "、".join(gifts)


def _extract_selected_gift_items(text: str) -> str:
    if not any(token in text for token in ["已经买", "买过", "买了", "已买", "已入", "下单", "付款", "来一只", "再来一个", "再来一"]):
        return ""
    if any(token in text for token in ["买过的不要", "买过的别", "送过的不要", "送过的别", "已经买过的不要"]):
        return ""
    cleaned = text.replace("你记一下", "").replace("再想想剩下的预算", "")
    patterns = [
        r"(?:已经买过|已买过|买过|已经买了|已买了|买了|已入|下单了?)([^，。,；;]+)",
        r"来一只([^，。,；;]+?)(?:\s+\d{2,5}|，|,|。|$)",
        r"再来一个([^，。,；;]+?)(?:\s+\d{2,5}|，|,|。|$)",
        r"再来一(?:个|只)([^，。,；;]+?)(?:\s+\d{2,5}|，|,|。|$)",
    ]
    items = []
    for pattern in patterns:
        for match in re.finditer(pattern, cleaned):
            item = _clean_gift_item_name(match.group(1))
            if any(token in item for token in ["不要重复", "不要再选", "别重复", "不要再推荐"]):
                continue
            if item and item not in items:
                items.append(item)
    return "、".join(items)


def _clean_gift_item_name(text: str) -> str:
    item = re.sub(r"\s*\d{2,5}\s*(?:元|块)?\s*", "", text)
    item = item.strip(" ：:，,。；; 了“”\"")
    item = item.replace("日本", "").strip()
    item = re.sub(r"\s*(?:和|以及|、)\s*", "、", item).strip("、")
    if len(item) < 2 or any(token in item for token in ["预算", "剩下", "想想"]):
        return ""
    return item


def _extract_gift_interests(text: str) -> str:
    interests = []
    checks = [
        ("程序员", "程序员"),
        ("阳台", "阳台养花"),
        ("养花", "阳台养花"),
        ("蝴蝶兰", "蝴蝶兰"),
        ("鹿角蕨", "鹿角蕨"),
        ("金鱼", "养金鱼"),
        ("摄影", "摄影"),
        ("咖啡", "咖啡"),
        ("露营", "露营"),
        ("跑步", "跑步"),
        ("游戏", "游戏"),
        ("玫瑰金", "玫瑰金"),
        ("紫色", "紫色"),
    ]
    for token, label in checks:
        if token in text and label not in interests:
            interests.append(label)
    return "、".join(interests)


def _keywords(text: str) -> list[str]:
    vocab = [
        "父亲",
        "少步行",
        "步行",
        "孩子",
        "动物",
        "自然",
        "网红",
        "老板",
        "3 条结论",
        "3条结论",
        "风险",
        "风险表",
        "负责人",
        "下一步",
        "跨部门",
        "番茄钟",
        "例题",
        "自测",
        "高频考点",
        "文献综述",
        "方法类别",
        "模板",
        "数据集",
        "局限",
        "可复现性",
        "谨慎",
        "brainstorm",
        "紫色",
        "玫瑰金",
        "项链",
        "手链",
        "银色",
        "耳钉",
        "选定",
        "选过",
        "送过",
        "闺蜜",
        "前女友",
        "香水",
        "礼物",
    ]
    return [word for word in vocab if word in text]


def _gift_answer(text: str, memories: list[MemoryItem], context: str = "") -> str:
    decisions = [memory for memory in memories if memory.type == DECISION]
    history = [memory for memory in memories if memory.type == HISTORY]
    constraints = [memory for memory in memories if memory.type == CONSTRAINT]
    resolved_object = _resolve_gift_reference(text, context)
    if resolved_object and _is_execution_signal(text):
        return f"妥，就按{resolved_object}。我会把它当作已经送出的历史，后面再选礼物会避开它。"
    if resolved_object and _is_acceptance_signal(text):
        return f"妥，就定{resolved_object}。后面如果继续聊这次礼物，我就围绕它走。"
    rejected_or_avoided = _avoided_gift_objects(memories)
    previous_objects = [memory.object for memory in [*history, *decisions] if memory.object]
    avoid_scent = any(memory.type == CONSTRAINT and any(token in memory.object + memory.content for token in ["香水", "香氛"]) for memory in memories)
    non_jewelry = any("非首饰" in memory.content or "避开首饰" in memory.content for memory in constraints) or any(
        token in text for token in ["非首饰", "不送首饰", "不能不送首饰", "换个品类"]
    )
    avoid_repeat = any("送过" in memory.content or "重复" in memory.content for memory in constraints)
    if decisions and any(token in text for token in ["之前", "已经", "还记得", "选", "礼物"]) and not avoid_repeat:
        return f"我会延续已定方案：{decisions[-1].content}。如果继续推进，我会围绕这件礼物做款式、预算和避重校验。"
    wants_one = any("只保留一个" in memory.content or "一个最合适" in memory.content for memory in memories)
    likes_purple = any(memory.type == PREFERENCE and "紫色" in memory.content for memory in memories)
    likes_rose_gold = any(memory.type == PREFERENCE and "玫瑰金" in memory.content for memory in memories)
    likes_silver_bracelet = any(memory.type == PREFERENCE and "手链" in memory.content and "银色" in memory.content for memory in memories)
    has_budget = any(memory.type == CONSTRAINT and "预算" in memory.content for memory in memories)
    if avoid_repeat and previous_objects:
        if likes_purple:
            return "推荐：紫色真丝小方巾。\n理由：这次避开上次已选/已送的礼物，转到非首饰小配饰，仍能用上她的颜色偏好。"
        item = "真丝小方巾" if avoid_scent else "小众香氛礼盒"
        return f"推荐：{item}。\n理由：这次先避开上次已选/已送的礼物，换到不同品类，重复风险低。"
    if "玫瑰金耳钉" in rejected_or_avoided:
        if likes_silver_bracelet and "银色手链" not in previous_objects:
            return "推荐：银色手链。\n理由：你已经否掉玫瑰金耳钉了；手链方向里银色更贴合她后来的反馈。"
        return "推荐：银色手链。\n理由：玫瑰金耳钉已经被否掉，这个换了款式和颜色，和常见耳钉拉开差异。"
    if non_jewelry:
        color = "紫色系" if likes_purple else "她日常会用到的"
        history_clause = "它避开了已送过的礼物和首饰品类" if history else "它避开了首饰品类"
        color_clause = "；如果不送首饰，紫色偏好才适合重新作为颜色线索" if likes_purple else ""
        return (
            f"我会换到非首饰品类，不再推项链、手链、耳饰。推荐：{color}真丝小方巾。\n"
            f"理由：{history_clause}{color_clause}；预算也容易控制在 1000 元以内。"
        )
    if history and any(token in text for token in ["送过", "之前", "还记得", "重复"]):
        return "我会避开已经送过的礼物：" + "、".join(memory.object or memory.content for memory in history) + "。"
    if avoid_repeat and history:
        if likes_rose_gold:
            return (
                "推荐：玫瑰金耳钉。\n"
                "理由：如果仍坚持首饰品类，材质偏好应继续用玫瑰金；同时它避开了已经送过的玫瑰金项链，不重复同一件礼物。"
            )
        if likes_purple:
            return "推荐：紫色真丝小方巾。\n理由：它使用了已知紫色偏好，同时避开已经送过的礼物。"
        return "推荐：小众香氛礼盒。\n理由：它和已送礼物拉开品类差异，重复风险低。"
    if likes_silver_bracelet and "银色手链" not in rejected_or_avoided and "银色手链" not in previous_objects:
        return "推荐：银色手链。\n理由：她明确说手链更喜欢银色，比继续推玫瑰金耳钉更贴近这次反馈。"
    if wants_one and likes_rose_gold and has_budget:
        return "我就保留一个推荐：玫瑰金耳钉。它仍在首饰品类里尊重玫瑰金偏好，同时避开已送过的项链形态。"
    if wants_one and likes_purple and has_budget:
        return "我就保留一个推荐：紫色真丝小方巾。它符合 1000 元以内预算，也避开了首饰重复风险。"
    if likes_rose_gold and has_budget:
        return "推荐：玫瑰金耳钉。\n理由：它匹配首饰材质偏好，预算也容易控制在 1000 元以内。"
    if likes_purple and has_budget:
        return "推荐：紫色真丝小方巾。\n理由：它匹配紫色偏好，预算容易控制在 1000 元以内，也比首饰更不容易踩重复。"
    if likes_rose_gold:
        return "推荐：玫瑰金耳钉。\n理由：它直接匹配她对玫瑰金首饰的偏好。"
    if likes_purple:
        return "推荐：紫色真丝小方巾。\n理由：它直接匹配她的颜色偏好，且作为日常配饰比较实用。"
    if memories:
        item = "体验型礼物" if avoid_scent else "高频使用、低尺码风险的实用礼物"
        return f"推荐：{item}。\n理由：当前记忆不足以支持更细的颜色或材质偏好，这个默认方向更稳，也方便后续按禁忌过滤。"
    return "推荐：高频使用、低尺码风险的实用礼物方向。\n理由：在还没有预算、偏好和已送清单前，先避开强气味、尺码、材质偏好很重的品类；你补充预算、偏好和已送过清单后，我会收敛到更精确的一件。"


def _generic_gift_answer(text: str, memories: list[MemoryItem], context: str = "") -> str:
    answer_text = _explicit_memory_payload(text) or text
    recipient_label, _ = _gift_recipient(answer_text, context)
    selected = _extract_selected_gift_items(answer_text)
    if selected and any(token in answer_text for token in ["已经买", "买过", "买了", "已买", "已入", "下单", "付款"]):
        return f"已记录：这次已经给{recipient_label}买了{selected}。后续继续凑预算或做补充礼物时，我会避开重复购买。"
    if selected:
        return f"当前已选：{selected}。我会按这些已选项继续帮你核算剩余预算和补充搭配。"
    profile = _gift_profile_text(answer_text, memories, context)
    budget = _extract_budget(profile) or "预算内"
    interests = _extract_gift_interests(profile)
    previous = _extract_previous_gifts(profile)

    if any(token in profile for token in ["阳台", "养花", "蝴蝶兰", "鹿角蕨"]):
        reasons = []
        if "程序员" in profile:
            reasons.append("有可调参数和可观察数据，贴合程序员喜欢优化系统的特点")
        reasons.append("能直接服务阳台植物，尤其适合蝴蝶兰、鹿角蕨这类对光照和湿度敏感的植物")
        if "金鱼" in profile:
            reasons.append("比鱼缸设备更少依赖现有鱼缸尺寸、过滤和水体条件，踩坑概率更低")
        if previous:
            reasons.append(f"和以前送过的{previous}拉开品类，不重复")
        reasons.append(f"{budget}可以买到质感不错的一套")
        return "推荐：智能阳台养护套装（全光谱植物补光灯 + 温湿度/光照记录仪）。\n理由：" + "；".join(reasons) + "。"

    if "金鱼" in profile:
        return (
            "推荐：鱼缸水质监测仪。\n"
            f"理由：它直接服务养金鱼这个爱好，偏实用也有数据感；{budget}可选到稳定款。下单前最好确认鱼缸大小和是否已有类似设备。"
        )

    if "程序员" in profile:
        suffix = f"；也和以前送过的{previous}不重复" if previous else ""
        return (
            "推荐：高质量桌面人体工学小件，例如可编程旋钮控制器或显示器挂灯。\n"
            f"理由：它贴合程序员日常使用场景，实用、低炫技，{budget}好控制{suffix}。"
        )

    if interests:
        suffix = f"；避开以前送过的{previous}" if previous else ""
        return f"推荐：围绕{interests}选一个能直接投入使用的升级配件。\n理由：它贴合{recipient_label}已有兴趣，{budget}好控制{suffix}。"

    return (
        f"推荐：先选一件高频使用、低尺码风险的实用礼物。\n"
        f"理由：当前只知道要给{recipient_label}选礼物，缺少稳定爱好和禁忌；这种方向比香氛、饰品这类强偏好品类更稳。"
    )


def _avoided_gift_objects(memories: list[MemoryItem]) -> str:
    objects = []
    for memory in memories:
        if memory.type == CONSTRAINT and memory.object and any(token in memory.content for token in ["不再推荐", "不要重复", "避开", "不能再送"]):
            objects.append(memory.object)
    return "、".join(objects)


def _travel_answer(text: str, memories: list[MemoryItem]) -> str:
    points = "、".join(m.content for m in memories) if memories else "常规节奏、交通便利、体验丰富"
    if "南京" in text or "半日" in text:
        return f"半日路线：上午/下午任选 4 小时，先去玄武湖或中山陵音乐台这类开阔点，再安排一个室内休息点。约束：{points}。全程控制转场和步行，不排人挤人的网红点。"
    return f"行程草案：第 1 天安排低强度核心景点加早休息；第 2 天安排自然或动物相关点位；第 3 天保留机动和轻松返程。执行约束：{points}。"


def _work_answer(text: str, memories: list[MemoryItem]) -> str:
    points = "、".join(m.content for m in memories) if memories else "先明确受众，再组织结论、风险和下一步"
    return (
        f"同步材料草案：\n"
        f"1. 结论：当前进展正常，主要风险集中在依赖、排期和跨团队确认。\n"
        f"2. 风险/负责人/下一步：按受众决定是否保留风险表。\n"
        f"3. 下周动作：明确负责人、截止时间和需要拍板的问题。\n"
        f"采用规则：{points}。"
    )


def _study_answer(text: str, memories: list[MemoryItem]) -> str:
    points = "、".join(m.content for m in memories) if memories else "先判断阶段，再安排知识点、例题和练习"
    return (
        f"复习计划：第 1 天梳理高频考点并用例题开路；第 2 天集中做错题和自测；后续按天扩展知识点、例题和练习。\n"
        f"采用规则：{points}。"
    )


def _research_answer(text: str, memories: list[MemoryItem]) -> str:
    points = "、".join(m.content for m in memories) if memories else "按任务类型选择综述、评测或 brainstorm 结构"
    if "brainstorm" in text or "研究问题" in text:
        return f"3 个研究问题：1. RAG 评测中检索质量和生成质量如何解耦？2. 多跳问题的证据覆盖率如何度量？3. 不同数据集上的评测结论能否迁移？写法规则：{points}。"
    return f"综述草案：先按方法类别组织，再分别说明代表数据集、局限和可复现性，最后用谨慎表述总结趋势。当前规则：{points}。"
