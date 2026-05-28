from __future__ import annotations

import os
from pathlib import Path
from typing import Any


DEFAULT_SOUL_PATH = "soul.md"


def load_soul_prompt(path: str | Path | None = None) -> str:
    soul_path = Path(path or os.getenv("AGENT_SOUL_PATH", DEFAULT_SOUL_PATH))
    if not soul_path.exists():
        return ""
    text = soul_path.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    config, body = _split_frontmatter(text)
    if str(config.get("enabled", "true")).lower() in {"0", "false", "no", "off"}:
        return ""
    config_lines = [f"- {key}: {value}" for key, value in config.items()]
    config_block = "\n".join(config_lines)
    if config_block:
        return f"下面是本 agent 的可配置人格设定，必须体现在回复风格里：\n\n配置：\n{config_block}\n\n{body}".strip()
    return f"下面是本 agent 的可配置人格设定，必须体现在回复风格里：\n\n{body}".strip()


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    raw_config = parts[1]
    body = parts[2].strip()
    config: dict[str, Any] = {}
    for line in raw_config.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        config[key.strip()] = raw_value.strip().strip("\"'")
    return config, body
