from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from .env import load_env

DEFAULT_LLM_PROVIDER = "minimax"
LLM_PROVIDER_LABELS = {
    "minimax": "MiniMax",
    "deepseek_pro": "DeepSeek V4 Pro",
    "deepseek_flash": "DeepSeek V4 Flash",
}
PUBLIC_LLM_PROVIDERS = ("minimax",)
LLM_PROVIDER_ALIASES = {
    "deepseek": "deepseek_pro",
    "deepseek-pro": "deepseek_pro",
    "deepseek_v4_pro": "deepseek_pro",
    "deepseek-v4-pro": "deepseek_pro",
    "deepseek-flash": "deepseek_flash",
    "deepseek_v4_flash": "deepseek_flash",
    "deepseek-v4-flash": "deepseek_flash",
    "minimax": "minimax",
    "mini_max": "minimax",
    "mini-max": "minimax",
    "mimo": "minimax",
}


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    timeout: float = 60.0
    provider: str = "custom"
    label: str = "LLM"


@dataclass(frozen=True)
class MimoConfig(LLMConfig):
    """Backward-compatible MiniMax/Mimo config wrapper."""

    @classmethod
    def from_env(cls) -> "MimoConfig":
        load_env()
        api_key = (os.getenv("MINIMAX_API_KEY") or os.getenv("MIMO_API_KEY") or "").strip()
        if not api_key:
            raise ValueError("MINIMAX_API_KEY is not configured")
        return cls(
            api_key=api_key,
            base_url=(
                os.getenv("MINIMAX_BASE_URL")
                or os.getenv("MINIMAX_API_BASE")
                or os.getenv("MIMO_BASE_URL")
                or "https://api.minimax.io/v1"
            ).rstrip("/"),
            model=(os.getenv("MINIMAX_MODEL") or os.getenv("MIMO_MODEL") or "MiniMax-M2.7").strip(),
            timeout=float(os.getenv("MINIMAX_TIMEOUT") or os.getenv("MIMO_TIMEOUT") or "60"),
            provider="minimax",
            label=LLM_PROVIDER_LABELS["minimax"],
        )


class OpenAICompatibleClient:
    """Minimal OpenAI-compatible chat client."""

    def __init__(self, config: LLMConfig) -> None:
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
            raise RuntimeError(f"{self.config.label} HTTP {exc.code}: {body}") from exc
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            raise RuntimeError(
                f"{self.config.label} request timed out or failed after {self.config.timeout:g}s "
                f"for model {self.config.model} at {self.config.base_url}: {exc}"
            ) from exc
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise RuntimeError(f"{self.config.label} response content is not a string")
        return content

    def json_chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> dict[str, Any]:
        content = self.chat(
            messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        return _parse_json_object(content)


class MimoClient(OpenAICompatibleClient):
    """Backward-compatible Mimo client wrapper."""

    def __init__(self, config: MimoConfig | None = None) -> None:
        super().__init__(config or MimoConfig.from_env())


def normalize_llm_provider(provider: str | None) -> str:
    value = str(provider or DEFAULT_LLM_PROVIDER).strip().lower()
    return LLM_PROVIDER_ALIASES.get(value, value if value in LLM_PROVIDER_LABELS else DEFAULT_LLM_PROVIDER)


def supported_llm_providers() -> list[dict[str, Any]]:
    load_env()
    return [
        {"value": provider, "label": label, "configured": llm_configured(provider)}
        for provider, label in LLM_PROVIDER_LABELS.items()
        if provider in PUBLIC_LLM_PROVIDERS
    ]


def llm_config_from_env(provider: str | None = None) -> LLMConfig:
    load_env()
    normalized = normalize_llm_provider(provider)
    if normalized == "minimax":
        return MimoConfig.from_env()
    if normalized in {"deepseek_pro", "deepseek_flash"}:
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is not configured")
        model_env = "DEEPSEEK_PRO_MODEL" if normalized == "deepseek_pro" else "DEEPSEEK_FLASH_MODEL"
        default_model = "deepseek-v4-pro" if normalized == "deepseek_pro" else "deepseek-v4-flash"
        return LLMConfig(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/"),
            model=os.getenv(model_env, default_model).strip() or default_model,
            timeout=float(os.getenv("DEEPSEEK_TIMEOUT", "60")),
            provider=normalized,
            label=LLM_PROVIDER_LABELS[normalized],
        )
    raise ValueError(f"Unsupported LLM provider: {provider}")


def llm_client_from_env(provider: str | None = None, *, timeout: float | None = None) -> OpenAICompatibleClient:
    config = llm_config_from_env(provider)
    if timeout is not None:
        config = replace(config, timeout=timeout)
    return OpenAICompatibleClient(config)


def llm_configured(provider: str | None = None) -> bool:
    load_env()
    normalized = normalize_llm_provider(provider)
    if normalized == "minimax":
        return bool((os.getenv("MINIMAX_API_KEY") or os.getenv("MIMO_API_KEY") or "").strip())
    if normalized in {"deepseek_pro", "deepseek_flash"}:
        return bool(os.getenv("DEEPSEEK_API_KEY", "").strip())
    return False


def any_llm_configured() -> bool:
    return any(llm_configured(provider) for provider in LLM_PROVIDER_LABELS)


def default_configured_provider() -> str:
    if llm_configured(DEFAULT_LLM_PROVIDER):
        return DEFAULT_LLM_PROVIDER
    for provider in LLM_PROVIDER_LABELS:
        if llm_configured(provider):
            return provider
    return DEFAULT_LLM_PROVIDER


def mimo_configured() -> bool:
    return llm_configured("minimax")


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start : end + 1])
        raise
