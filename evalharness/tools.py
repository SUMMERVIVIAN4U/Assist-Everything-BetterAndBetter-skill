from __future__ import annotations

from typing import Any

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

    def process_message(self, message: str, context: str = "") -> tuple[SkillResponse, ToolCall]:
        response = self.skill.process_message(message, context=context)
        return response, self._call("process_message", {"message": message, "context": context}, response.to_dict())

    def snapshot(self) -> dict[str, Any]:
        return self.skill.memory.snapshot()

    def _call(self, name: str, input_data: dict[str, Any], output: dict[str, Any]) -> ToolCall:
        return ToolCall(name=name, input=input_data, output=output)
