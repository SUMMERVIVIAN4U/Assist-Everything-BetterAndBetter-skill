from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


ACTIVE = "active"
SUPERSEDED = "superseded"
ARCHIVED = "archived"
DELETED = "deleted"


@dataclass
class MemoryItem:
    type: str
    content: str
    scope: str
    source: str = "explicit_feedback"
    confidence: float = 0.9
    status: str = ACTIVE
    evidence: list[str] = field(default_factory=list)
    applies_when: list[str] = field(default_factory=list)
    user_approved: bool = True
    tags: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"mem_{uuid4().hex[:8]}")
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    supersedes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryStore:
    """Small in-memory store with explicit state transitions for eval reproducibility."""

    def __init__(self) -> None:
        self.items: list[MemoryItem] = []
        self.events: list[dict[str, Any]] = []
        self.version = 0

    def reset(self) -> dict[str, Any]:
        self.items.clear()
        self.events.clear()
        self.version = 0
        return self._event("reset", None, "Memory reset to M0")

    def add(self, item: MemoryItem) -> MemoryItem:
        self.items.append(item)
        self.version += 1
        self._event("add", item.id, item.content)
        return item

    def update(self, memory_id: str, **changes: Any) -> MemoryItem | None:
        item = self.get(memory_id, include_inactive=True)
        if not item:
            return None
        for key, value in changes.items():
            if hasattr(item, key):
                setattr(item, key, value)
        item.updated_at = datetime.now(timezone.utc).isoformat()
        self.version += 1
        self._event("update", item.id, item.content)
        return item

    def downgrade(self, memory_id: str, reason: str = "") -> MemoryItem | None:
        item = self.get(memory_id, include_inactive=True)
        if not item:
            return None
        item.status = SUPERSEDED
        item.confidence = min(item.confidence, 0.45)
        item.updated_at = datetime.now(timezone.utc).isoformat()
        self.version += 1
        self._event("downgrade", item.id, reason or item.content)
        return item

    def archive(self, memory_id: str, reason: str = "") -> MemoryItem | None:
        item = self.get(memory_id, include_inactive=True)
        if not item:
            return None
        item.status = ARCHIVED
        item.updated_at = datetime.now(timezone.utc).isoformat()
        self.version += 1
        self._event("archive", item.id, reason or item.content)
        return item

    def delete(self, memory_id_or_query: str, reason: str = "user_requested_delete") -> list[MemoryItem]:
        matches = self.find(memory_id_or_query, include_inactive=True)
        if not matches:
            normalized = _normalize_query(memory_id_or_query)
            matches = self.find(normalized, include_inactive=True)
        matches = [item for item in matches if item.status != DELETED]
        for item in matches:
            item.status = DELETED
            item.updated_at = datetime.now(timezone.utc).isoformat()
            self._event("delete", item.id, reason)
        if matches:
            self.version += 1
        return matches

    def get(self, memory_id: str, include_inactive: bool = False) -> MemoryItem | None:
        for item in self.items:
            if item.id == memory_id and (include_inactive or item.status == ACTIVE):
                return item
        return None

    def find(self, query: str = "", include_inactive: bool = False) -> list[MemoryItem]:
        q = _normalize_query(query)
        results = []
        for item in self.items:
            if not include_inactive and item.status != ACTIVE:
                continue
            haystack = _normalize_query(" ".join([item.id, item.type, item.scope, item.content, *item.tags]))
            parts = [part for part in q.split() if len(part) >= 2]
            compact_hit = q and q in haystack
            token_hit = parts and any(part in haystack for part in parts)
            char_hit = q and len(q) >= 4 and any(q[i : i + 4] in haystack for i in range(max(1, len(q) - 3)))
            if not q or compact_hit or token_hit or char_hit:
                results.append(item)
        return results

    def active(self) -> list[MemoryItem]:
        return [item for item in self.items if item.status == ACTIVE]

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": f"M{self.version}",
            "active": [m.to_dict() for m in self.items if m.status == ACTIVE],
            "superseded": [m.to_dict() for m in self.items if m.status == SUPERSEDED],
            "archived": [m.to_dict() for m in self.items if m.status == ARCHIVED],
            "deleted": [m.to_dict() for m in self.items if m.status == DELETED],
        }

    def _event(self, action: str, memory_id: str | None, detail: str) -> dict[str, Any]:
        event = {
            "action": action,
            "memory_id": memory_id,
            "detail": detail,
            "version": f"M{self.version}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.events.append(event)
        return event


def _normalize_query(text: str) -> str:
    lowered = text.lower().strip()
    for token in [
        "删除",
        "忘掉",
        "这条记忆",
        "这条",
        "记忆",
        "。此前",
        "。",
        "，",
        ",",
        "：",
        ":",
        "“",
        "”",
        "\"",
        "'",
    ]:
        lowered = lowered.replace(token, " ")
    aliases = {
        "孩子喜欢动物": "孩子 动物",
        "每天 5 道自测题": "5道自测 自测",
        "每天5道自测题": "5道自测 自测",
        "先给 3 条结论": "3条结论 结论",
        "先给3条结论": "3条结论 结论",
        "每篇都标可复现性": "可复现性",
    }
    for src, dst in aliases.items():
        lowered = lowered.replace(src, dst)
    return " ".join(lowered.split())
