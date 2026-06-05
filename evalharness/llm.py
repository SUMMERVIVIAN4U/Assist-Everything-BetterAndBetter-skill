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
class OpenAICompatibleConfig:
    provider: str
    api_key: str
    base_url: str
    model: str
    timeout: float = 60.0


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


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str
    base_url: str
    model: str
    timeout: float = 60.0

    @classmethod
    def from_env(cls, variant: str = "flash") -> "DeepSeekConfig":
        load_env()
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is not configured")
        return cls(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/"),
            model=_deepseek_model_from_env(variant),
            timeout=float(os.getenv("DEEPSEEK_TIMEOUT", "60")),
        )


class OpenAICompatibleClient:
    """Minimal OpenAI-compatible chat client."""

    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.config = config

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
            raise RuntimeError(f"{self.config.provider} HTTP {exc.code}: {body}") from exc
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            raise RuntimeError(
                f"{self.config.provider} request timed out or failed after {self.config.timeout:g}s "
                f"for model {self.config.model} at {self.config.base_url}: {exc}"
            ) from exc
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise RuntimeError(f"{self.config.provider} response content is not a string")
        return content

    def json_chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> dict[str, Any]:
        content = self.chat(
            messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        return _parse_json_object(content)


class MimoClient(OpenAICompatibleClient):
    """OpenAI-compatible chat client for Mimo."""

    def __init__(self, config: MimoConfig | None = None) -> None:
        raw = config or MimoConfig.from_env()
        super().__init__(OpenAICompatibleConfig("Mimo", raw.api_key, raw.base_url, raw.model, raw.timeout))


class DeepSeekClient(OpenAICompatibleClient):
    """OpenAI-compatible chat client for DeepSeek."""

    def __init__(self, config: DeepSeekConfig | None = None, *, variant: str = "flash") -> None:
        raw = config or DeepSeekConfig.from_env(variant)
        super().__init__(OpenAICompatibleConfig("DeepSeek", raw.api_key, raw.base_url, raw.model, raw.timeout))


def mimo_configured() -> bool:
    load_env()
    return bool(os.getenv("MIMO_API_KEY", "").strip())


def deepseek_configured() -> bool:
    load_env()
    return bool(os.getenv("DEEPSEEK_API_KEY", "").strip())


def llm_configured() -> bool:
    return mimo_configured() or deepseek_configured()


def provider_configured(mode: str) -> bool:
    if mode == "mimo":
        return mimo_configured()
    if mode in {"deepseek", "deepseek-flash", "deepseek-pro"}:
        return deepseek_configured()
    if mode == "auto":
        return llm_configured()
    return False


def provider_env_key(mode: str) -> str:
    if mode.startswith("deepseek"):
        return "DEEPSEEK"
    if mode == "auto" and deepseek_configured() and not mimo_configured():
        return "DEEPSEEK"
    return "MIMO"


def build_llm_config(mode: str = "auto", *, timeout: float | None = None) -> OpenAICompatibleConfig:
    selected = _resolve_provider_mode(mode)
    if selected == "mimo":
        raw_mimo = MimoConfig.from_env()
        return OpenAICompatibleConfig(
            "Mimo",
            raw_mimo.api_key,
            raw_mimo.base_url,
            raw_mimo.model,
            raw_mimo.timeout if timeout is None else timeout,
        )
    variant = "pro" if selected == "deepseek-pro" else "flash"
    raw_deepseek = DeepSeekConfig.from_env(variant)
    return OpenAICompatibleConfig(
        "DeepSeek",
        raw_deepseek.api_key,
        raw_deepseek.base_url,
        raw_deepseek.model,
        raw_deepseek.timeout if timeout is None else timeout,
    )


def build_llm_client(mode: str = "auto", *, timeout: float | None = None) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(build_llm_config(mode, timeout=timeout))


def _resolve_provider_mode(mode: str) -> str:
    if mode == "auto":
        if mimo_configured():
            return "mimo"
        if deepseek_configured():
            return "deepseek-flash"
        raise ValueError("No LLM provider is configured")
    if mode == "deepseek":
        return "deepseek-flash"
    if mode in {"mimo", "deepseek-flash", "deepseek-pro"}:
        return mode
    raise ValueError(f"Unsupported LLM provider mode: {mode}")


def _deepseek_model_from_env(variant: str) -> str:
    if variant == "pro":
        return os.getenv("DEEPSEEK_PRO_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"))
    return os.getenv("DEEPSEEK_FLASH_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"))


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start : end + 1])
        raise
