from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from assist_everything_betterandbetter_skill.cases import DIMENSIONS
from assist_everything_betterandbetter_skill.mem0_backend import HostedMem0Client, Mem0Config, Mem0SdkClient, config_from_dict
from assist_everything_betterandbetter_skill.skill import PRIVATE_MARKERS

from .agent import HarnessAgent
from .evaluation import build_report, evaluate_case_run, save_report, with_history
from .judge import score_with_fallback
from .llm import MimoConfig
from .mem0_performance import (
    config_for_demo_user,
    latest_report as latest_performance_report,
    reset_demo_memory,
    run_performance_demo,
)
from .runner import run_all

LATEST = Path("eval/output/latest/eval_report.json")
PRIVACY_SETTINGS = Path("memories/workbench/_privacy.json")
BACKEND_SETTINGS = Path("memories/workbench/_backend.json")
STATIC_DIR = Path(__file__).resolve().parent / "static"


class WorkbenchState:
    def __init__(self, agent_mode: str = "auto") -> None:
        self.agent_mode = agent_mode
        self.chat_agent = _new_workbench_agent(agent_mode)


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
            self._send_json({"agent_mode": STATE.agent_mode})
        elif path == "/api/report":
            if not LATEST.exists():
                run_all()
            self._send_json(with_history(json.loads(LATEST.read_text(encoding="utf-8"))))
        elif path == "/api/settings":
            self._send_json(_settings_payload())
        elif path == "/api/health":
            self._send_json({"ok": True})
        elif path == "/api/llm-health":
            self._send_json(_llm_health())
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
            print("[workbench] /api/run compatibility route -> preset cases")
            report = run_all(judge_mode=body.get("judge", "heuristic"), agent_mode=body.get("agent", "local"))
            self._send_json(with_history(report))
        elif path == "/api/run-preset":
            print(f"[workbench] run preset cases agent={body.get('agent', 'local')} judge={body.get('judge', 'heuristic')}")
            report = run_all(judge_mode=body.get("judge", "heuristic"), agent_mode=body.get("agent", "local"))
            self._send_json(with_history(report))
        elif path == "/api/run-chat":
            print(f"[workbench] run chat eval judge={body.get('judge', 'heuristic')}")
            report = _chat_report(body.get("judge", "heuristic"))
            self._send_json(report)
        elif path == "/api/chat":
            message = str(body.get("message", "")).strip()
            mode = str(body.get("agent", STATE.agent_mode))
            if mode != STATE.agent_mode:
                STATE.agent_mode = mode
                STATE.chat_agent = _new_workbench_agent(mode)
            print(f"[workbench] chat agent={STATE.agent_mode} message={message[:80]}")
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
            mode = str(body.get("agent", STATE.agent_mode))
            STATE.agent_mode = mode
            STATE.chat_agent = _new_workbench_agent(mode)
            print(f"[workbench] reset chat session agent={STATE.agent_mode}")
            self._send_json({"ok": True, "session": STATE.chat_agent.session.to_dict()})
        elif path == "/api/reset-memory":
            print("[workbench] reset chat memory")
            response, call = STATE.chat_agent.toolbox.reset_memory()
            backend = _memory_backend_config()["backend"]
            mem0_reset = response.memory_actions[0] if backend in {"mem0_hosted", "mem0_sdk"} and response.memory_actions else _reset_mem0_memory()
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
        elif path == "/api/settings/privacy":
            items = body.get("privacy_items", [])
            if not isinstance(items, list):
                self._send_json({"ok": False, "error": "privacy_items must be a list"})
                return
            _save_privacy_items([str(item) for item in items])
            _apply_privacy_settings(STATE.chat_agent)
            print(f"[workbench] saved privacy items count={len(_privacy_items())}")
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


def serve(host: str = "127.0.0.1", port: int = 8787, agent_mode: str = "local") -> None:
    global STATE
    STATE = WorkbenchState(agent_mode=agent_mode)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Eval Harness Workbench: http://{host}:{port}")
    httpd.serve_forever()


def _new_workbench_agent(agent_mode: str) -> HarnessAgent:
    agent = HarnessAgent(
        name="workbench-chat-agent",
        llm_mode=agent_mode,
        memory_dir="memories/workbench",
        mem0_config=_mem0_config(),
        memory_enabled=_memory_enabled(),
        memory_backend=_memory_backend_config()["backend"],
    )
    _apply_privacy_settings(agent)
    return agent


def _memory_backend_config() -> dict[str, Any]:
    if BACKEND_SETTINGS.exists():
        try:
            data = json.loads(BACKEND_SETTINGS.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return _normalize_backend_config(data)
        except Exception:
            pass
    return _normalize_backend_config({})


def _normalize_backend_config(data: dict[str, Any]) -> dict[str, Any]:
    backend = _normalize_backend_name(data.get("backend"), "local")
    return {
        "backend": backend,
        "memory_enabled": bool(data.get("memory_enabled", True)),
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
    backend = str(value or default).strip().lower()
    aliases = {"mem0": "mem0_hosted", "hosted_mem0": "mem0_hosted", "mem0ai": "mem0_sdk", "sdk_mem0": "mem0_sdk"}
    backend = aliases.get(backend, backend)
    if backend not in {"local", "mem0_hosted", "mem0_sdk"}:
        return default if default in {"local", "mem0_hosted", "mem0_sdk"} else "local"
    return backend


def _mem0_config() -> Mem0Config:
    return _mem0_config_for_backend(_memory_backend_config()["backend"])


def _mem0_config_for_backend(backend: str) -> Mem0Config:
    data = _memory_backend_config()
    mem0 = dict(data["mem0"])
    mem0["enabled"] = backend in {"mem0_hosted", "mem0_sdk"}
    return config_from_dict(mem0)


def _memory_enabled() -> bool:
    return bool(_memory_backend_config().get("memory_enabled", True))


def _public_backend_config(backend: str | None = None) -> dict[str, Any]:
    data = _memory_backend_config()
    mem0 = data["mem0"]
    selected = _normalize_backend_name(backend, data["backend"]) if backend is not None else data["backend"]
    return {
        "backend": selected,
        "memory_enabled": bool(data.get("memory_enabled", True)),
        "mem0": {
            "endpoint_configured": bool(mem0["base_url"]),
            "api_key_configured": bool(mem0["api_key"]),
            "user_configured": bool(mem0["user_id"]),
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
    BACKEND_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    BACKEND_SETTINGS.write_text(json.dumps(_normalize_backend_config(config), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    if backend not in {"mem0_hosted", "mem0_sdk"}:
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
    engine = _normalize_mem0_performance_engine(body.get("engine") or _memory_backend_config()["backend"])
    if engine not in {"mem0_hosted", "mem0_sdk"}:
        return {"ok": False, "stage": "run", "error": f"unsupported engine: {body.get('engine') or _memory_backend_config()['backend']}"}
    mode = str(body.get("mode") or "dry_run")
    try:
        scale = _body_int(body, "scale", 1000)
        query_count = _body_int(body, "query_count", 20)
        client = None
        if mode == "real_run":
            config = config_for_demo_user(_mem0_config())
            client = _mem0_client_for_backend(engine, config)
        return run_performance_demo(engine=engine, mode=mode, scale=scale, query_count=query_count, client=client)
    except Exception as exc:
        return {"ok": False, "stage": "run", "error": str(exc)}


def _reset_mem0_performance_demo(body: dict[str, Any]) -> dict[str, Any]:
    engine = _normalize_mem0_performance_engine(body.get("engine") or _memory_backend_config()["backend"])
    if engine not in {"mem0_hosted", "mem0_sdk"}:
        return {"ok": False, "stage": "config", "error": f"unsupported engine: {body.get('engine') or _memory_backend_config()['backend']}"}
    try:
        config = config_for_demo_user(_mem0_config())
        client = _mem0_client_for_backend(engine, config)
        return reset_demo_memory(client)
    except Exception as exc:
        return {"ok": False, "stage": "reset", "error": str(exc)}


def _normalize_mem0_performance_engine(engine: Any) -> str:
    normalized = str(engine or "").strip().lower()
    aliases = {
        "mem0": "mem0_hosted",
        "hosted_mem0": "mem0_hosted",
        "mem0ai": "mem0_sdk",
        "sdk_mem0": "mem0_sdk",
    }
    return aliases.get(normalized, normalized)


def _body_int(body: dict[str, Any], key: str, default: int) -> int:
    if key not in body or body.get(key) is None or body.get(key) == "":
        return default
    return int(body[key])


def _mem0_client_for_backend(backend: str, config: Mem0Config) -> Any:
    if backend == "mem0_sdk":
        return Mem0SdkClient(config)
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
    if PRIVACY_SETTINGS.exists():
        try:
            data = json.loads(PRIVACY_SETTINGS.read_text(encoding="utf-8"))
            items = data.get("privacy_items", [])
            if isinstance(items, list):
                return _normalize_privacy_items([str(item) for item in items])
        except Exception:
            pass
    return list(PRIVATE_MARKERS)


def _save_privacy_items(items: list[str]) -> None:
    PRIVACY_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    payload = {"privacy_items": _normalize_privacy_items(items)}
    PRIVACY_SETTINGS.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_privacy_items(items: list[str]) -> list[str]:
    normalized = []
    for item in items:
        text = item.strip()
        if not text:
            continue
        if text not in normalized:
            normalized.append(text)
    return normalized[:80]


def _chat_report(judge_mode: str) -> dict[str, Any]:
    turns = [turn.to_dict() for turn in STATE.chat_agent.session.turns]
    events = STATE.chat_agent.toolbox.skill.memory.events
    snapshots = [turn["memory_snapshot"] for turn in turns]
    case = _chat_case(turns, events, snapshots, judge_mode)
    report = build_report([case], judge_mode=judge_mode, agent_mode=STATE.agent_mode, source="agent_chat_session")
    save_report("eval/output/latest", report, save_history=True)
    return with_history(report)


def _settings_payload() -> dict[str, Any]:
    snapshot = STATE.chat_agent.toolbox.snapshot()
    privacy_report = STATE.chat_agent.toolbox.skill.privacy_report()
    return {
        "agent_mode": STATE.agent_mode,
        "workbench_memory": snapshot,
        "privacy_items": _privacy_items(),
        "default_privacy_items": list(PRIVATE_MARKERS),
        "privacy_report": privacy_report,
        "memory_backend": _public_backend_config(),
        "current_memory": _current_memory_payload(snapshot),
    }


def _current_memory_payload(local_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    config = _memory_backend_config()
    selected = config["backend"]
    labels = {"local": "本地JSON", "mem0": "Mem0 Hosted", "mem0_hosted": "Mem0 Hosted", "mem0_sdk": "Mem0 SDK"}
    content = _mem0_memory() if selected in {"mem0", "mem0_hosted", "mem0_sdk"} else (local_snapshot or {})
    return {
        "memory_enabled": bool(config.get("memory_enabled", True)),
        "selected_engine": selected,
        "engine_label": labels.get(selected, selected),
        "content": content,
    }


def _memory_store_payload(engine: str, local_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    config = _memory_backend_config()
    inspected = str(engine or "local").strip().lower()
    if inspected not in {"local", "mem0_hosted", "mem0_sdk"}:
        raise ValueError("engine must be one of: local, mem0_hosted, mem0_sdk")
    labels = {"local": "本地Memory", "mem0_hosted": "Mem0 Hosted", "mem0_sdk": "Mem0 SDK"}
    content = local_snapshot or {} if inspected == "local" else _mem0_memory_for_engine(inspected)
    return {
        "ok": True,
        "memory_enabled": bool(config.get("memory_enabled", True)),
        "selected_engine": config["backend"],
        "engine": inspected,
        "engine_label": labels[inspected],
        "content": content,
    }


def _llm_health() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        config = MimoConfig.from_env()
    except Exception as exc:
        return {"ok": False, "stage": "config", "error": str(exc)}
    parsed = urlparse(config.base_url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    result: dict[str, Any] = {
        "ok": False,
        "base_url": config.base_url,
        "model": config.model,
        "host": host,
        "port": port,
        "timeout": min(config.timeout, 8),
    }
    try:
        dns_started = time.perf_counter()
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        result["dns_ms"] = round((time.perf_counter() - dns_started) * 1000)
        result["addresses"] = sorted({info[4][0] for info in infos})[:8]
    except Exception as exc:
        result.update({"stage": "dns", "error": str(exc), "elapsed_ms": round((time.perf_counter() - started) * 1000)})
        return result
    try:
        tcp_started = time.perf_counter()
        with socket.create_connection((host, port), timeout=min(config.timeout, 5)):
            pass
        result["tcp_ms"] = round((time.perf_counter() - tcp_started) * 1000)
    except Exception as exc:
        result.update({"stage": "tcp", "error": str(exc), "elapsed_ms": round((time.perf_counter() - started) * 1000)})
        return result
    try:
        http_started = time.perf_counter()
        request = urllib.request.Request(config.base_url, method="HEAD")
        with urllib.request.urlopen(request, timeout=min(config.timeout, 8)) as response:
            result["http_status"] = response.status
        result["http_ms"] = round((time.perf_counter() - http_started) * 1000)
        result["ok"] = True
        result["stage"] = "ok"
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
        result["http_ms"] = round((time.perf_counter() - http_started) * 1000)
        result["ok"] = exc.code in {401, 403, 404, 405}
        result["stage"] = "http_auth_or_route" if result["ok"] else "http"
        result["error"] = str(exc)
    except Exception as exc:
        result.update({"stage": "http", "error": str(exc)})
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    return result


def _chat_case(
    turns: list[dict[str, Any]],
    events: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    judge_mode: str,
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
        return evaluate_case_run(empty, "heuristic")
    domain = _infer_chat_domain(turns)
    run = {
        "id": "CHAT-SESSION",
        "title": "当前 Agent Chat",
        "domain": domain,
        "module": "自由对话记忆评估",
        "script": {
            "source": "agent_chat",
            "turn_count": len(turns),
            "messages": [turn["user"]["content"] for turn in turns],
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
    return evaluate_case_run(run, judge_mode)


def _infer_chat_domain(turns: list[dict[str, Any]]) -> str:
    return "ad_hoc_chat"


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
