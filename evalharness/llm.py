from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .env import load_env


@dataclass(frozen=True)
class MimoConfig:
    api_key: str
    base_url: str
    model: str
    timeout: float = 60.0

    @classmethod
    def from_env(cls) -> "MimoConfig":
        load_env()
        api_key = os.getenv("MIMO_API_KEY", "").strip()
        if not api_key:
            raise ValueError("MIMO_API_KEY is not configured")
        return cls(
            api_key=api_key,
            base_url=os.getenv("MIMO_BASE_URL", "https://api.mimo.chat/v1").rstrip("/"),
            model=os.getenv("MIMO_MODEL", "mimo-v1"),
            timeout=float(os.getenv("MIMO_TIMEOUT", "60")),
        )


class MimoClient:
    """Minimal OpenAI-compatible chat client for Mimo-style LLM endpoints."""

    def __init__(self, config: MimoConfig | None = None) -> None:
        self.config = config or MimoConfig.from_env()

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        response_format: dict[str, str] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format:
            payload["response_format"] = response_format
        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Mimo HTTP {exc.code}: {body}") from exc
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            raise RuntimeError(
                f"Mimo request timed out or failed after {self.config.timeout:g}s "
                f"for model {self.config.model} at {self.config.base_url}: {exc}"
            ) from exc
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise RuntimeError("Mimo response content is not a string")
        return content

    def json_chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> dict[str, Any]:
        content = self.chat(
            messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        return _parse_json_object(content)


def mimo_configured() -> bool:
    load_env()
    return bool(os.getenv("MIMO_API_KEY", "").strip())


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start : end + 1])
        raise
