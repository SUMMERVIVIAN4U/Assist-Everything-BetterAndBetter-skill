from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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
    """Memory store with optional Markdown persistence."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        self.storage_dir = Path(storage_dir) if storage_dir else None
        self.items: list[MemoryItem] = []
        self.events: list[dict[str, Any]] = []
        self.version = 0
        if self.storage_dir:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            self._load()

    def reset(self) -> dict[str, Any]:
        self.items.clear()
        self.events.clear()
        self.version = 0
        if self.storage_dir:
            for path in self.storage_dir.glob("*.md"):
                path.unlink()
        event = self._event("reset", None, "Memory reset to M0")
        self._persist_state()
        return event

    def add(self, item: MemoryItem) -> MemoryItem:
        self.items.append(item)
        self.version += 1
        self._event("add", item.id, item.content)
        self._persist_item(item)
        self._persist_state()
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
        self._persist_item(item)
        self._persist_state()
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
        self._persist_item(item)
        self._persist_state()
        return item

    def archive(self, memory_id: str, reason: str = "") -> MemoryItem | None:
        item = self.get(memory_id, include_inactive=True)
        if not item:
            return None
        item.status = ARCHIVED
        item.updated_at = datetime.now(timezone.utc).isoformat()
        self.version += 1
        self._event("archive", item.id, reason or item.content)
        self._persist_item(item)
        self._persist_state()
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
            self._persist_item(item)
        if matches:
            self.version += 1
            self._persist_state()
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
        self._persist_event(event)
        return event

    def _load(self) -> None:
        if not self.storage_dir:
            return
        state = self.storage_dir / "_state.json"
        if state.exists():
            try:
                self.version = int(json.loads(state.read_text(encoding="utf-8")).get("version", 0))
            except (json.JSONDecodeError, ValueError):
                self.version = 0
        for path in sorted(self.storage_dir.glob("*.md")):
            if path.name.startswith("_"):
                continue
            try:
                data = _read_memory_markdown(path)
            except ValueError:
                continue
            self.items.append(MemoryItem(**data))

    def _persist_item(self, item: MemoryItem) -> None:
        if not self.storage_dir:
            return
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        path = self.storage_dir / f"{item.id}-{_slug(item.scope)}-{_slug(item.type)}.md"
        path.write_text(_memory_markdown(item), encoding="utf-8")

    def _persist_event(self, event: dict[str, Any]) -> None:
        if not self.storage_dir:
            return
        log_path = self.storage_dir / "_events.jsonl"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _persist_state(self) -> None:
        if not self.storage_dir:
            return
        state = {"version": self.version, "updated_at": datetime.now(timezone.utc).isoformat()}
        (self.storage_dir / "_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _memory_markdown(item: MemoryItem) -> str:
    data = item.to_dict()
    lines = ["---"]
    for key, value in data.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.extend(["---", "", f"# {item.id}", "", item.content, "", "## Evidence"])
    for evidence in item.evidence:
        lines.append(f"- {evidence}")
    return "\n".join(lines) + "\n"


def _read_memory_markdown(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(path)
    _, frontmatter, _ = text.split("---", 2)
    data: dict[str, Any] = {}
    for line in frontmatter.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, raw = line.split(":", 1)
        data[key.strip()] = json.loads(raw.strip())
    return data


def _slug(text: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z_-]+", "-", text).strip("-").lower()
    return slug or "memory"


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
