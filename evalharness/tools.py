from __future__ import annotations

from typing import Any

from assist_everything_betterandbetter_skill.cases import EvalCase
from assist_everything_betterandbetter_skill.skill import AssistSkill, SkillResponse

from .schemas import ToolCall


class MemoryToolbox:
    """Thin tool layer exposed to the harness agent."""

    def __init__(self, skill: AssistSkill | None = None) -> None:
        self.skill = skill or AssistSkill()

    def reset_memory(self) -> tuple[SkillResponse, ToolCall]:
        response = self.skill.reset_memory()
        return response, self._call("reset_memory", {}, response.to_dict())

    def show_memory(self) -> tuple[SkillResponse, ToolCall]:
        response = self.skill.show_memory()
        return response, self._call("show_memory", {}, response.to_dict())

    def manage_memory(self, command: str) -> tuple[SkillResponse, ToolCall]:
        response = self.skill.manage_memory(command)
        return response, self._call("manage_memory", {"command": command}, response.to_dict())

    def first_task(self, case: EvalCase) -> tuple[SkillResponse, ToolCall]:
        response = self.skill.first_task(case)
        return response, self._call("answer_task", {"case_id": case.id, "round": 1}, response.to_dict())

    def learn_feedback(self, case: EvalCase) -> tuple[SkillResponse, ToolCall]:
        response = self.skill.learn_feedback(case)
        return response, self._call("learn_feedback_with_consent", {"case_id": case.id}, response.to_dict())

    def second_task(self, case: EvalCase) -> tuple[SkillResponse, ToolCall]:
        response = self.skill.second_task(case)
        return response, self._call("answer_task_with_memory", {"case_id": case.id, "round": 2}, response.to_dict())

    def update_preferences(self, case: EvalCase) -> tuple[SkillResponse, ToolCall]:
        response = self.skill.update_preferences(case)
        return response, self._call("update_memory_policy", {"case_id": case.id}, response.to_dict())

    def third_task(self, case: EvalCase) -> tuple[SkillResponse, ToolCall]:
        response = self.skill.third_task(case)
        return response, self._call("answer_task_with_updated_memory", {"case_id": case.id, "round": 3}, response.to_dict())

    def delete_and_retest(self, case: EvalCase) -> tuple[SkillResponse, ToolCall]:
        response = self.skill.delete_and_retest(case)
        return response, self._call("delete_memory_and_retest", {"case_id": case.id, "query": case.delete_query}, response.to_dict())

    def snapshot(self) -> dict[str, Any]:
        return self.skill.memory.snapshot()

    def _call(self, name: str, input_data: dict[str, Any], output: dict[str, Any]) -> ToolCall:
        return ToolCall(name=name, input=input_data, output=output)
