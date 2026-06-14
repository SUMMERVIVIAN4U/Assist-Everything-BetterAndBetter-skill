from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


RUNTIME_CONFIG_PATH = Path("memories/config/runtime.json")
LEGACY_WORKBENCH_BACKEND_PATH = Path("memories/workbench/_backend.json")
LEGACY_WORKBENCH_PRIVACY_PATH = Path("memories/workbench/_privacy.json")

SUPPORTED_PROVIDERS = {"deepseek_pro", "deepseek_flash", "mimo"}
PROVIDER_ALIASES = {
    "deepseek": "deepseek_pro",
    "deepseek-pro": "deepseek_pro",
    "deepseek_v4_pro": "deepseek_pro",
    "deepseek-v4-pro": "deepseek_pro",
    "deepseek-flash": "deepseek_flash",
    "deepseek_v4_flash": "deepseek_flash",
    "deepseek-v4-flash": "deepseek_flash",
    "mimo": "mimo",
}


def load_runtime_config(profile: str | None = None) -> dict[str, Any]:
    selected_profile = _profile_name(profile or os.getenv("ASSIST_RUNTIME_PROFILE") or "default")
    config = _default_runtime_config(selected_profile)
    _deep_merge(config, _legacy_workbench_config())
    _deep_merge(config, _read_json(RUNTIME_CONFIG_PATH))
    if profile:
        config["profile"] = selected_profile
    return normalize_runtime_config(config)


def save_runtime_config(config: dict[str, Any], profile: str | None = None) -> dict[str, Any]:
    merged = load_runtime_config(profile)
    _deep_merge(merged, config or {})
    normalized = normalize_runtime_config(merged)
    RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized


def update_runtime_config(patch: dict[str, Any], profile: str | None = None) -> dict[str, Any]:
    config = load_runtime_config(profile)
    _deep_merge(config, patch)
    return save_runtime_config(config, profile)


def normalize_runtime_config(config: dict[str, Any] | None) -> dict[str, Any]:
    raw = config if isinstance(config, dict) else {}
    normalized = _default_runtime_config(_profile_name(raw.get("profile") or "default"))
    _deep_merge(normalized, raw)
    normalized["profile"] = _profile_name(normalized.get("profile"))
    agent = normalized["agent"]
    agent["provider"] = normalize_provider(agent.get("provider"))
    memory = normalized["memory"]
    memory["backend"] = normalize_backend(memory.get("backend"))
    memory["enabled"] = bool(memory.get("enabled", True))
    memory["dir"] = str(memory.get("dir") or _default_memory_dir(normalized["profile"]))
    memory["llm_extractor"] = bool(memory.get("llm_extractor", True))
    mem0 = normalized["mem0"]
    for key in ["base_url", "api_key", "user_id", "app_id", "project_id", "project_name"]:
        mem0[key] = str(mem0.get(key) or "").strip()
    mem0["user_id"] = mem0["user_id"] or "assist-default-user"
    mem0["app_id"] = mem0["app_id"] or "assist-everything-betterandbetter-skill"
    try:
        mem0["timeout"] = float(mem0.get("timeout") or 15.0)
    except (TypeError, ValueError):
        mem0["timeout"] = 15.0
    privacy = normalized["privacy"]
    items = privacy.get("items", [])
    privacy["items"] = [str(item).strip() for item in items if str(item).strip()] if isinstance(items, list) else []
    return normalized


def public_runtime_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    data = normalize_runtime_config(config or load_runtime_config())
    mem0 = data["mem0"]
    return {
        "profile": data["profile"],
        "agent": dict(data["agent"]),
        "memory": dict(data["memory"]),
        "mem0": {
            "endpoint_configured": bool(mem0["base_url"]),
            "api_key_configured": bool(mem0["api_key"]),
            "user_configured": bool(mem0["user_id"]),
            "app_id": mem0["app_id"],
            "project_id_configured": bool(mem0["project_id"]),
            "project_name": mem0["project_name"],
            "timeout": mem0["timeout"],
        },
        "privacy": {"items": list(data["privacy"].get("items", []))},
        "config_path": str(RUNTIME_CONFIG_PATH),
    }


def normalize_provider(provider: Any) -> str:
    raw = str(provider or os.getenv("ASSIST_AGENT_PROVIDER") or "deepseek_pro").strip().lower()
    value = PROVIDER_ALIASES.get(raw, raw)
    return value if value in SUPPORTED_PROVIDERS else "deepseek_pro"


def normalize_backend(value: Any) -> str:
    raw = str(value or "local").strip().lower().replace("-", "_")
    aliases = {"mem0": "mem0_hosted", "hosted_mem0": "mem0_hosted", "mem0_rest": "mem0_hosted"}
    backend = aliases.get(raw, raw)
    return backend if backend in {"local", "mem0_hosted"} else "local"


def profile_memory_dir(profile: str | None = None) -> str:
    return load_runtime_config(profile)["memory"]["dir"]


def _default_runtime_config(profile: str) -> dict[str, Any]:
    return {
        "version": 1,
        "profile": profile,
        "agent": {
            "provider": os.getenv("ASSIST_AGENT_PROVIDER") or os.getenv("EVALHARNESS_AGENT_PROVIDER") or "deepseek_pro",
        },
        "memory": {
            "enabled": os.getenv("ASSIST_MEMORY_ENABLED", "1") != "0",
            "dir": os.getenv("ASSIST_MEMORY_DIR") or _default_memory_dir(profile),
            "backend": os.getenv("ASSIST_MEMORY_BACKEND", "local"),
            "llm_extractor": os.getenv("ASSIST_MEMORY_LLM_EXTRACTOR", "1").strip().lower() not in {"0", "false", "off", "no"},
        },
        "mem0": {
            "base_url": os.getenv("MEM0_BASE_URL", "").strip(),
            "api_key": os.getenv("MEM0_API_KEY", "").strip(),
            "user_id": os.getenv("MEM0_USER_ID", "assist-default-user").strip(),
            "app_id": os.getenv("MEM0_APP_ID", "assist-everything-betterandbetter-skill").strip(),
            "project_id": os.getenv("MEM0_PROJECT_ID", "").strip(),
            "project_name": os.getenv("MEM0_PROJECT_NAME", "").strip(),
            "timeout": float(os.getenv("MEM0_TIMEOUT", "15") or 15),
        },
        "privacy": {"items": []},
    }


def _legacy_workbench_config() -> dict[str, Any]:
    patch: dict[str, Any] = {}
    backend = _read_json(LEGACY_WORKBENCH_BACKEND_PATH)
    if backend:
        patch["memory"] = {
            "backend": backend.get("backend"),
            "enabled": backend.get("memory_enabled", True),
        }
        if isinstance(backend.get("mem0"), dict):
            patch["mem0"] = dict(backend["mem0"])
    privacy = _read_json(LEGACY_WORKBENCH_PRIVACY_PATH)
    if isinstance(privacy.get("privacy_items"), list):
        patch["privacy"] = {"items": privacy["privacy_items"]}
    return patch


def _default_memory_dir(profile: str) -> str:
    if profile in {"eval", "workbench-demo", "mem0-performance"}:
        return f"memories/{profile}"
    return "memories/default"


def _profile_name(value: Any) -> str:
    name = str(value or "default").strip().lower().replace(" ", "-")
    return name or "default"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        elif value is not None:
            base[key] = value
    return base
