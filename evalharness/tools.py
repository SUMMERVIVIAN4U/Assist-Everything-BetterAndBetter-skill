from __future__ import annotations

from typing import Any

from assist_everything_betterandbetter_skill.skill import AssistSkill, RetrievalIntentClassifier, SemanticExtractor, SkillResponse
from assist_everything_betterandbetter_skill.mem0_backend import Mem0Config

from .schemas import ToolCall


class MemoryToolbox:
    """Thin tool layer exposed to the harness agent."""

    def __init__(
        self,
        skill: AssistSkill | None = None,
        *,
        memory_dir: str | None = None,
        persist: bool | None = None,
        mem0_config: Mem0Config | None = None,
        memory_enabled: bool | None = None,
        memory_backend: str | None = None,
        semantic_extractor: SemanticExtractor | None = None,
        retrieval_intent_classifier: RetrievalIntentClassifier | None = None,
    ) -> None:
        self.skill = skill or AssistSkill(
            memory_dir=memory_dir,
            persist=persist,
            mem0_config=mem0_config,
            memory_enabled=memory_enabled,
            memory_backend=memory_backend,
            semantic_extractor=semantic_extractor,
            retrieval_intent_classifier=retrieval_intent_classifier,
        )

    @property
    def memory_enabled(self) -> bool:
        return self.skill.memory_enabled

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
        return self.skill.snapshot()

    def _call(self, name: str, input_data: dict[str, Any], output: dict[str, Any]) -> ToolCall:
        return ToolCall(name=name, input=input_data, output=output)
