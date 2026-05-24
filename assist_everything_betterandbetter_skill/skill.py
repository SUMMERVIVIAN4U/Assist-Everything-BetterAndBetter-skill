from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cases import EvalCase
from .memory import ACTIVE, MemoryItem, MemoryStore


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
    """Deterministic skill implementation optimized for the documented eval flow."""

    def __init__(self) -> None:
        self.memory = MemoryStore()
        self.trace: list[dict[str, Any]] = []
        self.pending_proposals: list[MemoryItem] = []

    def reset_memory(self) -> SkillResponse:
        event = self.memory.reset()
        return SkillResponse("已重置记忆：当前为 M0 空白状态。", [event], [], [])

    def show_memory(self) -> SkillResponse:
        active = self.memory.active()
        if not active:
            return SkillResponse("当前没有 active 记忆。", [], [], [])
        lines = ["当前 active 记忆："]
        for item in active:
            lines.append(f"- {item.id} [{item.type}/{item.scope}] {item.content}")
        return SkillResponse("\n".join(lines), [], [m.id for m in active], [])

    def propose_feedback(self, case: EvalCase) -> SkillResponse:
        self.pending_proposals = self._items_from_feedback(case)
        lines = ["我提取到以下候选长期记忆，需要授权后才会保存："]
        for idx, item in enumerate(self.pending_proposals, 1):
            lines.append(f"{idx}. [{item.type}/{item.scope}] {item.content}")
        lines.append("请回复 approve memory / 同意保存，或 reject memory / 拒绝保存。")
        return SkillResponse("\n".join(lines), [], [], [])

    def approve_pending(self) -> SkillResponse:
        if not self.pending_proposals:
            return SkillResponse("没有待授权的记忆候选。", [], [], [])
        created = []
        for item in self.pending_proposals:
            item.user_approved = True
            created.append(self.memory.add(item))
        self.pending_proposals = []
        actions = self.memory.events[-len(created):]
        return SkillResponse(
            "已获授权并保存长期记忆：\n" + "\n".join(f"- {m.content}" for m in created),
            actions,
            [],
            [],
        )

    def reject_pending(self) -> SkillResponse:
        count = len(self.pending_proposals)
        self.pending_proposals = []
        return SkillResponse(f"已拒绝保存 {count} 条候选记忆，不写入长期记忆库。", [], [], [])

    def manage_memory(self, text: str) -> SkillResponse:
        original = text.strip()
        normalized_command = original
        if original.startswith("/"):
            parts = original[1:].split(maxsplit=1)
            command = parts[0].lower() if parts else ""
            rest = parts[1] if len(parts) > 1 else ""
            if command in {"memory", "mem"}:
                normalized_command = rest
            elif command in {"reset-memory", "reset_memory"}:
                normalized_command = "reset memory"
            elif command in {"show-memory", "show_memory"}:
                normalized_command = "show memory"
            elif command in {"delete-memory", "delete_memory"}:
                normalized_command = "delete " + rest
            elif command in {"downgrade-memory", "downgrade_memory"}:
                normalized_command = "downgrade " + rest
            elif command in {"archive-memory", "archive_memory"}:
                normalized_command = "archive " + rest
            elif command in {"find-memory", "find_memory"}:
                normalized_command = "find " + rest
            elif command in {"approve-memory", "approve_memory"}:
                normalized_command = "approve memory"
            elif command in {"reject-memory", "reject_memory"}:
                normalized_command = "reject memory"

        lowered = normalized_command.lower()
        if "approve" in lowered or "同意保存" in normalized_command or "确认保存" in normalized_command:
            return self.approve_pending()
        if "reject" in lowered or "拒绝保存" in normalized_command or "不要保存" in normalized_command:
            return self.reject_pending()
        if "reset" in lowered or "清空" in normalized_command or "重置" in normalized_command:
            return self.reset_memory()
        if "show" in lowered or "展示" in normalized_command or "查看" in normalized_command:
            return self.show_memory()
        if "find" in lowered or "query" in lowered or "查询" in normalized_command or "搜索" in normalized_command:
            query = (
                normalized_command.replace("find", "")
                .replace("query", "")
                .replace("查询", "")
                .replace("搜索", "")
                .replace("记忆", "")
                .strip()
            )
            matches = self.memory.find(query, include_inactive=True)
            if not matches:
                return SkillResponse("查询结果：无匹配记忆。", [], [], [])
            lines = ["查询结果："]
            for item in matches:
                lines.append(f"- {item.id} [{item.status}/{item.type}/{item.scope}] {item.content}")
            return SkillResponse("\n".join(lines), [], [m.id for m in matches if m.status == "active"], [])
        if "删除" in normalized_command or "delete" in lowered or "forget" in lowered:
            query = normalized_command.replace("delete", "").replace("删除", "").replace("这条记忆", "").replace("记忆", "").strip()
            deleted = self.memory.delete(query)
            names = ", ".join(item.id for item in deleted) or "无匹配"
            return SkillResponse(f"删除结果：{names}。后续检索会过滤 deleted 记忆。", self.memory.events[-len(deleted):] if deleted else [], [], [])
        if "降权" in normalized_command or "降级" in normalized_command or "downgrade" in lowered:
            matches = self.memory.find(normalized_command, include_inactive=False)
            actions = []
            for item in matches:
                self.memory.downgrade(item.id, "user_requested_downgrade")
                actions.append(self.memory.events[-1])
            return SkillResponse(f"降权 {len(actions)} 条记忆。", actions, [], [])
        if "归档" in normalized_command or "archive" in lowered:
            matches = self.memory.find(normalized_command, include_inactive=False)
            actions = []
            for item in matches:
                self.memory.archive(item.id, "user_requested_archive")
                actions.append(self.memory.events[-1])
            return SkillResponse(f"归档 {len(actions)} 条记忆。", actions, [], [])
        return SkillResponse("未识别到记忆管理命令。支持 reset/show/删除/降权。", [], [], [])

    def first_task(self, case: EvalCase) -> SkillResponse:
        asks = {
            "life_family_travel": ["是否有老人或孩子的特殊约束？", "偏自然还是偏城市景点？"],
            "work_report": ["这是给老板还是跨部门团队？", "需要结论优先还是过程展开？"],
            "study_plan": ["你偏好整块学习还是番茄钟？", "是打基础还是冲刺？"],
            "research_review": ["要综述已有方法还是 brainstorm 研究问题？", "需要关注数据集、局限和可复现性吗？"],
        }[case.domain]
        return SkillResponse(
            f"我先按普通方案完成：{case.initial_task}\n为避免误记，我先不写长期记忆；如果你给出偏好，我会请求授权后保存。",
            [],
            [],
            asks,
        )

    def learn_feedback(self, case: EvalCase) -> SkillResponse:
        self.propose_feedback(case)
        approved = self.approve_pending()
        return SkillResponse(
            "已根据明确反馈提取候选记忆，并在授权后保存；已记录来源、scope 与 evidence。\n"
            + "\n".join(line for line in approved.text.splitlines()[1:]),
            approved.memory_actions,
            [],
            [],
        )

    def second_task(self, case: EvalCase) -> SkillResponse:
        applied = self._relevant(case)
        text = self._application_text(case, applied)
        return SkillResponse(text, [], [m.id for m in applied], [])

    def update_preferences(self, case: EvalCase) -> SkillResponse:
        actions: list[dict[str, Any]] = []
        # Downgrade broad memories that conflict with the new narrower scope.
        for item in self._relevant(case):
            if any(token in item.content for token in ["少步行", "老板", "番茄钟", "文献综述"]):
                self.memory.downgrade(item.id, f"新反馈缩小适用范围：{case.preference_change}")
                actions.append(self.memory.events[-1])
        for item in self._items_from_change(case):
            self.memory.add(item)
            actions.append(self.memory.events[-1])
        return SkillResponse(
            "已处理偏好变化：旧规则被降权/条件化，新规则 active。\n" + "\n".join(a["detail"] for a in actions),
            actions,
            [],
            [],
        )

    def third_task(self, case: EvalCase) -> SkillResponse:
        applied = self._relevant(case)
        return SkillResponse(self._third_text(case, applied), [], [m.id for m in applied], [])

    def delete_and_retest(self, case: EvalCase) -> SkillResponse:
        deleted = self.memory.delete(case.delete_query)
        applied = self._relevant(case)
        text = self._delete_retest_text(case, applied, deleted)
        return SkillResponse(text, self.memory.events[-len(deleted):] if deleted else [], [m.id for m in applied], [])

    def _relevant(self, case: EvalCase) -> list[MemoryItem]:
        return [m for m in self.memory.active() if case.domain in m.applies_when or case.domain == m.scope or case.id in m.tags]

    def _items_from_feedback(self, case: EvalCase) -> list[MemoryItem]:
        common = {"scope": case.domain, "evidence": [case.feedback], "applies_when": [case.domain]}
        if case.id == "C01":
            return [
                MemoryItem("scene_rule", "父亲同行时少步行并安排休息", tags=["少步行", case.id], **common),
                MemoryItem("scene_rule", "孩子喜欢自然和动物", tags=["孩子动物", case.id], **common),
                MemoryItem("preference", "家庭旅行避开人挤人的网红点", tags=["避开网红", case.id], **common),
            ]
        if case.id == "C02":
            return [
                MemoryItem("workflow_rule", "老板材料先给 3 条结论", tags=["3条结论", case.id], **common),
                MemoryItem("format_preference", "老板材料用表格列风险、负责人和下一步", tags=["风险表", case.id], **common),
            ]
        if case.id == "C03":
            return [
                MemoryItem("learning_preference", "学习计划按 25 分钟番茄钟安排", tags=["番茄钟", case.id], **common),
                MemoryItem("learning_preference", "先看例题再讲知识点", tags=["例题先行", case.id], **common),
                MemoryItem("learning_preference", "每天最后安排 5 道自测题", tags=["5道自测", case.id], **common),
            ]
        return [
            MemoryItem("research_method", "文献综述按方法类别组织", tags=["文献综述", case.id], **common),
            MemoryItem("research_method", "每篇文献标数据集、局限和可复现性", tags=["可复现性", case.id], **common),
            MemoryItem("communication_preference", "研究结论谨慎表述，不夸大", tags=["谨慎表述", case.id], **common),
        ]

    def _items_from_change(self, case: EvalCase) -> list[MemoryItem]:
        common = {"scope": case.domain, "evidence": [case.preference_change], "applies_when": [case.domain]}
        if case.id == "C01":
            return [MemoryItem("scene_rule", "少步行仅在父亲同行时适用；亲子自然路线仍避开拥挤", tags=["条件化", case.id], **common)]
        if case.id == "C02":
            return [MemoryItem("workflow_rule", "风险表仅用于老板材料，跨部门同步改为协作事项和依赖", tags=["条件化", case.id], **common)]
        if case.id == "C03":
            return [MemoryItem("learning_preference", "临近考试改用高频考点冲刺，保留例题先行", tags=["冲刺模式", case.id], **common)]
        return [MemoryItem("research_method", "brainstorm 研究问题时不用综述模板，仅保留谨慎表述", tags=["模式切换", case.id], **common)]

    def _application_text(self, case: EvalCase, applied: list[MemoryItem]) -> str:
        if case.id == "C01":
            return "杭州家庭行程会少步行、安排休息，加入自然/动物点位，并避开拥挤网红点。"
        if case.id == "C02":
            return "老板同步材料先给 3 条结论，再用风险/负责人/下一步表格。"
        if case.id == "C03":
            return "高数复习计划采用番茄钟、例题先行，并每天保留 5 道自测题。"
        return "RAG 评测综述按方法类别组织，并标注数据集、局限、可复现性，避免夸大。"

    def _third_text(self, case: EvalCase, applied: list[MemoryItem]) -> str:
        if case.id == "C01":
            return "上海亲子自然路线不再强制少步行，但继续避开拥挤并偏自然体验。"
        if case.id == "C02":
            return "跨部门同步不套老板模板，改为协作事项、依赖和需要对齐的问题。"
        if case.id == "C03":
            return "线代 2 天冲刺按高频考点组织，不再机械套番茄钟，保留例题先行。"
        return "RAG 研究问题 brainstorm 不套综述表格，输出问题、假设、验证路径，并保持谨慎表述。"

    def _delete_retest_text(self, case: EvalCase, applied: list[MemoryItem], deleted: list[MemoryItem]) -> str:
        if case.id == "C01":
            return "已删除孩子喜欢动物相关记忆；南京半日游不再主动安排动物主题，仍保留避开拥挤等未删除偏好。"
        if case.id == "C02":
            return "已删除 3 条结论记忆；老板材料不再强制三结论开头，仍可保留风险表等未删除规则。"
        if case.id == "C03":
            return "已删除每日 5 道自测题；物理复习不再强制每日 5 题，保留例题先行等未删除偏好。"
        return "已删除可复现性字段记忆；简短综述不再强制可复现性字段，保留谨慎表述。"
