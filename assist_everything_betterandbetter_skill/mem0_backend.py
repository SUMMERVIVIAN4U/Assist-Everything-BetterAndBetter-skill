from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from .memory import ACTIVE, MemoryItem


@dataclass
class Mem0Config:
    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    user_id: str = "workbench-user"
    app_id: str = "assist-everything-betterandbetter-skill"
    project_id: str = ""
    project_name: str = ""
    timeout: float = 15.0

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.base_url and self.api_key and self.user_id)

    def public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "api_key_configured": bool(self.api_key),
            "user_id": self.user_id,
            "app_id": self.app_id,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "timeout": self.timeout,
        }


class Mem0Client:
    """Small REST client for Mem0-compatible hosted memory projects."""

    def __init__(self, config: Mem0Config) -> None:
        self.config = config

    def add(self, item: MemoryItem) -> dict[str, Any]:
        payload = {
            "user_id": self.config.user_id,
            "app_id": self.config.app_id,
            "messages": [{"role": "user", "content": item.content}],
            "metadata": {
                "local_memory_id": item.id,
                "type": item.type,
                "scope": item.scope,
                "status": item.status,
                "source": item.source,
                "confidence": item.confidence,
                "applies_when": item.applies_when,
                "project_id": self.config.project_id,
                "assist_memory": item.to_dict(),
            },
            "infer": False,
            "async_mode": False,
        }
        return self._request_first("POST", ["/v1/memories/", "/v3/memories/add/"], payload)

    def search(self, query: str, *, top_k: int = 10) -> list[MemoryItem]:
        if not query.strip():
            return []
        payload = {
            "query": query,
            "user_id": self.config.user_id,
            "top_k": max(1, min(top_k, 50)),
            "threshold": 0.0,
        }
        data = self._request_first("POST", ["/v2/memories/search/", "/v1/memories/search/", "/v3/memories/search/"], payload)
        return [_item_from_mem0_result(result) for result in _mem0_results(data)]

    def get_all(self, *, page_size: int = 50) -> dict[str, Any]:
        query = urlencode({"page": 1, "page_size": max(1, min(page_size, 200))})
        return self._request_first(
            "GET",
            [f"/v1/memories/?user_id={self.config.user_id}&{query}", f"/v2/memories/?user_id={self.config.user_id}&{query}", f"/v3/memories/?user_id={self.config.user_id}&{query}"],
            None,
        )

    def delete(self, memory_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/v1/memories/{memory_id}/", None)

    def health(self) -> dict[str, Any]:
        if not self.config.ready:
            return {"ok": False, "stage": "config", "error": "Mem0 is not fully configured"}
        data = self.search("health check", top_k=1)
        return {"ok": True, "stage": "search", "result_count": len(data)}

    def _request(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        url = self.config.base_url.rstrip("/") + path
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Authorization": f"Token {self.config.api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {"status": response.status}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Mem0 {method} {path} failed: HTTP {exc.code} {raw[:500]}") from exc
        except Exception as exc:
            raise RuntimeError(f"Mem0 {method} {path} failed: {exc}") from exc

    def _request_first(self, method: str, paths: list[str], payload: dict[str, Any] | None) -> dict[str, Any] | list[Any]:
        errors = []
        for path in paths:
            try:
                return self._request(method, path, payload)
            except RuntimeError as exc:
                errors.append(str(exc))
                if "HTTP 404" not in str(exc):
                    break
        raise RuntimeError(errors[-1] if errors else f"Mem0 {method} failed")


def _mem0_results(data: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ["results", "memories", "data"]:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _item_from_mem0_result(result: dict[str, Any]) -> MemoryItem:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    stored = metadata.get("assist_memory") if isinstance(metadata.get("assist_memory"), dict) else {}
    content = str(result.get("memory") or result.get("text") or stored.get("content") or "")
    item = MemoryItem(
        type=str(stored.get("type") or metadata.get("type") or "preference"),
        content=content,
        scope=str(stored.get("scope") or metadata.get("scope") or "general"),
        subject=str(stored.get("subject") or ""),
        target=str(stored.get("target") or ""),
        object=str(stored.get("object") or ""),
        predicate=str(stored.get("predicate") or ""),
        source=str(stored.get("source") or "mem0"),
        confidence=float(stored.get("confidence") or result.get("score") or 0.8),
        status=str(stored.get("status") or ACTIVE),
        evidence=list(stored.get("evidence") or []),
        applies_when=list(stored.get("applies_when") or metadata.get("applies_when") or []),
        user_approved=bool(stored.get("user_approved", True)),
        tags=list(stored.get("tags") or result.get("categories") or []),
        validity=dict(stored.get("validity") or {}),
        id=str(stored.get("id") or f"mem0_{result.get('id', '')}"),
        created_at=str(stored.get("created_at") or result.get("created_at") or ""),
        updated_at=str(stored.get("updated_at") or result.get("updated_at") or ""),
        supersedes=list(stored.get("supersedes") or []),
    )
    item.validity["mem0_id"] = str(result.get("id") or "")
    item.validity["mem0_score"] = result.get("score")
    return item


def config_from_dict(data: dict[str, Any]) -> Mem0Config:
    return Mem0Config(
        enabled=bool(data.get("enabled")),
        base_url=str(data.get("base_url") or ""),
        api_key=str(data.get("api_key") or ""),
        user_id=str(data.get("user_id") or "workbench-user"),
        app_id=str(data.get("app_id") or "assist-everything-betterandbetter-skill"),
        project_id=str(data.get("project_id") or ""),
        project_name=str(data.get("project_name") or ""),
        timeout=float(data.get("timeout") or 15.0),
    )
