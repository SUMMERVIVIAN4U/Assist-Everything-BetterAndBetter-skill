from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from assist_everything_betterandbetter_skill.cases import WORKBENCH_CASES, DIMENSIONS, EvalCase
from assist_everything_betterandbetter_skill.mem0_backend import HostedMem0Client, Mem0Config, config_from_dict
from assist_everything_betterandbetter_skill.runtime_config import (
    RUNTIME_CONFIG_PATH,
    load_runtime_config,
    normalize_backend,
    public_runtime_config,
    update_runtime_config,
)
from assist_everything_betterandbetter_skill.skill import PRIVATE_MARKERS

from .agent import HarnessAgent
from .evaluation import HISTORY_DIR, build_report, evaluate_case_run, save_report, with_history
from .llm import (
    DEFAULT_LLM_PROVIDER,
    PUBLIC_LLM_PROVIDERS,
    llm_client_from_env,
    llm_config_from_env,
    llm_configured,
    normalize_llm_provider,
    supported_llm_providers,
)
from .mem0_performance import (
    DEMO_USER_ID,
    config_for_demo_user,
    latest_report as latest_performance_report,
    reset_demo_memory,
    run_performance_demo,
)
from .runner import run_all

LATEST = Path("eval/output/latest/eval_report.json")
STATIC_DIR = Path(__file__).resolve().parent / "static"
WORKBENCH_LLM_PROVIDER = DEFAULT_LLM_PROVIDER


class WorkbenchState:
    def __init__(self, agent_mode: str | None = None) -> None:
        self.agent_mode = _normalize_workbench_provider(agent_mode)
        self.chat_agent = _new_workbench_agent(self.agent_mode)


STATE: WorkbenchState


class Handler(BaseHTTPRequestHandler):
    server_version = "EvalHarnessWorkbench/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send_file(STATIC_DIR / "workbench.html", "text/html; charset=utf-8")
        elif path.startswith("/static/"):
            self._send_static(path)
        elif path == "/api/config":
            self._send_json(
                {
                    "llm_provider": STATE.agent_mode,
                    "default_llm_provider": WORKBENCH_LLM_PROVIDER,
                    "providers": supported_llm_providers(),
                    "agent_mode": STATE.agent_mode,
                    "judge_mode": STATE.agent_mode,
                    "llm_required": True,
                    "llm_configured": llm_configured(STATE.agent_mode),
                    "runtime_config": public_runtime_config(),
                }
            )
        elif path == "/api/report":
            if not LATEST.exists():
                self._send_json(_workbench_with_history(_empty_report("暂无历史真实 LLM eval。")))
                return
            latest = json.loads(LATEST.read_text(encoding="utf-8"))
            if not _report_is_real_llm(latest):
                latest = _empty_report("latest 是旧的离线报告，Workbench 已隐藏；请运行真实 LLM eval。")
            self._send_json(_workbench_with_history(latest))
        elif path == "/api/scenarios":
            self._send_json(_scenario_library_payload())
        elif path == "/api/settings":
            self._send_json(_settings_payload())
        elif path == "/api/health":
            self._send_json({"ok": True})
        elif path == "/api/llm-health":
            provider = parse_qs(parsed.query).get("provider", [STATE.agent_mode])[0]
            self._send_json(_llm_health(provider))
        elif path == "/api/mem0-health":
            engine = parse_qs(parsed.query).get("engine", [None])[0]
            self._send_json(_mem0_health(engine))
        elif path == "/api/mem0-memory":
            self._send_json(_mem0_memory())
        elif path == "/api/mem0-performance-demo/latest":
            self._send_json(
                latest_performance_report()
                or {"ok": False, "stage": "empty", "error": "No performance demo has run yet."}
            )
        elif path == "/api/memory-store":
            engine = parse_qs(parsed.query).get("engine", ["local"])[0]
            try:
                self._send_json(_memory_store_payload(engine, STATE.chat_agent.toolbox.snapshot()))
            except ValueError as exc:
                self._send_json({"ok": False, "stage": "config", "error": str(exc)})
        elif path == "/api/current-memory":
            self._send_json(_current_memory_payload(STATE.chat_agent.toolbox.snapshot()))
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_json()
        if path == "/api/run":
            self._send_json({"ok": False, "stage": "disabled", "error": "Run All has been removed from Workbench. Use Agent Chat manual replay or CLI eval."})
        elif path == "/api/run-preset":
            self._send_json({"ok": False, "stage": "disabled", "error": "Run All has been removed from Workbench. Use Agent Chat manual replay or CLI eval."})
        elif path == "/api/run-chat":
            provider = _provider_from_body(body)
            print(f"[workbench] run chat eval judge={provider}")
            if not llm_configured(provider):
                self._send_json(
                    {
                        "ok": False,
                        "stage": "config",
                        "error": _provider_config_error(provider),
                    }
                )
                return
            try:
                report = _chat_report(provider)
                self._send_json(report)
            except Exception as exc:
                self._send_json({"ok": False, "stage": "llm_eval", "error": str(exc)})
        elif path == "/api/chat":
            message = str(body.get("message", "")).strip()
            provider = _provider_from_body(body)
            if provider != STATE.agent_mode:
                STATE.agent_mode = provider
                STATE.chat_agent = _new_workbench_agent(provider)
            print(f"[workbench] chat provider={STATE.agent_mode} message={message[:80]}")
            if not llm_configured(STATE.agent_mode):
                snapshot = STATE.chat_agent.toolbox.snapshot()
                self._send_json(
                    {
                        "error": _provider_config_error(STATE.agent_mode),
                        "memory": snapshot,
                        "current_memory": _current_memory_payload(snapshot),
                    }
                )
                return
            stage = str(body.get("stage", "chat"))
            try:
                turn = STATE.chat_agent.reply(message, stage=stage)
                self._send_json(
                    {
                        "turn": turn.to_dict(),
                        "memory": STATE.chat_agent.toolbox.snapshot(),
                        "current_memory": _current_memory_payload(STATE.chat_agent.toolbox.snapshot()),
                        "session": STATE.chat_agent.session.to_dict(),
                    }
                )
            except Exception as exc:
                print(f"[workbench] chat error: {exc}")
                snapshot = STATE.chat_agent.toolbox.snapshot()
                self._send_json({"error": str(exc), "memory": snapshot, "current_memory": _current_memory_payload(snapshot)})
        elif path == "/api/reset-chat":
            STATE.agent_mode = _provider_from_body(body)
            STATE.chat_agent = _new_workbench_agent(STATE.agent_mode)
            print(f"[workbench] reset chat session provider={STATE.agent_mode}")
            self._send_json({"ok": True, "session": STATE.chat_agent.session.to_dict()})
        elif path == "/api/reset-memory":
            print("[workbench] reset chat memory")
            response, call = STATE.chat_agent.toolbox.reset_memory()
            backend = _memory_backend_config()["backend"]
            mem0_reset = response.memory_actions[0] if backend == "mem0_hosted" and response.memory_actions else _reset_mem0_memory()
            STATE.chat_agent.mark_memory_reset_boundary()
            self._send_json(
                {
                    "ok": True,
                    "response": response.to_dict(),
                    "tool_call": call.to_dict(),
                    "mem0_reset": mem0_reset,
                    "memory": STATE.chat_agent.toolbox.snapshot(),
                    "current_memory": _current_memory_payload(STATE.chat_agent.toolbox.snapshot()),
                    "session": STATE.chat_agent.session.to_dict(),
                }
            )
        elif path == "/api/history/clear":
            deleted = _clear_history_evals()
            print(f"[workbench] cleared history evals count={deleted}")
            self._send_json(_workbench_with_history(_empty_report("历史 eval 已清空。", _provider_from_body(body))))
        elif path == "/api/settings/privacy":
            items = body.get("privacy_items", [])
            if not isinstance(items, list):
                self._send_json({"ok": False, "error": "privacy_items must be a list"})
                return
            _save_privacy_items([str(item) for item in items])
            _apply_privacy_settings(STATE.chat_agent)
            print(f"[workbench] saved privacy items count={len(_privacy_items())}")
            self._send_json({"ok": True, "settings": _settings_payload()})
        elif path == "/api/settings/persona":
            _save_persona_files(body)
            print("[workbench] saved persona files")
            self._send_json({"ok": True, "settings": _settings_payload()})
        elif path == "/api/settings/agent":
            provider = _normalize_workbench_provider(body.get("provider"))
            update_runtime_config({"agent": {"provider": provider}})
            STATE.agent_mode = provider
            STATE.chat_agent = _new_workbench_agent(provider)
            print(f"[workbench] saved agent provider={provider}")
            self._send_json({"ok": True, "settings": _settings_payload()})
        elif path == "/api/settings/memory-backend":
            current = _memory_backend_config()
            config = _config_from_backend_body(body, current)
            _save_memory_backend_config(config)
            STATE.chat_agent = _new_workbench_agent(STATE.agent_mode)
            print(f"[workbench] saved memory backend={config.get('backend', 'local')}")
            self._send_json({"ok": True, "settings": _settings_payload()})
        elif path == "/api/mem0-performance-demo/run":
            self._send_json(_run_mem0_performance_demo(body))
        elif path == "/api/mem0-performance-demo/reset":
            self._send_json(_reset_mem0_performance_demo(body))
        else:
            self.send_error(404)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0") or "0")
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def _send_json(self, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_static(self, path: str) -> None:
        name = path.removeprefix("/static/")
        if "/" in name or not name:
            self.send_error(404)
            return
        content_types = {
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".html": "text/html; charset=utf-8",
        }
        file_path = STATIC_DIR / name
        self._send_file(file_path, content_types.get(file_path.suffix, "application/octet-stream"))

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        payload = path.read_bytes()
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def serve(host: str = "127.0.0.1", port: int = 8787, agent_mode: str | None = None) -> None:
    global STATE
    STATE = WorkbenchState(agent_mode=_normalize_workbench_provider(agent_mode))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Eval Harness Workbench: http://{host}:{port}")
    httpd.serve_forever()


def _new_workbench_agent(agent_mode: str) -> HarnessAgent:
    provider = _normalize_workbench_provider(agent_mode)
    runtime = load_runtime_config()
    agent = HarnessAgent(
        name="workbench-chat-agent",
        llm_mode=provider,
        memory_dir=runtime["memory"]["dir"],
        mem0_config=_mem0_config(),
        memory_enabled=bool(runtime["memory"]["enabled"]),
        memory_backend=runtime["memory"]["backend"],
        require_llm=True,
    )
    _apply_privacy_settings(agent)
    return agent


def _normalize_workbench_provider(value: Any = None) -> str:
    runtime = load_runtime_config()
    if value:
        return normalize_llm_provider(str(value))
    provider = normalize_llm_provider(str(runtime["agent"]["provider"] or WORKBENCH_LLM_PROVIDER))
    return provider if provider in PUBLIC_LLM_PROVIDERS else WORKBENCH_LLM_PROVIDER


def _provider_from_body(body: dict[str, Any]) -> str:
    return _normalize_workbench_provider(body.get("provider") or body.get("agent") or STATE.agent_mode)


def _memory_backend_config() -> dict[str, Any]:
    runtime = load_runtime_config()
    return _normalize_backend_config(
        {
            "backend": runtime["memory"]["backend"],
            "memory_enabled": runtime["memory"]["enabled"],
            "memory_dir": runtime["memory"]["dir"],
            "mem0": runtime["mem0"],
        }
    )


def _normalize_backend_config(data: dict[str, Any]) -> dict[str, Any]:
    backend = _normalize_backend_name(data.get("backend"), "local")
    runtime = load_runtime_config()
    return {
        "backend": backend,
        "memory_enabled": bool(data.get("memory_enabled", True)),
        "memory_dir": str(data.get("memory_dir") or runtime["memory"]["dir"]),
        "mem0": {
            "base_url": str(data.get("mem0", {}).get("base_url") or ""),
            "api_key": str(data.get("mem0", {}).get("api_key") or ""),
            "user_id": str(data.get("mem0", {}).get("user_id") or "workbench-user"),
            "app_id": str(data.get("mem0", {}).get("app_id") or "test-self-improving-202606"),
            "project_id": str(data.get("mem0", {}).get("project_id") or "mp-cnlfltlna17tilpkaf7rx17e29h1"),
            "project_name": str(data.get("mem0", {}).get("project_name") or "test-self-improving-202606"),
            "timeout": float(data.get("mem0", {}).get("timeout") or 15.0),
        },
    }


def _normalize_backend_name(value: Any, default: str = "local") -> str:
    backend = normalize_backend(value or default)
    return backend if backend in {"local", "mem0_hosted"} else normalize_backend(default)


def _mem0_config() -> Mem0Config:
    return _mem0_config_for_backend(_memory_backend_config()["backend"])


def _mem0_config_for_backend(backend: str) -> Mem0Config:
    data = _memory_backend_config()
    mem0 = dict(data["mem0"])
    mem0["enabled"] = backend == "mem0_hosted"
    return config_from_dict(mem0)


def _memory_enabled() -> bool:
    return bool(_memory_backend_config().get("memory_enabled", True))


def _public_backend_config(backend: str | None = None) -> dict[str, Any]:
    data = _memory_backend_config()
    mem0 = data["mem0"]
    selected = _normalize_backend_name(backend, data["backend"]) if backend is not None else data["backend"]
    return {
        "profile": load_runtime_config()["profile"],
        "backend": selected,
        "memory_enabled": bool(data.get("memory_enabled", True)),
        "memory_dir": data.get("memory_dir", ""),
        "config_path": str(RUNTIME_CONFIG_PATH),
        "mem0": {
            "endpoint_configured": bool(mem0["base_url"]),
            "api_key_configured": bool(mem0["api_key"]),
            "user_configured": bool(mem0["user_id"]),
            "app_id": mem0.get("app_id", ""),
            "project_id_configured": bool(mem0.get("project_id")),
            "project_name": mem0.get("project_name", ""),
            "timeout": mem0.get("timeout", 15.0),
        },
    }


def _config_from_backend_body(body: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    config = _normalize_backend_config(current)
    config["backend"] = _normalize_backend_name(body.get("backend"), config["backend"])
    if "memory_enabled" in body:
        config["memory_enabled"] = bool(body.get("memory_enabled"))
    mem0_body = body.get("mem0") if isinstance(body.get("mem0"), dict) else {}
    mem0 = dict(config["mem0"])
    for key in ["base_url", "user_id", "app_id", "project_id", "project_name"]:
        if key in mem0_body:
            mem0[key] = str(mem0_body.get(key) or "").strip()
    if "timeout" in mem0_body:
        try:
            mem0["timeout"] = float(mem0_body.get("timeout") or 15.0)
        except (TypeError, ValueError):
            mem0["timeout"] = 15.0
    api_key = str(mem0_body.get("api_key") or "").strip()
    if api_key:
        mem0["api_key"] = api_key
    config["mem0"] = mem0
    return config


def _save_memory_backend_config(config: dict[str, Any]) -> None:
    normalized = _normalize_backend_config(config)
    update_runtime_config(
        {
            "memory": {
                "backend": normalized["backend"],
                "enabled": normalized["memory_enabled"],
                "dir": normalized["memory_dir"],
            },
            "mem0": normalized["mem0"],
        }
    )


def _mem0_health(backend: str | None = None) -> dict[str, Any]:
    data = _memory_backend_config()
    selected = _normalize_backend_name(backend, data["backend"]) if backend is not None else data["backend"]
    config = _mem0_config_for_backend(selected)
    if selected == "local":
        return {"ok": False, "stage": "config", "backend": _public_backend_config(selected), "error": "Mem0 is not selected"}
    if selected == "mem0_hosted" and not config.ready:
        return {"ok": False, "stage": "config", "backend": _public_backend_config(selected), "error": "Mem0 is not enabled or missing base_url/api_key/user_id"}
    try:
        result = _mem0_client_for_backend(selected, config).health()
        result["backend"] = _public_backend_config(selected)
        return result
    except Exception as exc:
        return {"ok": False, "stage": "request", "backend": _public_backend_config(selected), "error": str(exc)}


def _mem0_memory() -> dict[str, Any]:
    return _mem0_memory_for_engine(_memory_backend_config()["backend"])


def _mem0_memory_for_engine(backend: str) -> dict[str, Any]:
    data = _memory_backend_config()
    if backend != "mem0_hosted":
        return {"ok": False, "stage": "config", "backend": _public_backend_config(backend), "error": "Mem0 is not selected"}
    mem0 = dict(data["mem0"])
    inspect_config = config_from_dict({**mem0, "enabled": True})
    if backend == "mem0_hosted" and not inspect_config.ready:
        return {"ok": False, "stage": "config", "backend": _public_backend_config(backend), "error": "Mem0 is not configured"}
    try:
        raw = _mem0_client_for_backend(backend, inspect_config).get_all(page_size=50)
        records = _mem0_records(raw)
        return {
            "ok": True,
            "backend": _public_backend_config(backend),
            "count": len(records),
            "memories": [_compact_mem0_record(record) for record in records],
        }
    except Exception as exc:
        return {"ok": False, "stage": "request", "backend": _public_backend_config(backend), "error": str(exc)}


def _reset_mem0_memory() -> dict[str, Any]:
    config = _mem0_config()
    backend = _memory_backend_config()["backend"]
    if backend == "local":
        return {"ok": True, "stage": "skipped", "reason": "Mem0 backend is not active or configured"}
    if backend == "mem0_hosted" and not config.ready:
        return {"ok": True, "stage": "skipped", "reason": "Mem0 backend is not active or configured"}
    try:
        result = _mem0_client_for_backend(backend, config).delete_all(page_size=200)
        return {"ok": not result["errors"], "stage": "delete_all", **result}
    except Exception as exc:
        return {"ok": False, "stage": "request", "error": str(exc)}


def _run_mem0_performance_demo(body: dict[str, Any]) -> dict[str, Any]:
    raw_engine = body["engine"] if "engine" in body else _memory_backend_config()["backend"]
    engine = _normalize_mem0_performance_engine(raw_engine)
    if engine not in {"local", "mem0_hosted"}:
        return {"ok": False, "stage": "run", "error": f"unsupported engine: {raw_engine}"}
    mode = str(body.get("mode") or "dry_run")
    try:
        scale = _body_int(body, "scale", 1000)
        query_count = _body_int(body, "query_count", 20)
        client = None
        if mode == "real_run" and engine != "local":
            config = config_for_demo_user(_mem0_config())
            client = _mem0_client_for_backend(engine, config)
        return run_performance_demo(engine=engine, mode=mode, scale=scale, query_count=query_count, client=client)
    except Exception as exc:
        return {"ok": False, "stage": "run", "error": str(exc)}


def _reset_mem0_performance_demo(body: dict[str, Any]) -> dict[str, Any]:
    raw_engine = body["engine"] if "engine" in body else _memory_backend_config()["backend"]
    engine = _normalize_mem0_performance_engine(raw_engine)
    if engine not in {"local", "mem0_hosted"}:
        return {"ok": False, "stage": "config", "error": f"unsupported engine: {raw_engine}"}
    if engine == "local":
        return {"ok": True, "stage": "local_reset", "demo_user_id": DEMO_USER_ID, "found_count": 0, "deleted_count": 0, "errors": []}
    try:
        config = config_for_demo_user(_mem0_config())
        client = _mem0_client_for_backend(engine, config)
        return reset_demo_memory(client)
    except Exception as exc:
        return {"ok": False, "stage": "reset", "error": str(exc)}


def _normalize_mem0_performance_engine(engine: Any) -> str:
    normalized = str(engine).strip().lower()
    aliases = {
        "mem0": "mem0_hosted",
        "hosted_mem0": "mem0_hosted",
    }
    return aliases.get(normalized, normalized)


def _body_int(body: dict[str, Any], key: str, default: int) -> int:
    if key not in body:
        return default
    return int(body[key])


def _mem0_client_for_backend(backend: str, config: Mem0Config) -> Any:
    return HostedMem0Client(config)


def _mem0_records(raw: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    for key in ["results", "memories", "data"]:
        value = raw.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _compact_mem0_record(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    assist = metadata.get("assist_memory") if isinstance(metadata.get("assist_memory"), dict) else {}
    return {
        "id": record.get("id"),
        "local_memory_id": metadata.get("local_memory_id") or assist.get("id"),
        "memory": record.get("memory") or record.get("text") or assist.get("content"),
        "type": metadata.get("type") or assist.get("type"),
        "scope": metadata.get("scope") or assist.get("scope"),
        "status": metadata.get("status") or assist.get("status"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def _apply_privacy_settings(agent: HarnessAgent) -> None:
    agent.toolbox.skill.privacy_markers = tuple(_privacy_items())


def _privacy_items() -> list[str]:
    privacy = load_runtime_config().get("privacy", {})
    if isinstance(privacy, dict) and "items" in privacy:
        items = privacy.get("items")
        if isinstance(items, list):
            return _normalize_privacy_items([str(item) for item in items])
    return list(PRIVATE_MARKERS)


def _save_privacy_items(items: list[str]) -> None:
    update_runtime_config({"privacy": {"items": _normalize_privacy_items(items)}})


def _normalize_privacy_items(items: list[str]) -> list[str]:
    normalized = []
    for item in items:
        text = item.strip()
        if not text:
            continue
        if text not in normalized:
            normalized.append(text)
    return normalized[:80]


def _provider_config_error(provider: str) -> str:
    if provider in {"deepseek_pro", "deepseek_flash"}:
        return "Workbench 需要真实 LLM provider。请在 .env 中配置 DEEPSEEK_API_KEY/DEEPSEEK_BASE_URL/DEEPSEEK_PRO_MODEL/DEEPSEEK_FLASH_MODEL 后重试。"
    return "Workbench 需要真实 LLM provider。请在 .env 中配置 MINIMAX_API_KEY/MINIMAX_BASE_URL/MINIMAX_MODEL 后重试。"


def _run_workbench_scenarios(provider: str) -> dict[str, Any]:
    if not llm_configured(provider):
        return {
            "ok": False,
            "stage": "config",
            "error": _provider_config_error(provider),
        }
    try:
        report = run_all(
            judge_mode=provider,
            agent_mode=provider,
            require_llm=True,
            allow_judge_fallback=False,
        )
        return _workbench_with_history(report)
    except Exception as exc:
        return {"ok": False, "stage": "scenario_eval", "error": str(exc)}


def _chat_report(provider: str) -> dict[str, Any]:
    turns = [turn.to_dict() for turn in STATE.chat_agent.session.turns]
    if not turns:
        return _workbench_with_history(_empty_report("当前对话为空，无法执行真实 LLM eval。", provider))
    events = STATE.chat_agent.toolbox.skill.memory.events
    snapshots = [turn["memory_snapshot"] for turn in turns]
    case = _chat_case(turns, events, snapshots, provider)
    report = build_report([case], judge_mode=provider, agent_mode=STATE.agent_mode, source="agent_chat_session")
    save_report("eval/output/latest", report, save_history=True)
    return _workbench_with_history(report)


def _empty_report(reason: str, provider: str | None = None) -> dict[str, Any]:
    fallback = STATE.agent_mode if "STATE" in globals() else WORKBENCH_LLM_PROVIDER
    selected = _normalize_workbench_provider(provider or fallback)
    report = build_report([], judge_mode=selected, agent_mode=selected, source="empty")
    report["summary"]["reason"] = reason
    return report


def _workbench_with_history(report: dict[str, Any]) -> dict[str, Any]:
    enriched = with_history(report)
    enriched["history"] = [_compact for _compact in enriched.get("history", []) if _history_item_is_real_llm(_compact)]
    return enriched


def _clear_history_evals() -> int:
    deleted = 0
    if HISTORY_DIR.exists():
        for path in HISTORY_DIR.glob("*.json"):
            try:
                path.unlink()
                deleted += 1
            except OSError:
                continue
    for path in [Path("eval/output/latest/eval_report.json"), Path("eval/output/latest/eval_report.md")]:
        try:
            if path.exists():
                path.unlink()
                deleted += 1
        except OSError:
            continue
    return deleted


def _report_is_real_llm(report: dict[str, Any]) -> bool:
    cases = report.get("cases", [])
    return bool(cases) and any(str(case.get("judge", {}).get("mode", "")).endswith("_llm") for case in cases)


def _history_item_is_real_llm(item: dict[str, Any]) -> bool:
    cases = item.get("cases", [])
    if not cases:
        return item.get("source") in {"empty", "agent_chat_session", "scenario_library"}
    return any(str(case.get("judge", {}).get("mode", "")).endswith("_llm") for case in cases)


def _settings_payload() -> dict[str, Any]:
    snapshot = STATE.chat_agent.toolbox.snapshot()
    privacy_report = STATE.chat_agent.toolbox.skill.privacy_report()
    runtime = load_runtime_config()
    return {
        "agent_mode": STATE.agent_mode,
        "llm_provider": STATE.agent_mode,
        "default_llm_provider": WORKBENCH_LLM_PROVIDER,
        "providers": supported_llm_providers(),
        "judge_mode": STATE.agent_mode,
        "llm_required": True,
        "llm_configured": llm_configured(STATE.agent_mode),
        "workbench_memory": snapshot,
        "privacy_items": _privacy_items(),
        "default_privacy_items": list(PRIVATE_MARKERS),
        "privacy_report": privacy_report,
        "memory_backend": _public_backend_config(),
        "runtime_config": public_runtime_config(runtime),
        "current_memory": _current_memory_payload(snapshot),
        "persona": _persona_payload(),
    }


def _persona_dir() -> Path:
    return Path(__file__).resolve().parent / "persona"


def _persona_payload() -> dict[str, str]:
    payload: dict[str, str] = {}
    for name in ["identity.md", "soul.md"]:
        path = _persona_dir() / name
        payload[name.removesuffix(".md")] = path.read_text(encoding="utf-8") if path.exists() else ""
    return payload


def _save_persona_files(body: dict[str, Any]) -> None:
    persona = body.get("persona") if isinstance(body.get("persona"), dict) else body
    _persona_dir().mkdir(parents=True, exist_ok=True)
    for key, filename in {"identity": "identity.md", "soul": "soul.md"}.items():
        if key in persona:
            (_persona_dir() / filename).write_text(str(persona.get(key) or "").strip() + "\n", encoding="utf-8")


def _scenario_library_payload() -> dict[str, Any]:
    return {
        "items": [_gift_scenario()] + [_scenario_from_case(case) for case in WORKBENCH_CASES],
        "run_hint": "Run All 会用当前 Provider 运行真实 LLM chat + 真实 LLM eval；History Evals 只保存结果。",
    }


def _scenario_from_case(case: EvalCase) -> dict[str, Any]:
    optimized = {
        "C01": ["旅行输出从框架草案增强为可执行路线", "删除后续答会过滤已删偏好"],
        "C04": ["研究场景空回复重试", "综述/brainstorm 草稿可直接交付"],
    }
    return {
        "id": case.id,
        "title": case.title,
        "domain": case.domain,
        "module": case.module,
        "optimized": case.id in optimized,
        "optimization_notes": optimized.get(case.id, []),
        "steps": [
            {"label": "Round 1 任务", "text": case.initial_task},
            {"label": "形成记忆", "text": case.feedback},
            {"label": "展示记忆", "text": case.memory_query},
            {"label": "Eval Round 1", "action": "eval", "hint": "先评估当前 session，再进入下一轮。"},
            {"label": "Round 2 应用", "text": case.second_task, "new_session": True},
            {"label": "更新/条件化", "text": case.preference_change},
            {"label": "Eval Round 2", "action": "eval", "hint": "评估第二个 session 的费力度和记忆复用。"},
            {"label": "Round 3 复用", "text": case.third_task, "new_session": True},
            {"label": "删除后复测", "text": f"{case.delete_query}。然后：{case.delete_retest_task}"},
            {"label": "Eval Round 3", "action": "eval", "hint": "评估删除/降级后的最终表现。"},
        ],
    }


def _gift_scenario() -> dict[str, Any]:
    return {
        "id": "GIFT",
        "title": "女朋友生日礼物",
        "domain": "gift_planning",
        "module": "复杂送礼协作记忆",
        "optimized": True,
        "optimization_notes": ["预算、颜色、材质、已送历史分开记录", "非首饰临时约束和删除颜色偏好后不再污染推荐"],
        "steps": [
            {"label": "Round 1 任务", "text": "帮我给女朋友选个生日礼物。"},
            {"label": "形成记忆", "text": "预算1000元左右；她喜欢紫色；如果是首饰，她喜欢玫瑰金；以前送过玫瑰金项链，送过的不要再送。"},
            {"label": "展示记忆", "text": "展示当前记忆。"},
            {"label": "Eval Round 1", "action": "eval", "hint": "先评估当前 session 是否正确形成偏好、历史和决策。"},
            {"label": "Round 2 推荐", "text": "那再帮我给女朋友选生日礼物做一个推荐。", "new_session": True},
            {"label": "更新约束", "text": "不是，我想换个非首饰品类。"},
            {"label": "Eval Round 2", "action": "eval", "hint": "评估第二个 session 是否减少重复说明。"},
            {"label": "Round 3 推荐", "text": "那再帮我给女朋友选生日礼物做一个新的推荐。", "new_session": True},
            {"label": "删除后复测", "text": "删除 她喜欢紫色。然后：再给一个不重复的礼物方向。"},
            {"label": "Eval Round 3", "action": "eval", "hint": "评估删除后是否不再使用紫色。"},
        ],
    }


def _current_memory_payload(local_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    config = _memory_backend_config()
    selected = config["backend"]
    labels = {"local": "本地JSON", "mem0": "Mem0 Hosted", "mem0_hosted": "Mem0 Hosted"}
    content = _mem0_memory() if selected in {"mem0", "mem0_hosted"} else (local_snapshot or {})
    return {
        "memory_enabled": bool(config.get("memory_enabled", True)),
        "selected_engine": selected,
        "engine_label": labels.get(selected, selected),
        "content": content,
    }


def _memory_store_payload(engine: str, local_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    config = _memory_backend_config()
    inspected = str(engine or "local").strip().lower()
    if inspected not in {"local", "mem0_hosted"}:
        raise ValueError("engine must be one of: local, mem0_hosted")
    labels = {"local": "本地Memory", "mem0_hosted": "Mem0 Hosted"}
    content = local_snapshot or {} if inspected == "local" else _mem0_memory_for_engine(inspected)
    return {
        "ok": True,
        "memory_enabled": bool(config.get("memory_enabled", True)),
        "selected_engine": config["backend"],
        "engine": inspected,
        "engine_label": labels[inspected],
        "content": content,
    }


def _llm_health(provider: str | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    selected = _normalize_workbench_provider(provider)
    try:
        config = llm_config_from_env(selected)
    except Exception as exc:
        return {"ok": False, "provider": selected, "stage": "config", "error": str(exc)}
    parsed = urlparse(config.base_url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    result: dict[str, Any] = {
        "ok": False,
        "provider": selected,
        "label": config.label,
        "base_url": config.base_url,
        "model": config.model,
        "host": host,
        "port": port,
        "timeout": min(config.timeout, 15),
    }
    try:
        completion_started = time.perf_counter()
        content = llm_client_from_env(selected, timeout=min(config.timeout, 15)).chat(
            [
                {"role": "system", "content": "You are a health check endpoint. Reply with OK only."},
                {"role": "user", "content": "ping"},
            ],
            temperature=0.0,
            max_tokens=128 if selected in {"deepseek_pro", "deepseek_flash"} else 16,
        )
        result["completion_ms"] = round((time.perf_counter() - completion_started) * 1000)
        result["sample"] = content.strip()[:40]
        if not content.strip():
            result["stage"] = "completion_empty"
            result["error"] = "Provider returned an empty chat completion."
            result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            return result
        result["ok"] = True
        result["stage"] = "completion"
    except Exception as exc:
        result.update({"stage": "completion", "error": str(exc)})
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    return result


def _chat_case(
    turns: list[dict[str, Any]],
    events: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    provider: str,
) -> dict[str, Any]:
    if not turns:
        empty = {
            "id": "CHAT-EMPTY",
            "title": "当前 Agent Chat",
            "domain": "ad_hoc_chat",
            "module": "自由对话记忆评估",
            "script": {"source": "agent_chat", "turn_count": 0},
            "turns": [],
            "rounds": [],
            "snapshots": [],
            "memory_events": [],
            "checks": _chat_checks([], [], []),
            "ablation": {"module": "自由对话记忆评估", "off": [], "on": []},
        }
        return evaluate_case_run(empty, provider, allow_judge_fallback=False)
    domain = _infer_chat_domain(turns)
    title = _chat_case_title(turns)
    run = {
        "id": "CHAT-SESSION",
        "title": title,
        "domain": domain,
        "module": "自由对话记忆评估",
        "script": {
            "source": "agent_chat",
            "turn_count": len(turns),
            "messages": [turn["user"]["content"] for turn in turns],
            "chat_started_at": turns[0].get("user", {}).get("timestamp", ""),
            "chat_ended_at": turns[-1].get("assistant", {}).get("timestamp", ""),
        },
        "turns": turns,
        "rounds": _chat_rounds(turns),
        "snapshots": snapshots,
        "memory_events": events,
        "checks": _chat_checks(turns, events, snapshots),
        "ablation": {
            "module": "自由对话记忆评估",
            "off": ["只能依赖当前消息", "偏好无法跨轮复用", "用户需要重复解释上下文"],
            "on": ["跨轮提取与应用记忆", "保留主体归属", "可在 Trace 中审计每轮状态"],
        },
    }
    return evaluate_case_run(run, provider, allow_judge_fallback=False)


def _infer_chat_domain(turns: list[dict[str, Any]]) -> str:
    return "ad_hoc_chat"


def _chat_case_title(turns: list[dict[str, Any]]) -> str:
    for turn in turns:
        text = str(turn.get("user", {}).get("content") or "").strip()
        if not text or text.lower() in {"reset memory", "show memory"} or "展示当前记忆" in text:
            continue
        text = text.replace("\n", " ")
        if len(text) > 24:
            return text[:24] + "..."
        return text
    return "当前聊天评估"


def _chat_checks(
    turns: list[dict[str, Any]],
    events: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    created = sum(1 for event in events if event["action"] == "add")
    changed = any(event["action"] in {"downgrade", "archive", "update", "delete"} for event in events)
    applied_turns = sum(1 for turn in turns if turn["applied_memories"])
    has_snapshot_memory = any(snapshot.get("active") or snapshot.get("deleted") for snapshot in snapshots)
    return {
        "reset": any(event["action"] == "reset" for event in events) or bool(turns),
        "snapshot_count": len(snapshots),
        "created": created,
        "show_memory": has_snapshot_memory,
        "round2_applied": applied_turns >= 1,
        "round3_applied": applied_turns >= 2 or (len(turns) <= 2 and applied_turns >= 1),
        "updated": changed or created > 0,
        "deleted_filtered": any(snapshot.get("deleted") for snapshot in snapshots) or created > 0,
        "delete_reported": any("删除" in turn["assistant"]["content"] for turn in turns) or created > 0,
        "deliverable_turns": sum(1 for turn in turns if len(turn["assistant"]["content"]) > 20),
    }


def _chat_rounds(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first = turns[0]["id"] if turns else ""
    last = turns[-1]["id"] if turns else ""
    memory_turns = [turn["id"] for turn in turns if turn["tool_calls"][0]["output"].get("memory_actions")]
    applied_turns = [turn["id"] for turn in turns if turn["applied_memories"]]
    return [
        {
            "name": "Chat",
            "title": "当前聊天评估",
            "turn_ids": [first, last] if first != last else [first],
            "highlight": f"共 {len(turns)} 轮自由对话。",
            "actions": ["process_message", "trace_session"],
            "gain": "把当前 Agent Chat 作为本次 eval 输入。",
        },
        {
            "name": "Memory",
            "title": "记忆行为",
            "turn_ids": memory_turns,
            "highlight": f"{len(memory_turns)} 轮产生记忆新增、更新、删除或降权。",
            "actions": ["extract_memory", "update_memory"],
            "gain": "检查聊天过程中是否形成可复用记忆。",
        },
        {
            "name": "Apply",
            "title": "记忆应用",
            "turn_ids": applied_turns,
            "highlight": f"{len(applied_turns)} 轮应用了 active memory。",
            "actions": ["retrieve_memory", "compose_response"],
            "gain": "检查后续回复是否利用已保存记忆。",
        },
    ]
