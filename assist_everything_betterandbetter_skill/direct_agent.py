from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evalharness.agent import HarnessAgent
from evalharness.env import load_env
from evalharness.llm import (
    llm_configured,
    normalize_llm_provider,
    supported_llm_providers,
)
from evalharness.schemas import Message

from .mem0_backend import Mem0Config
from .runtime_config import RUNTIME_CONFIG_PATH, load_runtime_config, normalize_backend
from .skill import AssistSkill

SESSION_DIR = Path("memories/sessions")
DEFAULT_SESSION_ID = "default"


@dataclass(frozen=True)
class DirectAgentConfig:
    provider: str
    memory_dir: str
    memory_backend: str
    memory_enabled: bool
    require_llm: bool
    llm_configured: bool
    profile: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "provider": self.provider,
            "memory_dir": self.memory_dir,
            "memory_backend": self.memory_backend,
            "memory_enabled": self.memory_enabled,
            "require_llm": self.require_llm,
            "llm_configured": self.llm_configured,
            "supported_providers": supported_llm_providers(),
            "config_path": str(RUNTIME_CONFIG_PATH),
            "env": {
                "ASSIST_MEMORY_DIR": self.memory_dir,
                "ASSIST_MEMORY_BACKEND": self.memory_backend,
                "ASSIST_MEMORY_ENABLED": "1" if self.memory_enabled else "0",
                "ASSIST_RUNTIME_PROFILE": self.profile,
            },
        }


def direct_agent_config(
    *,
    provider: str | None = None,
    memory_dir: str | None = None,
    memory_backend: str | None = None,
    memory_enabled: bool | None = None,
    require_llm: bool = True,
    profile: str | None = None,
) -> DirectAgentConfig:
    runtime = load_runtime_config(profile)
    selected_provider = normalize_llm_provider(provider or runtime["agent"]["provider"])
    selected_memory_dir = str(memory_dir or runtime["memory"]["dir"])
    selected_backend = normalize_backend(memory_backend or runtime["memory"]["backend"])
    selected_enabled = memory_enabled if memory_enabled is not None else bool(runtime["memory"]["enabled"])
    return DirectAgentConfig(
        provider=selected_provider,
        memory_dir=selected_memory_dir,
        memory_backend=selected_backend,
        memory_enabled=bool(selected_enabled),
        require_llm=require_llm,
        llm_configured=llm_configured(selected_provider),
        profile=runtime["profile"],
    )


def build_direct_agent(
    *,
    provider: str | None = None,
    memory_dir: str | None = None,
    memory_backend: str | None = None,
    memory_enabled: bool | None = None,
    require_llm: bool = True,
    profile: str | None = None,
) -> HarnessAgent:
    config = direct_agent_config(
        provider=provider,
        memory_dir=memory_dir,
        memory_backend=memory_backend,
        memory_enabled=memory_enabled,
        require_llm=require_llm,
        profile=profile,
    )
    agent = HarnessAgent(
        name="assist-direct-agent",
        llm_mode=config.provider,
        memory_dir=config.memory_dir,
        persist_memory=True,
        mem0_config=_mem0_config(config.memory_backend, profile=config.profile),
        memory_enabled=config.memory_enabled,
        memory_backend=config.memory_backend,
        require_llm=config.require_llm,
    )
    _apply_privacy_config(agent, profile=config.profile)
    return agent


def build_memory_skill(
    *,
    memory_dir: str | None = None,
    memory_backend: str | None = None,
    memory_enabled: bool | None = None,
    profile: str | None = None,
    session_id: str | None = None,
) -> AssistSkill:
    config = direct_agent_config(
        memory_dir=memory_dir,
        memory_backend=memory_backend,
        memory_enabled=memory_enabled,
        require_llm=False,
        profile=profile,
    )
    skill = AssistSkill(
        memory_dir=config.memory_dir,
        mem0_config=_mem0_config(config.memory_backend, profile=config.profile),
        memory_enabled=config.memory_enabled,
        memory_backend=config.memory_backend,
    )
    items = load_runtime_config(config.profile)["privacy"]["items"]
    if items:
        skill.privacy_markers = tuple(items)
    if session_id:
        skill.session_id = session_id
    return skill


def memory_pack(
    message: str,
    *,
    context: str = "",
    memory_dir: str | None = None,
    memory_backend: str | None = None,
    memory_enabled: bool | None = None,
    profile: str | None = None,
    session_id: str = "host-default",
) -> dict[str, Any]:
    skill = build_memory_skill(
        memory_dir=memory_dir,
        memory_backend=memory_backend,
        memory_enabled=memory_enabled,
        profile=profile,
        session_id=session_id,
    )
    if not skill.memory_enabled:
        pack = {"apply_now": [], "confirm_first": [], "suppressed": []}
        relevant: list[Any] = []
    else:
        relevant = skill.retrieve_relevant_memories(message, context)
        pack = skill.relevant_memory_pack(message, relevant, context)
    return {
        "ok": True,
        "mode": "host_model_memory_pack",
        "message": message,
        "context_used": bool(context.strip()),
        "session_id": session_id,
        "memory_pack": pack,
        "applied_memory_ids": [item.id for item in relevant],
        "profile": skill.memory_profile(),
        "snapshot": skill.compact_snapshot(),
    }


def memory_write(
    message: str,
    *,
    context: str = "",
    memory_dir: str | None = None,
    memory_backend: str | None = None,
    memory_enabled: bool | None = None,
    profile: str | None = None,
    session_id: str = "host-default",
) -> dict[str, Any]:
    skill = build_memory_skill(
        memory_dir=memory_dir,
        memory_backend=memory_backend,
        memory_enabled=memory_enabled,
        profile=profile,
        session_id=session_id,
    )
    response = skill.process_message(message, context=context)
    return {
        "ok": True,
        "mode": "host_model_memory_write",
        "message": message,
        "context_used": bool(context.strip()),
        "session_id": session_id,
        "response": response.to_dict(),
        "memory_pack": (response.diagnostics or {}).get("memory_pack", {}),
        "snapshot": skill.compact_snapshot(),
    }


def memory_manage(
    message: str,
    *,
    memory_dir: str | None = None,
    memory_backend: str | None = None,
    memory_enabled: bool | None = None,
    profile: str | None = None,
    session_id: str = "host-default",
) -> dict[str, Any]:
    skill = build_memory_skill(
        memory_dir=memory_dir,
        memory_backend=memory_backend,
        memory_enabled=memory_enabled,
        profile=profile,
        session_id=session_id,
    )
    response = skill.manage_memory(message)
    return {
        "ok": True,
        "mode": "host_model_memory_manage",
        "message": message,
        "session_id": session_id,
        "response": response.to_dict(),
        "snapshot": skill.snapshot(),
    }


def direct_agent_turn(
    message: str,
    *,
    provider: str | None = None,
    memory_dir: str | None = None,
    memory_backend: str | None = None,
    memory_enabled: bool | None = None,
    require_llm: bool = True,
    profile: str | None = None,
) -> dict[str, Any]:
    agent = build_direct_agent(
        provider=provider,
        memory_dir=memory_dir,
        memory_backend=memory_backend,
        memory_enabled=memory_enabled,
        require_llm=require_llm,
        profile=profile,
    )
    turn = agent.reply(message)
    return {
        "ok": True,
        "turn": turn.to_dict(),
        "memory": agent.toolbox.snapshot(),
        "config": direct_agent_config(
            provider=provider,
            memory_dir=memory_dir,
            memory_backend=memory_backend,
            memory_enabled=memory_enabled,
            require_llm=require_llm,
            profile=profile,
        ).to_dict(),
    }


def direct_agent_session_turn(
    message: str,
    *,
    session_id: str = DEFAULT_SESSION_ID,
    provider: str | None = None,
    memory_dir: str | None = None,
    memory_backend: str | None = None,
    memory_enabled: bool | None = None,
    require_llm: bool = True,
    profile: str | None = None,
    reset_session: bool = False,
) -> dict[str, Any]:
    """Run one direct-agent turn while preserving conversation context on disk.

    This is the standalone direct-agent fallback. Installed Skill usage should
    normally use memory-pack/write/manage with the host agent's own model.
    """

    config = direct_agent_config(
        provider=provider,
        memory_dir=memory_dir,
        memory_backend=memory_backend,
        memory_enabled=memory_enabled,
        require_llm=require_llm,
        profile=profile,
    )
    path = session_path(session_id)
    if reset_session or _is_session_reset_request(message):
        if path.exists():
            path.unlink()
        return {
            "ok": True,
            "session": {"id": session_id, "path": str(path), "reset": True},
            "turn": {"assistant": {"content": "Session 已重置；长期记忆保持不变。"}},
            "memory": build_direct_agent(
                provider=config.provider,
                memory_dir=config.memory_dir,
                memory_backend=config.memory_backend,
                memory_enabled=config.memory_enabled,
                require_llm=False,
                profile=config.profile,
            ).toolbox.snapshot(),
            "config": config.to_dict(),
        }

    agent = build_direct_agent(
        provider=config.provider,
        memory_dir=config.memory_dir,
        memory_backend=config.memory_backend,
        memory_enabled=config.memory_enabled,
        require_llm=config.require_llm,
        profile=config.profile,
    )
    restore_agent_session(agent, path)
    turn = agent.reply(message)
    save_agent_session(agent, path, session_id=session_id, config=config)
    return {
        "ok": True,
        "session": {"id": session_id, "path": str(path), "message_count": len(agent.session.messages)},
        "turn": turn.to_dict(),
        "memory": agent.toolbox.snapshot(),
        "config": config.to_dict(),
    }


def session_path(session_id: str = DEFAULT_SESSION_ID) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in (session_id or DEFAULT_SESSION_ID))
    return SESSION_DIR / f"{safe}.json"


def restore_agent_session(agent: HarnessAgent, path: Path) -> None:
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        backup = path.with_suffix(".corrupt.json")
        try:
            path.replace(backup)
        except OSError:
            pass
        return
    messages = []
    for item in payload.get("messages", []):
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            timestamp = str(item.get("timestamp") or "").strip()
            messages.append(Message(role=role, content=content, timestamp=timestamp) if timestamp else Message(role=role, content=content))
    agent.session.id = str(payload.get("session_id") or agent.session.id)
    agent.session.messages = messages[-24:]
    boundary = int(payload.get("context_start_index", 0) or 0)
    agent._context_start_index = max(0, min(boundary, len(agent.session.messages)))


def save_agent_session(agent: HarnessAgent, path: Path, *, session_id: str, config: DirectAgentConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    kept_messages = agent.session.messages[-24:]
    dropped = max(0, len(agent.session.messages) - len(kept_messages))
    context_start_index = max(0, agent._context_start_index - dropped)
    context_start_index = min(context_start_index, len(kept_messages))
    payload = {
        "version": "direct-session-v1",
        "session_id": session_id,
        "context_start_index": context_start_index,
        "messages": [message.to_dict() for message in kept_messages],
        "config": {
            "provider": config.provider,
            "memory_backend": config.memory_backend,
            "memory_dir": config.memory_dir,
            "profile": config.profile,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_session_reset_request(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {
        "重置session",
        "重置 session",
        "重置会话",
        "新开session",
        "新开 session",
        "新开会话",
        "开始新任务",
        "reset session",
        "new session",
    }


def config_report(
    *,
    provider: str | None = None,
    memory_dir: str | None = None,
    memory_backend: str | None = None,
    memory_enabled: bool | None = None,
    require_llm: bool = True,
    profile: str | None = None,
) -> dict[str, Any]:
    config = direct_agent_config(
        provider=provider,
        memory_dir=memory_dir,
        memory_backend=memory_backend,
        memory_enabled=memory_enabled,
        require_llm=require_llm,
        profile=profile,
    )
    skill = AssistSkill(
        memory_dir=config.memory_dir,
        memory_enabled=config.memory_enabled,
        memory_backend=config.memory_backend,
        mem0_config=_mem0_config(config.memory_backend, profile=config.profile),
    )
    skill.privacy_markers = tuple(load_runtime_config(config.profile)["privacy"]["items"] or skill.privacy_markers)
    return {
        "ok": True,
        "config": config.to_dict(),
        "memory": skill.snapshot(),
        "profile": skill.memory_profile(),
        "privacy": skill.privacy_report(),
    }


def load_direct_env(path: str | Path | None = ".env") -> None:
    if path:
        load_env(path)


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _apply_privacy_config(agent: HarnessAgent, *, profile: str | None = None) -> None:
    items = load_runtime_config(profile)["privacy"]["items"]
    if items:
        agent.toolbox.skill.privacy_markers = tuple(items)


def _mem0_config(memory_backend: str | None = None, *, profile: str | None = None) -> Mem0Config:
    runtime = load_runtime_config(profile)
    backend = normalize_backend(memory_backend or runtime["memory"]["backend"])
    mem0 = runtime["mem0"]
    return Mem0Config(
        enabled=backend == "mem0_hosted",
        base_url=mem0["base_url"],
        api_key=mem0["api_key"],
        user_id=mem0["user_id"],
        app_id=mem0["app_id"],
        project_id=mem0["project_id"],
        project_name=mem0["project_name"],
        timeout=float(mem0["timeout"]),
    )
