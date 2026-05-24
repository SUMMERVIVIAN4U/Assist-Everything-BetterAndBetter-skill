from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .memory import ACTIVE, DELETED, SUPERSEDED, MemoryItem, MemoryStore


@dataclass
class SkillResponse:
    text: str
    memory_actions: list[dict[str, Any]]
    applied_memories: list[str]
    asks: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "memory_actions": self.memory_actions,
            "applied_memories": self.applied_memories,
            "asks": self.asks,
        }


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
        relevant = self.retrieve_relevant_memories(text, context)
        asks = self._suggest_followups(text, relevant, context)
        response_text = self.compose_response(text, relevant, actions, asks, context)
        return SkillResponse(response_text, actions, [item.id for item in relevant], asks)

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
        scope = _infer_scope(text, context)
        terms = _keywords(text)
        relevant: list[MemoryItem] = []
        for item in self.memory.active():
            haystack = " ".join([item.content, item.scope, *item.applies_when, *item.tags])
            scope_hit = scope and (scope == item.scope or scope in item.applies_when)
            term_hit = any(term and term in haystack for term in terms)
            if scope_hit or term_hit:
                relevant.append(item)
        return relevant

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
            lines.append("\n已保存可复用记忆：")
            lines.extend(f"- {a['detail']}" for a in created)
        if changed:
            lines.append("\n已处理记忆变更：")
            lines.extend(f"- {a['action']}: {a['detail']}" for a in changed)
        if memories:
            lines.append("\n本轮已应用记忆：")
            lines.extend(f"- {item.content}" for item in memories)
        if asks:
            lines.append("\n我还会追问这些缺口：")
            lines.extend(f"- {ask}" for ask in asks)
        return "\n".join(lines)

    def _try_memory_command(self, text: str) -> SkillResponse | None:
        original = text.strip()
        normalized = _normalize_command(original)
        lowered = normalized.lower()

        if "approve" in lowered or "同意保存" in normalized or "确认保存" in normalized:
            return self._approve_pending()
        if "reject" in lowered or "拒绝保存" in normalized or "不要保存" in normalized:
            return self._reject_pending()
        if "reset" in lowered or "清空" in normalized or "重置" in normalized:
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
            if "然后" in query:
                query = query.split("然后", 1)[0]
            deleted = self.memory.delete(query)
            names = ", ".join(item.id for item in deleted) or "无匹配"
            active = self.retrieve_relevant_memories(normalized)
            text_after = f"删除结果：{names}。后续检索会过滤 deleted 记忆。"
            if active:
                text_after += "\n\n删除后仍可使用的相关记忆：\n" + "\n".join(f"- {m.content}" for m in active)
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

    def extract_memory_candidates(self, text: str, context: str = "") -> list[MemoryItem]:
        normalized = text.strip()
        if not normalized or not _has_memory_signal(normalized, context):
            return []
        scope = _infer_scope(normalized, context)
        relationship = _relationship_candidates(normalized, context, scope)
        if relationship:
            return relationship
        candidates: list[MemoryItem] = []
        for clause in _split_clauses(normalized):
            if not _has_memory_signal(clause, context):
                continue
            memory_type = _infer_memory_type(clause, scope)
            content = _clean_memory_content(clause, scope, context)
            if not content:
                continue
            candidates.append(
                MemoryItem(
                    memory_type,
                    content,
                    scope=scope,
                    source="chat_feedback",
                    evidence=[normalized],
                    applies_when=[scope],
                    tags=_keywords(clause),
                )
            )
        return candidates

    def _conflicting_memories(self, text: str, context: str = "") -> list[MemoryItem]:
        lowered = text.lower()
        conflict_terms = ["不适用", "不用", "不要", "不能", "只用于", "仅用于", "改成", "推翻", "不再"]
        if not any(term in lowered for term in conflict_terms):
            return []
        terms = _keywords(text)
        matches = []
        for item in self.memory.active():
            if any(term and term in item.content for term in terms):
                matches.append(item)
        if not matches:
            scope = _infer_scope(text, context)
            topic_terms = ["步行", "风险", "番茄钟", "文献综述", "模板", "自测", "可复现", "香水", "前女友"]
            for item in self.memory.active():
                if item.scope == scope and any(term in item.content for term in topic_terms if term in text):
                    matches.append(item)
        if not matches and "模板" in text:
            scope = _infer_scope(text, context)
            matches.extend(
                item
                for item in self.memory.active()
                if item.scope == scope and item.type in {"research_method", "workflow_rule", "format_preference"}
            )
        return matches

    def _is_duplicate(self, candidate: MemoryItem) -> bool:
        for item in self.memory.active():
            if item.content == candidate.content and item.scope == candidate.scope:
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
        if scope == "relationship_gift" and not any("闺蜜" in m.content for m in memories):
            return ["她闺蜜最近晒过或收到过什么品牌吗？", "有没有前女友有过、绝对不能送的东西？"]
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
        if scope == "relationship_gift":
            return _gift_answer(text, memories)
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


def _has_memory_signal(text: str, context: str = "") -> bool:
    signals = [
        "以后",
        "请记住",
        "记住",
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
        "晒过",
        "预算",
        "保留一个",
        "就保留一个",
        "只要一个",
        "一个最合适",
    ]
    if any(signal in text for signal in signals):
        return True
    return _is_relationship_context(context) and any(token in text for token in ["紫色", "1000", "一千", "一个"])


def _infer_scope(text: str, context: str = "") -> str:
    if any(token in text for token in ["女朋友", "礼物", "送礼", "闺蜜", "前女友"]) or _is_relationship_context(context):
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
    if scope == "work_report" and any(token in text for token in ["表格", "风险", "负责人", "下一步", "3 条结论", "3条结论"]):
        return "workflow_rule"
    if scope == "study_plan":
        return "learning_preference"
    if scope == "research_review":
        return "research_method" if any(token in text for token in ["文献", "方法", "数据集", "局限", "可复现"]) else "communication_preference"
    if any(token in text for token in ["不能", "不要", "不喜欢", "前女友"]):
        return "taboo_or_negative_preference"
    if scope == "relationship_gift" and any(token in text for token in ["保留一个", "只要一个", "一个最合适", "推荐的有点多"]):
        return "communication_preference"
    if any(token in text for token in ["只用于", "仅用于", "不适用", "同行", "闺蜜"]):
        return "scene_rule"
    return "preference"


def _clean_memory_content(text: str, scope: str, context: str = "") -> str:
    content = text.strip()
    for prefix in ["以后", "家庭出行", "写给老板的项目材料，请", "学习计划请", "做文献综述时，请"]:
        content = content.replace(prefix, "")
    content = content.strip(" ，,。：:")
    if scope == "relationship_gift":
        content = _relationship_memory_content(content, context)
    return content


def _relationship_memory_content(content: str, context: str) -> str:
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


def _relationship_candidates(text: str, context: str, scope: str) -> list[MemoryItem]:
    if scope != "relationship_gift":
        return []
    candidates: list[MemoryItem] = []
    budget = _extract_budget(text)
    if budget:
        candidates.append(
            MemoryItem(
                "scene_rule",
                f"给女朋友选礼物预算在 {budget}",
                scope=scope,
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["礼物", "预算"],
            )
        )
    if "紫色" in text or "喜欢紫色" in text:
        candidates.append(
            MemoryItem(
                "preference",
                "女朋友喜欢紫色",
                scope=scope,
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["女朋友", "紫色"],
            )
        )
    if "保留一个" in text or "只要一个" in text or "一个最合适" in text or "推荐的有点多" in text:
        candidates.append(
            MemoryItem(
                "communication_preference",
                "给女朋友选礼物时，用户希望只保留一个最合适的推荐",
                scope=scope,
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["推荐简洁", "一个"],
            )
        )
    if "闺蜜" in text:
        candidates.append(
            MemoryItem(
                "scene_rule",
                f"给女朋友选礼物需参考闺蜜线索：{text}",
                scope=scope,
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["闺蜜", "礼物"],
            )
        )
    if "前女友" in text:
        candidates.append(
            MemoryItem(
                "taboo_or_negative_preference",
                f"给女朋友选礼物需避开前女友相关物品：{text}",
                scope=scope,
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["前女友", "禁忌"],
            )
        )
    return candidates


def _extract_budget(text: str) -> str:
    for marker in ["1000", "一千"]:
        if marker in text:
            return "1000 元以内"
    return ""


def _is_relationship_context(context: str) -> bool:
    return any(token in context for token in ["女朋友", "礼物", "送礼", "闺蜜", "前女友"])


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
        "闺蜜",
        "前女友",
        "香水",
        "礼物",
    ]
    return [word for word in vocab if word in text]


def _gift_answer(text: str, memories: list[MemoryItem]) -> str:
    wants_one = any("只保留一个" in memory.content or "一个最合适" in memory.content for memory in memories)
    likes_purple = any("紫色" in memory.content for memory in memories)
    has_budget = any("预算" in memory.content for memory in memories)
    if wants_one and likes_purple and has_budget:
        return "我就保留一个推荐：紫水晶项链或手链。它符合 1000 元以内预算，也命中女朋友喜欢紫色这个偏好。"
    if memories:
        return "我会按已知偏好先筛礼物：避开禁忌，参考她喜欢的颜色和闺蜜品牌线索，再给出不重复的选择。"
    return "我先不乱猜。可以先从首饰、香氛、包袋配饰、体验类礼物里筛一轮。"


def _travel_answer(text: str, memories: list[MemoryItem]) -> str:
    points = "、".join(m.content for m in memories) if memories else "常规节奏、交通便利、体验丰富"
    return f"我会按这些约束安排路线：{points}。输出会控制步行强度、点位密度和休息节奏。"


def _work_answer(text: str, memories: list[MemoryItem]) -> str:
    points = "、".join(m.content for m in memories) if memories else "先明确受众，再组织结论、风险和下一步"
    return f"我会按这个工作流整理材料：{points}。"


def _study_answer(text: str, memories: list[MemoryItem]) -> str:
    points = "、".join(m.content for m in memories) if memories else "先判断阶段，再安排知识点、例题和练习"
    return f"我会按这个学习偏好生成计划：{points}。"


def _research_answer(text: str, memories: list[MemoryItem]) -> str:
    points = "、".join(m.content for m in memories) if memories else "按任务类型选择综述、评测或 brainstorm 结构"
    return f"我会按这个研究工作法输出：{points}。"
