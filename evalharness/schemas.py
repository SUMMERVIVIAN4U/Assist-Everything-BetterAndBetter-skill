from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]
    output: dict[str, Any]
    timestamp: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Message:
    role: str
    content: str
    timestamp: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TurnTrace:
    id: str
    stage: str
    user: Message
    assistant: Message
    tool_calls: list[ToolCall] = field(default_factory=list)
    applied_memories: list[str] = field(default_factory=list)
    relevant_memory_pack: dict[str, Any] = field(default_factory=dict)
    memory_snapshot: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["user"] = self.user.to_dict()
        data["assistant"] = self.assistant.to_dict()
        data["tool_calls"] = [call.to_dict() for call in self.tool_calls]
        return data


@dataclass
class HarnessSession:
    id: str = field(default_factory=lambda: f"session_{uuid4().hex[:8]}")
    messages: list[Message] = field(default_factory=list)
    turns: list[TurnTrace] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "messages": [message.to_dict() for message in self.messages],
            "turns": [turn.to_dict() for turn in self.turns],
        }
