from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from assist_everything_betterandbetter_skill.cases import DIMENSIONS
from assist_everything_betterandbetter_skill.skill import PRIVATE_MARKERS

from .agent import HarnessAgent
from .ab import run_current_chat_ab, run_gift_ab_script
from .evaluation import build_report, evaluate_case_run, save_report, with_history
from .judge import score_with_fallback
from .llm import MimoConfig
from .runner import run_all

LATEST = Path("eval/output/latest/eval_report.json")
PRIVACY_SETTINGS = Path("memories/workbench/_privacy.json")


class WorkbenchState:
    def __init__(self, agent_mode: str = "auto") -> None:
        self.agent_mode = agent_mode
        self.chat_agent = _new_workbench_agent(agent_mode)


STATE: WorkbenchState


class Handler(BaseHTTPRequestHandler):
    server_version = "EvalHarnessWorkbench/1.0"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(APP_HTML)
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
        elif path == "/api/run-ab-script":
            print(f"[workbench] run gift A/B script agent={body.get('agent', 'local')}")
            report = run_gift_ab_script(agent_mode=body.get("agent", "local"))
            self._send_json(report)
        elif path == "/api/run-current-ab":
            print("[workbench] run current chat A/B replay")
            report = run_current_chat_ab(
                [turn.to_dict() for turn in STATE.chat_agent.session.turns],
                STATE.chat_agent.toolbox.snapshot(),
            )
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
                        "session": STATE.chat_agent.session.to_dict(),
                    }
                )
            except Exception as exc:
                print(f"[workbench] chat error: {exc}")
                self._send_json({"error": str(exc), "memory": STATE.chat_agent.toolbox.snapshot()})
        elif path == "/api/reset-chat":
            mode = str(body.get("agent", STATE.agent_mode))
            STATE.agent_mode = mode
            STATE.chat_agent = _new_workbench_agent(mode)
            print(f"[workbench] reset chat session agent={STATE.agent_mode}")
            self._send_json({"ok": True, "session": STATE.chat_agent.session.to_dict()})
        elif path == "/api/reset-memory":
            print("[workbench] reset chat memory")
            response, call = STATE.chat_agent.toolbox.reset_memory()
            STATE.chat_agent.mark_memory_reset_boundary()
            self._send_json(
                {
                    "ok": True,
                    "response": response.to_dict(),
                    "tool_call": call.to_dict(),
                    "memory": STATE.chat_agent.toolbox.snapshot(),
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

    def _send_html(self, html: str) -> None:
        payload = html.encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
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
    agent = HarnessAgent(name="workbench-chat-agent", llm_mode=agent_mode, memory_dir="memories/workbench")
    _apply_privacy_settings(agent)
    return agent


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
    soul = Path("soul.md")
    memory = Path("memory.md")
    privacy_report = STATE.chat_agent.toolbox.skill.privacy_report()
    return {
        "agent_mode": STATE.agent_mode,
        "soul_md": soul.read_text(encoding="utf-8") if soul.exists() else "",
        "memory_md": memory.read_text(encoding="utf-8") if memory.exists() else "",
        "workbench_memory": STATE.chat_agent.toolbox.snapshot(),
        "privacy_items": _privacy_items(),
        "privacy_report": privacy_report,
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
    text = " ".join(turn.get("user", {}).get("content", "") for turn in turns)
    if any(token in text for token in ["女朋友", "礼物", "首饰", "手链", "项链", "送过"]):
        return "relationship_gift"
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


LEGACY_APP_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Eval Harness Workbench</title>
  <style>
    :root { color-scheme: light; --ink:#1c2430; --muted:#667085; --line:#d9dee7; --panel:#f7f8fb; --accent:#0f766e; --warn:#b45309; --bad:#b42318; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color:var(--ink); background:#fff; }
    header { padding:18px 24px 12px; border-bottom:1px solid var(--line); display:flex; justify-content:space-between; gap:16px; align-items:center; }
    h1 { margin:0; font-size:20px; letter-spacing:0; }
    button, select, input { font:inherit; }
    button { border:1px solid #b7c0ce; background:#fff; border-radius:6px; padding:8px 11px; cursor:pointer; }
    button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
    .tabs { display:flex; gap:6px; padding:12px 24px 0; border-bottom:1px solid var(--line); }
    .tab { border:0; border-bottom:3px solid transparent; border-radius:0; padding:10px 12px; color:var(--muted); }
    .tab.active { color:var(--ink); border-bottom-color:var(--accent); }
    main { padding:18px 24px 28px; }
    .grid { display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:12px; }
    .metric, .case, .trace, .chatbox { border:1px solid var(--line); border-radius:8px; padding:13px; background:#fff; }
    .metric .num { font-size:28px; font-weight:700; margin-top:4px; }
    .muted { color:var(--muted); font-size:13px; }
    .case { margin-bottom:12px; }
    .case-head { display:flex; justify-content:space-between; align-items:center; gap:12px; }
    .score { font-weight:700; color:var(--accent); }
    .case-layout { display:grid; grid-template-columns:320px minmax(0,1fr); gap:14px; align-items:start; }
    .case-list { display:grid; gap:8px; }
    .case-btn { border:1px solid var(--line); background:#fff; border-radius:8px; padding:11px; text-align:left; cursor:pointer; }
    .case-btn.active { border-color:var(--accent); box-shadow:0 0 0 3px #dff4ef; }
    .case-btn-title { font-weight:700; display:flex; justify-content:space-between; gap:8px; }
    .case-detail { border:1px solid var(--line); border-radius:8px; padding:14px; background:#fff; }
    .note { background:#fbfcff; border:1px solid var(--line); border-radius:7px; padding:10px; color:#44536a; margin-top:10px; }
    .field { margin-top:9px; }
    .field-label { color:var(--muted); font-size:12px; font-weight:700; }
    .field-body { margin-top:3px; font-size:13px; line-height:1.45; }
    .gain { margin-top:10px; padding:8px; border:1px solid #cfe8dc; background:#f3fbf6; border-radius:7px; font-size:13px; }
    .memory-journey { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:10px; }
    .memory-state { border:1px solid var(--line); border-radius:7px; padding:10px; background:#fff; }
    .case-score-head { display:grid; grid-template-columns:170px minmax(0,1fr); gap:10px; margin-bottom:12px; }
    .case-stats { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; margin:10px 0; }
    .event-list { display:grid; gap:8px; margin-top:10px; }
    .event-card { border:1px solid var(--line); border-radius:7px; background:#fff; padding:10px; display:grid; grid-template-columns:96px minmax(0,1fr); gap:9px; }
    .event-version { color:var(--accent); font-weight:800; }
    .dialog-list { display:grid; gap:8px; margin-top:10px; }
    .dialog-turn { border:1px solid var(--line); border-radius:7px; background:#fff; padding:10px; }
    .dialog-user { border-left:4px solid var(--warn); }
    .dialog-agent { border-left:4px solid var(--accent); }
    .effort-track { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; margin-top:10px; }
    .effort-card { border:1px solid var(--line); border-radius:7px; background:#fff; padding:10px; }
    .effort-bar { height:8px; border-radius:99px; background:#e8edf3; overflow:hidden; margin-top:8px; }
    .effort-fill { height:100%; background:var(--accent); }
    .chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
    .chip { border:1px solid var(--line); border-radius:999px; padding:3px 7px; font-size:12px; background:#fff; }
    .dims { display:grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap:8px; margin-top:10px; }
    .dim { background:#fafafa; border:1px solid var(--line); border-radius:6px; padding:8px; }
    .dim b { display:block; font-size:16px; }
    .hidden { display:none; }
    pre { white-space:pre-wrap; word-break:break-word; margin:0; font-size:12px; }
    .trace { margin-bottom:10px; }
    .trace h3 { margin:0 0 6px; font-size:15px; }
    .chat-layout { display:grid; grid-template-columns:minmax(0,1.25fr) minmax(360px,.85fr); gap:14px; align-items:start; }
    .chat-side { display:grid; gap:14px; }
    .chatlog { height:430px; overflow:auto; border:1px solid var(--line); border-radius:8px; padding:10px; background:var(--panel); }
    .msg { margin:0 0 10px; padding:10px 11px; border-radius:7px; background:#fff; border:1px solid var(--line); }
    .msg.user { border-left:4px solid var(--warn); }
    .msg.assistant { border-left:4px solid var(--accent); }
    .msg.thinking { color:var(--muted); font-style:italic; }
    .msg .content { white-space:pre-wrap; line-height:1.55; margin-top:4px; }
    .composer { display:flex; gap:8px; margin-top:10px; }
    .composer input { flex:1; border:1px solid var(--line); border-radius:6px; padding:9px; }
    .chat-eval-head { display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }
    .chat-eval-status { min-height:18px; margin-top:8px; }
    .loading-row { display:flex; align-items:center; gap:8px; color:var(--muted); }
    .spinner { width:14px; height:14px; border:2px solid #d7dee8; border-top-color:var(--accent); border-radius:50%; animation:spin .8s linear infinite; }
    .compact-stats { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; margin-top:10px; }
    .mini-metric { border:1px solid var(--line); border-radius:7px; padding:9px; background:#fff; }
    .mini-metric b { display:block; font-size:20px; margin-top:3px; }
    .chatbox .dims { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .ab-head { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }
    .ab-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:10px; }
    .ab-column { border:1px solid var(--line); border-radius:7px; background:#fff; padding:10px; min-width:0; }
    .ab-turns { display:grid; gap:7px; margin-top:10px; max-height:360px; overflow:auto; }
    .ab-turn { border:1px solid var(--line); border-radius:7px; padding:8px; background:#fbfcff; }
    .ab-user { border-left:4px solid var(--warn); }
    .ab-agent { border-left:4px solid var(--accent); }
    @keyframes spin { to { transform:rotate(360deg); } }
    @media (max-width: 900px) { .grid, .case-layout, .case-score-head, .case-stats, .dims, .chat-layout, .memory-journey, .event-card, .effort-track, .ab-grid { grid-template-columns:1fr; } header { align-items:flex-start; flex-direction:column; } }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Assist Everything BetterAndBetter Eval Harness</h1>
      <div class="muted">真实工作台：跑 case、看 trace、看 judge 分、直接和 agent harness 对话</div>
    </div>
    <div>
      <select id="agentMode"><option value="local">local agent</option><option value="mimo">Mimo agent</option><option value="auto">auto agent</option></select>
    <select id="judge"><option value="heuristic">offline judge</option><option value="mimo">Mimo judge</option><option value="external">external LLM judge</option><option value="auto">auto judge</option></select>
      <button class="primary" onclick="runPresetCases()">Run Preset Cases</button>
      <button onclick="checkMimoHealth()">Check Mimo</button>
      <span id="llmHealth" class="muted"></span>
    </div>
  </header>
  <nav class="tabs">
    <button class="tab active" onclick="setTab('dashboard', this)">Dashboard</button>
    <button class="tab" onclick="setTab('cases', this)">Cases</button>
    <button class="tab" onclick="setTab('trace', this)">Trace</button>
    <button class="tab" onclick="setTab('chat', this)">Agent Chat</button>
  </nav>
  <main>
    <section id="dashboard"></section>
    <section id="cases" class="hidden"></section>
    <section id="trace" class="hidden"></section>
    <section id="chat" class="hidden">
      <div class="chat-layout">
        <div>
          <div id="chatlog" class="chatlog"></div>
          <div class="composer">
            <input id="chatInput" placeholder="例如：展示当前记忆 / 删除孩子喜欢动物这条记忆">
            <button class="primary" onclick="sendChat()">Send</button>
            <button id="chatEvalBtn" onclick="runChatEval()">Run Eval</button>
            <button onclick="resetSession()">Reset Session</button>
            <button onclick="resetMemory()">Reset Memory</button>
          </div>
        </div>
        <div class="chat-side">
          <div class="chatbox">
            <div class="chat-eval-head">
              <div>
                <b>当前对话 Eval</b>
                <div id="chatEvalStatus" class="muted chat-eval-status">点击 Run Eval 后在这里显示当前 Agent Chat 的评分结果。</div>
              </div>
            </div>
            <div id="chatEvalPanel" class="field-body muted">暂无评分。</div>
          </div>
          <div class="chatbox">
            <b>当前记忆状态</b>
            <pre id="chatMemory">{}</pre>
          </div>
          <div class="chatbox">
            <div class="ab-head">
              <div>
                <b>礼物脚本 A/B 对比</b>
                <div id="abStatus" class="muted chat-eval-status">运行两轮送礼脚本，对比 Memory Agent 和 No-skill Baseline 的费力度。</div>
              </div>
              <button id="abRunBtn" onclick="runGiftAB()">Run A/B</button>
            </div>
            <div id="abPanel" class="field-body muted">暂无对比结果。</div>
          </div>
        </div>
      </div>
    </section>
  </main>
  <script>
    let report = null;
    let selectedCaseId = null;
    let chatEvalReport = null;
    let abReport = null;
    const dimNames = {
      reproducibility:'可复测', memory_extraction:'提取', memory_application:'应用',
      update_and_decay:'更新淘汰', transparency:'透明', result_quality:'质量'
    };
    const dimMax = {
      reproducibility:10, memory_extraction:20, memory_application:25,
      update_and_decay:20, transparency:10, result_quality:15
    };
    async function fetchConfig() {
      const cfg = await (await fetch('/api/config')).json();
      document.getElementById('agentMode').value = cfg.agent_mode || 'local';
    }
    async function fetchReport() {
      report = await (await fetch('/api/report')).json();
      renderAll();
    }
    async function runPresetCases() {
      report = await (await fetch('/api/run-preset', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({judge:document.getElementById('judge').value, agent:document.getElementById('agentMode').value})})).json();
      renderAll();
    }
    async function checkMimoHealth() {
      const el = document.getElementById('llmHealth');
      el.textContent = 'checking...';
      try {
        const data = await (await fetch('/api/llm-health')).json();
        el.textContent = data.ok ? `Mimo OK · ${data.stage} · ${data.elapsed_ms}ms` : `Mimo FAIL · ${data.stage} · ${data.error}`;
      } catch (err) {
        el.textContent = `Mimo FAIL · ${err.message}`;
      }
    }
    async function runChatEval() {
      const btn = document.getElementById('chatEvalBtn');
      const status = document.getElementById('chatEvalStatus');
      const panel = document.getElementById('chatEvalPanel');
      btn.disabled = true;
      btn.textContent = 'Scoring...';
      status.innerHTML = '<span class="loading-row"><span class="spinner"></span><span>打分中，正在汇总当前 Agent Chat trace...</span></span>';
      panel.innerHTML = '<div class="note muted">评估完成前先保留当前对话，不切换视图。</div>';
      try {
        chatEvalReport = await (await fetch('/api/run-chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({judge:document.getElementById('judge').value})})).json();
        report = chatEvalReport;
        selectedCaseId = chatEvalReport.cases?.[0]?.id || selectedCaseId;
        renderAll();
        renderChatEvalPanel(chatEvalReport);
      } catch (err) {
        status.textContent = '打分失败。';
        panel.innerHTML = `<div class="note" style="color:var(--bad)">ERROR: ${escapeHtml(err.message)}</div>`;
      } finally {
        btn.disabled = false;
        btn.textContent = 'Run Eval';
      }
    }
    async function runGiftAB() {
      const btn = document.getElementById('abRunBtn');
      const status = document.getElementById('abStatus');
      const panel = document.getElementById('abPanel');
      btn.disabled = true;
      btn.textContent = 'Running...';
      status.innerHTML = '<span class="loading-row"><span class="spinner"></span><span>正在回放两轮礼物脚本...</span></span>';
      panel.innerHTML = '<div class="note muted">回放完成后展示两条线的 transcript、费力度分解和差异。</div>';
      try {
        abReport = await (await fetch('/api/run-ab-script', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({agent:'local'})})).json();
        renderABPanel(abReport);
      } catch (err) {
        status.textContent = 'A/B 回放失败。';
        panel.innerHTML = `<div class="note" style="color:var(--bad)">ERROR: ${escapeHtml(err.message)}</div>`;
      } finally {
        btn.disabled = false;
        btn.textContent = 'Run A/B';
      }
    }
    function setTab(id, el) {
      document.querySelectorAll('main section').forEach(s => s.classList.add('hidden'));
      document.getElementById(id).classList.remove('hidden');
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      el.classList.add('active');
    }
    function renderAll() { renderDashboard(); renderCases(); renderTrace(); }
    function renderDashboard() {
      const s = report.summary, h = report.harness;
      document.getElementById('dashboard').innerHTML = `
        <div class="grid">
          <div class="metric"><div class="muted">平均分</div><div class="num">${s.config_average}</div></div>
          <div class="metric"><div class="muted">Case 数</div><div class="num">${s.case_count}</div></div>
          <div class="metric"><div class="muted">全部 > 90</div><div class="num">${s.all_cases_above_90 ? 'YES' : 'NO'}</div></div>
          <div class="metric"><div class="muted">Agent / Judge</div><div class="num" style="font-size:16px">${h.agent_mode}<br>${h.judge_mode}</div></div>
        </div>`;
    }
    function renderCases() {
      if (!report.cases.length) { document.getElementById('cases').innerHTML = '<div class="muted">暂无 Case。</div>'; return; }
      if (!selectedCaseId || !report.cases.find(c => c.id === selectedCaseId)) selectedCaseId = report.cases[0].id;
      const selected = report.cases.find(c => c.id === selectedCaseId);
      const events = memoryEvents(selected);
      const effort = selected.user_effort || {final_score:100, reduction:0, turns:[], rules:[]};
      document.getElementById('cases').innerHTML = `
        <div class="case-layout">
          <aside class="case">
            <b>历史执行 Case</b>
            <div class="case-list">${report.cases.map(c => `
              <button class="case-btn ${c.id === selectedCaseId ? 'active' : ''}" onclick="selectCase('${c.id}')">
                <div class="case-btn-title"><span>${c.id} ${escapeHtml(c.title)}</span><span class="score">${c.score}</span></div>
                <div class="muted">${escapeHtml(c.module || c.domain || '')}</div>
              </button>`).join('')}</div>
            <div class="note muted">单个对话/执行节点不单独给综合总分；Case 或 Chat Session 完成后汇总六维总分。</div>
          </aside>
          <article class="case-detail">
            <section class="case-score-head">
              <div class="metric"><div class="muted">当前 Case 总分</div><div class="num">${selected.score}</div><div class="muted">${selected.id}</div></div>
              <div class="case">
                <div class="case-head"><div><h2 style="margin:0">Case 六维评分</h2><div class="muted">${selected.id} ${escapeHtml(selected.title)}</div></div></div>
                <div class="dims">${Object.entries(selected.scores).filter(([k])=>k!=='total').map(([k,v])=>`<div class="dim"><span class="muted">${dimNames[k]}</span><b>${v} / ${dimMax[k]}</b></div>`).join('')}</div>
              </div>
            </section>
            <div class="case-stats">
              <div class="metric"><div class="muted">记忆终态</div><div class="num">${finalVersion(selected)}</div></div>
              <div class="metric"><div class="muted">费力度下降</div><div class="num">85 → ${effort.final_score}</div></div>
              <div class="metric"><div class="muted">记忆动作</div><div class="num">${selected.memory_events?.length || 0}</div></div>
              <div class="metric"><div class="muted">触发记忆的用户句</div><div class="num">${events.length}</div></div>
            </div>
            <div class="note">${caseGoal(selected)}</div>
            <h3>对话 / 执行时间线</h3>
            ${dialogTimeline(selected)}
            <h3>触发记忆变化的用户句</h3>
            <div class="event-list">${events.length ? events.map(eventCard).join('') : '<div class="muted">当前 case 未触发记忆变化。</div>'}</div>
            <h3>用户费力度趋势</h3>
            <div class="note">规则：分数越低越省力。纠错、重复说明、情绪反馈、违反记忆组合会升高费力度；有效交付、正确应用记忆、有效记忆变化会降低费力度。</div>
            <div class="event-list">${(effort.turns || []).map(effortTurnCard).join('')}</div>
          </article>
        </div>`;
    }
    function selectCase(id) { selectedCaseId = id; renderCases(); }
    function caseGoal(c) {
      if (c.id === 'CHAT-SESSION') return '评估当前 Agent Chat 对话是否形成、应用并透明展示记忆。';
      return `验证 ${escapeHtml(c.module || c.title)} 在 reset、连续任务、记忆变化和删除复测中的表现。`;
    }
    function finalVersion(c) {
      const snaps = c.snapshots || [];
      return snaps.length ? (snaps[snaps.length - 1].version || '-') : '-';
    }
    function dialogTimeline(c) {
      return `<div class="dialog-list">${(c.turns || []).map(t => `
        <div class="dialog-turn dialog-user"><div class="case-head"><b>user · ${escapeHtml(t.stage || 'chat')}</b><span class="chip">${t.memory_snapshot?.version || ''}</span></div><div class="field-body">${escapeHtml(t.user?.content || '')}</div></div>
        <div class="dialog-turn dialog-agent"><div class="case-head"><b>agent</b><span class="chip">${turnBadge(t)}</span></div><div class="chips">${turnActions(t).map(x => `<span class="chip">${escapeHtml(x)}</span>`).join('')}</div><div class="field-body">${escapeHtml(brief(t.assistant?.content || ''))}</div></div>
      `).join('')}</div>`;
    }
    function turnBadge(t) {
      const actions = (t.tool_calls || []).flatMap(call => call.output?.memory_actions || []).filter(a => a.action !== 'reset');
      if (actions.length) return `记忆变化 ${actions.length}`;
      if ((t.applied_memories || []).length) return `应用 ${(t.applied_memories || []).length} 条`;
      return '普通回复';
    }
    function turnActions(t) {
      const names = (t.tool_calls || []).map(call => call.name);
      const actions = (t.tool_calls || []).flatMap(call => call.output?.memory_actions || []).filter(a => a.action !== 'reset').map(a => a.action);
      const applied = (t.applied_memories || []).length ? ['retrieve_memory'] : [];
      return [...new Set([...names, ...actions, ...applied])].slice(0, 4);
    }
    function brief(text, max = 180) {
      const clean = String(text || '').replace(/\n{2,}/g, '\n').trim();
      return clean.length > max ? `${clean.slice(0, max)}...` : clean;
    }
    function memoryEvents(c) {
      const output = [];
      let previous = 'M0';
      (c.turns || []).forEach(t => {
        const actions = (t.tool_calls || []).flatMap(call => call.output?.memory_actions || []);
        actions.filter(a => a.action !== 'reset').forEach(a => {
          output.push({
            transition: `${previous} → ${a.version || t.memory_snapshot?.version || ''}`,
            action: a.action,
            detail: a.detail || '',
            user: t.user?.content || '',
            gain: transitionGain(a)
          });
          previous = a.version || previous;
        });
      });
      return output;
    }
    function eventCard(e) {
      return `<div class="event-card">
        <div class="event-version">${escapeHtml(e.transition)}</div>
        <div>
          <div><b>${escapeHtml(e.action)}</b> · ${escapeHtml(e.detail)}</div>
          <div class="muted">触发语：${escapeHtml(e.user)}</div>
          <div class="gain"><b>跃迁增益：</b>${escapeHtml(e.gain)}</div>
        </div>
      </div>`;
    }
    function transitionGain(action) {
      const text = action.detail || '';
      if (action.action === 'delete') return '后续检索会过滤这条记忆，避免旧偏好继续影响输出。';
      if (action.action === 'downgrade') return '旧规则降权或条件化，减少冲突场景下的误用。';
      if (text.includes('预算')) return '后续任务不再追问预算，可直接过滤不合适选项。';
      if (text.includes('紫色') || text.includes('喜欢')) return '后续推荐能主动命中对象偏好，减少用户纠错。';
      if (text.includes('一个') || text.includes('简洁')) return '后续输出会收敛，减少用户筛选成本。';
      return '这条记忆会在后续相似任务中减少重复说明。';
    }
    function effortTrend(c) {
      const events = memoryEvents(c);
      const versions = ['M0', ...events.map(e => e.transition.split('→').pop().trim())].slice(0, 4);
      if (!versions.length) return [{version:'M0', score:85, level:'高', reason:'空白状态，需要完整说明偏好和边界。'}];
      return versions.map((version, idx) => {
        const score = Math.max(18, 85 - idx * 20);
        const level = score >= 70 ? '高' : (score >= 40 ? '中' : '低');
        const reason = idx === 0 ? '空白状态，需要完整说明偏好和边界。' : (idx === versions.length - 1 ? '关键偏好已沉淀，用户只需提出任务或例外。' : '部分偏好已知，仍需补充边界。');
        return {version, score, level, reason};
      });
    }
    function effortTurnCard(e) {
      const cls = e.delta > 0 ? 'var(--bad)' : 'var(--accent)';
      return `<div class="event-card">
        <div class="event-version">${escapeHtml(e.turn_id || '')}<br><span style="color:${cls}">${e.delta > 0 ? '+' : ''}${e.delta}</span><br>${e.before} → ${e.after}</div>
        <div>
          <div><b>${escapeHtml(e.stage || '')}</b></div>
          <div class="muted">用户：${escapeHtml(e.user || '')}</div>
          <div class="field-body">原因：${(e.reasons || []).map(escapeHtml).join('；')}</div>
          <div class="chips">${(e.six_dim_gain || []).map(x => `<span class="chip">${escapeHtml(x)}</span>`).join('')}</div>
        </div>
      </div>`;
    }
    function stateTransition(turns) {
      const versions = turns.map(t => t.memory_snapshot?.version).filter(Boolean);
      if (!versions.length) return '无 snapshot';
      return versions[0] === versions[versions.length - 1] ? versions[0] : `${versions[0]} → ${versions[versions.length - 1]}`;
    }
    function memoryJourney(c) {
      const snaps = c.snapshots || [];
      if (!snaps.length) return [{name:'无快照', desc:'当前 case 没有 memory snapshot。', meta:''}];
      const pick = [snaps[0], snaps[Math.floor((snaps.length - 1)/2)], snaps[snaps.length - 1]];
      return pick.map((s, i) => ({
        name: s.version || `S${i}`,
        desc: `active ${s.active?.length || 0} / superseded ${s.superseded?.length || 0} / deleted ${s.deleted?.length || 0}`,
        meta: i === 0 ? '起点' : (i === 1 ? '中段' : '终态')
      }));
    }
    function renderTrace() {
      document.getElementById('trace').innerHTML = report.cases.map(c => `
        <div class="trace">
          <h3>${c.id} ${c.title} <span class="score">${c.score}/100</span></h3>
          ${c.turns.map(t=>`<details><summary>${t.id} · ${t.stage} · tools: ${t.tool_calls.map(x=>x.name).join(', ') || 'none'}</summary><pre>${escapeHtml(JSON.stringify(t, null, 2))}</pre></details>`).join('')}
        </div>`).join('');
    }
    function renderChatEvalPanel(data) {
      const status = document.getElementById('chatEvalStatus');
      const panel = document.getElementById('chatEvalPanel');
      const c = data?.cases?.[0];
      if (!c) {
        status.textContent = '没有可展示的评分结果。';
        panel.innerHTML = '<div class="muted">当前对话为空或 eval 未返回 case。</div>';
        return;
      }
      const effort = c.user_effort || {final_score:100, reduction:0, turns:[]};
      const checks = c.checks || {};
      status.textContent = `完成：${escapeHtml(c.id)} · ${escapeHtml(c.title || '')}`;
      panel.innerHTML = `
        <div class="compact-stats">
          <div class="mini-metric"><span class="muted">总分</span><b class="score">${c.score}</b></div>
          <div class="mini-metric"><span class="muted">费力度</span><b>${effort.final_score}</b></div>
          <div class="mini-metric"><span class="muted">下降</span><b>${effort.reduction}</b></div>
        </div>
        <div class="dims">${Object.entries(c.scores || {}).filter(([k])=>k!=='total').map(([k,v])=>`<div class="dim"><span class="muted">${dimNames[k] || k}</span><b>${v} / ${dimMax[k] || '-'}</b></div>`).join('')}</div>
        <div class="chips">
          <span class="chip">任务 ${checks.delivered_task_turns || 0}/${checks.task_turns || 0}</span>
          <span class="chip">记忆动作 ${c.memory_events?.length || 0}</span>
          <span class="chip">语义违规 ${checks.semantic_violations || 0}</span>
          <span class="chip">污染记忆 ${checks.polluted_memories || 0}</span>
        </div>
        ${c.judge?.fallback_error ? `<div class="note" style="color:var(--warn)">远端 judge 失败，已回退到 offline judge：${escapeHtml(c.judge.fallback_error)}</div>` : ''}
        <h3>费力度轨迹</h3>
        <div class="event-list">${(effort.turns || []).map(effortTurnCard).join('') || '<div class="muted">暂无费力度轨迹。</div>'}</div>
      `;
    }
    function renderABPanel(data) {
      const status = document.getElementById('abStatus');
      const panel = document.getElementById('abPanel');
      if (!data?.summary) {
        status.textContent = '没有可展示的 A/B 结果。';
        panel.innerHTML = '<div class="muted">A/B runner 未返回 summary。</div>';
        return;
      }
      const s = data.summary;
      status.textContent = `完成：第二轮费力度节省 ${s.second_session_effort_saved}，总轮数节省 ${s.turns_saved}，winner=${s.winner}`;
      panel.innerHTML = `
        <div class="compact-stats">
          <div class="mini-metric"><span class="muted">Memory 费力度</span><b>${s.memory_user_effort}</b></div>
          <div class="mini-metric"><span class="muted">Baseline 费力度</span><b>${s.baseline_user_effort}</b></div>
          <div class="mini-metric"><span class="muted">第二轮节省</span><b>${s.second_session_effort_saved}</b></div>
        </div>
        <div class="note">规则：分数越低越省力。主要看用户轮数、重复解释、纠错、缺记忆追问和违反已知约束；第二轮如果还要用户重复“上次选了什么”，会明确加成本。</div>
        <div class="ab-grid">
          ${abColumn(data.memory)}
          ${abColumn(data.baseline)}
        </div>
        <h3>费力度计算规则</h3>
        <div class="event-list">${(data.rules || []).map(rule => `<div class="event-card"><div class="event-version">${escapeHtml(rule.weight)}</div><div><b>${escapeHtml(rule.name)}</b><div class="field-body">${escapeHtml(rule.description)}</div></div></div>`).join('')}</div>
      `;
    }
    function abColumn(path) {
      const effort = path.effort || {};
      return `<div class="ab-column">
        <div class="case-head">
          <div><b>${escapeHtml(path.label || '')}</b><div class="muted">${escapeHtml(path.description || '')}</div></div>
          <span class="score">${effort.score ?? '-'}</span>
        </div>
        <div class="chips">
          <span class="chip">用户轮数 ${effort.user_turns ?? 0}</span>
          <span class="chip">重复解释 ${effort.repeated_explanations ?? 0}</span>
          <span class="chip">追问 ${effort.clarification_asks ?? 0}</span>
          <span class="chip">违规 ${effort.violations ?? 0}</span>
        </div>
        <div class="ab-turns">${abTurns(path.turns || [])}</div>
        <h3>费力度轨迹</h3>
        <div class="event-list">${(effort.trace || []).map(abEffortCard).join('')}</div>
      </div>`;
    }
    function abTurns(turns) {
      return turns.map(t => `
        <div class="ab-turn ab-user"><b>user · ${escapeHtml(t.script_session || '')}</b><div class="field-body">${escapeHtml(t.user?.content || '')}</div></div>
        <div class="ab-turn ab-agent"><b>agent</b><div class="chips">${turnActions(t).map(x => `<span class="chip">${escapeHtml(x)}</span>`).join('')}</div><div class="field-body">${escapeHtml(brief(t.assistant?.content || '', 220))}</div></div>
      `).join('');
    }
    function abEffortCard(e) {
      return `<div class="event-card">
        <div class="event-version">${escapeHtml(e.session || '')}<br>${escapeHtml(e.turn_id || '')}<br>+${e.delta}<br>${e.before} → ${e.after}</div>
        <div>
          <div class="muted">用户：${escapeHtml(e.user || '')}</div>
          <div class="field-body">原因：${(e.reasons || []).map(escapeHtml).join('；')}</div>
        </div>
      </div>`;
    }
    async function sendChat() {
      const input = document.getElementById('chatInput');
      const text = input.value.trim(); if (!text) return;
      input.value = '';
      appendMsg('user', text);
      const thinking = appendMsg('assistant thinking', '正在思考...');
      try {
        const data = await (await fetch('/api/chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({message:text, agent:document.getElementById('agentMode').value})})).json();
        updateMsg(thinking, data.error ? ('ERROR: ' + data.error) : data.turn.assistant.content, 'assistant');
        document.getElementById('chatMemory').textContent = JSON.stringify(data.memory, null, 2);
        markChatEvalStale();
      } catch (err) {
        updateMsg(thinking, 'ERROR: ' + err.message, 'assistant');
      }
    }
    async function resetSession() {
      await fetch('/api/reset-chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({agent:document.getElementById('agentMode').value})});
      document.getElementById('chatlog').innerHTML = '';
      chatEvalReport = null;
      document.getElementById('chatEvalStatus').textContent = 'Session 已重置；memory 保持不变。点击 Run Eval 后在这里显示当前 Agent Chat 的评分结果。';
      document.getElementById('chatEvalPanel').innerHTML = '暂无评分。';
    }
    async function resetMemory() {
      const data = await (await fetch('/api/reset-memory', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({agent:document.getElementById('agentMode').value})})).json();
      document.getElementById('chatMemory').textContent = JSON.stringify(data.memory || {}, null, 2);
      appendMsg('assistant', data.response?.text || '已重置 memory。');
      chatEvalReport = null;
      document.getElementById('chatEvalStatus').textContent = 'Memory 已重置；当前 session 对话未清空。点击 Run Eval 后刷新评分。';
      document.getElementById('chatEvalPanel').innerHTML = '暂无评分。';
    }
    function appendMsg(role, content) {
      const div = document.createElement('div');
      div.className = 'msg ' + role;
      const label = role.includes('user') ? 'user' : 'assistant';
      div.innerHTML = `<b>${label}</b><div class="content">${escapeHtml(content)}</div>`;
      document.getElementById('chatlog').appendChild(div);
      div.scrollIntoView({block:'end'});
      return div;
    }
    function updateMsg(div, content, role) {
      div.className = 'msg ' + role;
      const label = role.includes('user') ? 'user' : 'assistant';
      div.innerHTML = `<b>${label}</b><div class="content">${escapeHtml(content)}</div>`;
      div.scrollIntoView({block:'end'});
    }
    function markChatEvalStale() {
      if (!chatEvalReport) return;
      document.getElementById('chatEvalStatus').textContent = '对话已更新，当前评分已过期。重新点击 Run Eval 后刷新结果。';
    }
    function escapeHtml(str) { return String(str ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }
    fetchConfig().then(fetchReport);
  </script>
</body>
</html>"""


APP_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Eval Harness Workbench</title>
  <style>
    :root { color-scheme: light; --ink:#17202c; --muted:#667085; --line:#d9dee7; --panel:#f7f8fb; --accent:#0f766e; --warn:#a15c07; --bad:#b42318; --good:#0f766e; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color:var(--ink); background:#fff; }
    header { padding:16px 22px 10px; border-bottom:1px solid var(--line); display:flex; justify-content:space-between; gap:14px; align-items:center; }
    h1 { margin:0; font-size:19px; letter-spacing:0; }
    h2 { margin:0 0 10px; font-size:16px; }
    h3 { margin:14px 0 8px; font-size:14px; }
    button, select, input { font:inherit; }
    button { border:1px solid #b7c0ce; background:#fff; border-radius:6px; padding:8px 11px; cursor:pointer; }
    button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
    button:disabled { opacity:.55; cursor:default; }
    select { border:1px solid var(--line); border-radius:6px; padding:8px; background:#fff; }
    nav { display:flex; gap:6px; padding:10px 22px 0; border-bottom:1px solid var(--line); }
    .tab { border:0; border-bottom:3px solid transparent; border-radius:0; padding:10px 12px; color:var(--muted); }
    .tab.active { color:var(--ink); border-bottom-color:var(--accent); }
    main { padding:16px 22px 26px; }
    .hidden { display:none; }
    .muted { color:var(--muted); font-size:13px; }
    .panel, .metric, .case-btn, .turn-card, .note { border:1px solid var(--line); border-radius:8px; background:#fff; }
    .panel { padding:13px; }
    .note { padding:10px; background:#fbfcff; color:#44536a; margin:8px 0; }
    .grid { display:grid; gap:12px; }
    .chat-layout { display:grid; grid-template-columns:minmax(0,1.2fr) minmax(420px,.8fr); gap:14px; align-items:start; }
    .side { display:grid; gap:12px; }
    .chatlog { height:440px; overflow:auto; border:1px solid var(--line); border-radius:8px; padding:10px; background:var(--panel); }
    .msg { margin:0 0 10px; padding:10px 11px; border:1px solid var(--line); border-radius:7px; background:#fff; }
    .msg.user { border-left:4px solid var(--warn); }
    .msg.assistant { border-left:4px solid var(--accent); }
    .msg .content { white-space:pre-wrap; line-height:1.55; margin-top:4px; }
    .composer { display:flex; gap:8px; margin-top:10px; }
    .composer input { flex:1; border:1px solid var(--line); border-radius:6px; padding:9px; min-width:0; }
    .metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }
    .metric { padding:10px; min-width:0; }
    .metric b { display:block; font-size:22px; margin-top:3px; }
    .score { color:var(--accent); font-weight:800; }
    .chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
    .chip { border:1px solid var(--line); border-radius:999px; padding:3px 7px; font-size:12px; background:#fff; }
    .chip.bad { color:var(--bad); border-color:#f0c5bd; background:#fff7f5; }
    .chip.good { color:var(--good); border-color:#b9dfd7; background:#f2fbf8; }
    .dims { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:8px; margin-top:10px; }
    .dim { border:1px solid var(--line); border-radius:7px; padding:8px; background:#fafafa; }
    .dim b { display:block; font-size:15px; }
    .cases-layout { display:grid; grid-template-columns:340px minmax(0,1fr); gap:14px; align-items:start; }
    .case-list { display:grid; gap:8px; max-height:72vh; overflow:auto; }
    .case-btn { padding:10px; text-align:left; }
    .case-btn.active { border-color:var(--accent); box-shadow:0 0 0 3px #dff4ef; }
    .case-head { display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }
    .turn-list { display:grid; gap:10px; margin-top:10px; }
    .turn-card { padding:10px; background:#fff; }
    .turn-meta { display:flex; justify-content:space-between; gap:8px; align-items:flex-start; }
    .dialog { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:8px; }
    .bubble { border:1px solid var(--line); border-radius:7px; padding:8px; background:#fbfcff; min-width:0; }
    .bubble.user { border-left:4px solid var(--warn); }
    .bubble.agent { border-left:4px solid var(--accent); }
    .body { white-space:pre-wrap; line-height:1.45; font-size:13px; margin-top:4px; }
    .subgrid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin-top:8px; }
    .mini { border:1px solid var(--line); border-radius:7px; padding:8px; background:#fff; }
    .event { border-left:3px solid var(--accent); padding-left:8px; margin-top:6px; }
    pre { white-space:pre-wrap; word-break:break-word; margin:0; font-size:12px; }
    textarea { width:100%; min-height:180px; border:1px solid var(--line); border-radius:7px; padding:9px; font:12px ui-monospace, SFMono-Regular, Menlo, monospace; }
    .loading-row { display:flex; align-items:center; gap:8px; color:var(--muted); }
    .spinner { width:14px; height:14px; border:2px solid #d7dee8; border-top-color:var(--accent); border-radius:50%; animation:spin .8s linear infinite; }
    @keyframes spin { to { transform:rotate(360deg); } }
    @media (max-width: 980px) { .chat-layout, .cases-layout, .dialog, .subgrid, .dims, .metrics { grid-template-columns:1fr; } header { align-items:flex-start; flex-direction:column; } }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Memory Eval Workbench</h1>
      <div class="muted">当前对话、历史 eval 结果、统计和配置分开管理；Agent Chat 与 History Evals 使用同一套 eval 输出。</div>
    </div>
    <div>
      <select id="agentMode"><option value="local">local agent</option><option value="mimo">Mimo agent</option><option value="auto">auto agent</option></select>
      <select id="judge"><option value="heuristic">offline judge</option><option value="mimo">Mimo judge</option><option value="external">external LLM judge</option><option value="auto">auto judge</option></select>
      <button onclick="checkMimoHealth()">Check Mimo</button>
      <span id="llmHealth" class="muted"></span>
    </div>
  </header>
  <nav>
    <button class="tab active" onclick="setTab('chat', this)">Agent Chat</button>
    <button class="tab" onclick="setTab('cases', this)">History Evals</button>
    <button class="tab" onclick="setTab('stats', this)">统计</button>
    <button class="tab" onclick="setTab('settings', this)">设置</button>
  </nav>
  <main>
    <section id="chat">
      <div class="chat-layout">
        <div>
          <div id="chatlog" class="chatlog"></div>
          <div class="composer">
            <input id="chatInput" placeholder="输入当前对话，例如：帮我给女朋友选个礼物">
            <button class="primary" onclick="sendChat()">Send</button>
            <button id="chatEvalBtn" onclick="runChatEval()">Run Eval</button>
            <button onclick="resetSession()">Reset Session</button>
            <button onclick="resetMemory()">Reset Memory</button>
          </div>
        </div>
        <div class="side">
          <div class="panel">
            <div class="case-head"><div><h2>当前对话 Eval</h2><div id="chatEvalStatus" class="muted">点击 Run Eval 后显示当前会话评分。</div></div></div>
            <div id="chatEvalPanel" class="muted">暂无评分。</div>
          </div>
          <div class="panel">
            <div class="case-head"><div><h2>当前礼物对话 A/B</h2><div id="abStatus" class="muted">当当前对话已经满意确认礼物后，回放到 no-memory baseline 做对比。</div></div><button id="abRunBtn" onclick="runGiftAB()">Run A/B</button></div>
            <div id="abPanel" class="muted">暂无对比结果。</div>
          </div>
          <div class="panel"><h2>当前 Memory</h2><pre id="chatMemory">{}</pre></div>
        </div>
      </div>
    </section>
    <section id="cases" class="hidden">
      <div class="cases-layout">
        <aside class="panel">
          <div class="case-head"><div><h2>History Evals</h2><div id="historyHint" class="muted">保留每次 Run Eval / Run Preset 的历史 eval 结果。</div></div><button class="primary" onclick="runPresetCases()">Run Preset</button></div>
          <div id="caseList" class="case-list"></div>
        </aside>
        <article id="caseDetail" class="panel muted">暂无历史 eval。</article>
      </div>
    </section>
    <section id="stats" class="hidden"></section>
    <section id="settings" class="hidden">
      <div class="grid">
        <div class="panel"><h2>Agent 配置</h2><div id="settingsAgent" class="muted"></div></div>
        <div class="panel"><h2>soul.md</h2><textarea id="soulMd" readonly></textarea></div>
        <div class="panel"><h2>memory.md</h2><textarea id="memoryMd" readonly></textarea></div>
        <div class="panel"><h2>Workbench Memory</h2><pre id="settingsMemory">{}</pre></div>
      </div>
    </section>
  </main>
  <script>
    let report = null;
    let settings = null;
    let selectedCaseKey = null;
    let chatEvalReport = null;
    const dimNames = {reproducibility:'可复测', memory_extraction:'提取', memory_application:'应用', update_and_decay:'更新淘汰', transparency:'透明', result_quality:'质量'};
    const dimMax = {reproducibility:10, memory_extraction:20, memory_application:25, update_and_decay:20, transparency:10, result_quality:15};

    async function fetchConfig() {
      const cfg = await (await fetch('/api/config')).json();
      document.getElementById('agentMode').value = cfg.agent_mode || 'local';
    }
    async function fetchReport() {
      report = await (await fetch('/api/report')).json();
      renderCases();
      renderStats();
    }
    async function fetchSettings() {
      settings = await (await fetch('/api/settings')).json();
      document.getElementById('settingsAgent').textContent = `agent_mode=${settings.agent_mode || ''}`;
      document.getElementById('soulMd').value = settings.soul_md || '';
      document.getElementById('memoryMd').value = settings.memory_md || '未配置 memory.md';
      document.getElementById('settingsMemory').textContent = JSON.stringify(settings.workbench_memory || {}, null, 2);
    }
    function setTab(id, el) {
      document.querySelectorAll('main section').forEach(s => s.classList.add('hidden'));
      document.getElementById(id).classList.remove('hidden');
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      el.classList.add('active');
      if (id === 'settings') fetchSettings();
    }
    async function runPresetCases() {
      const list = document.getElementById('caseList');
      list.innerHTML = '<div class="loading-row"><span class="spinner"></span><span>正在运行 preset evals...</span></div>';
      report = await (await fetch('/api/run-preset', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({judge:document.getElementById('judge').value, agent:document.getElementById('agentMode').value})})).json();
      selectedCaseKey = null;
      renderCases();
      renderStats();
    }
    async function checkMimoHealth() {
      const el = document.getElementById('llmHealth');
      el.textContent = 'checking...';
      try {
        const data = await (await fetch('/api/llm-health')).json();
        el.textContent = data.ok ? `Mimo OK · ${data.stage} · ${data.elapsed_ms}ms` : `Mimo FAIL · ${data.stage} · ${data.error}`;
      } catch (err) {
        el.textContent = `Mimo FAIL · ${err.message}`;
      }
    }
    async function sendChat() {
      const input = document.getElementById('chatInput');
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      appendMsg('user', text);
      const thinking = appendMsg('assistant', '正在思考...');
      try {
        const data = await (await fetch('/api/chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({message:text, agent:document.getElementById('agentMode').value})})).json();
        updateMsg(thinking, data.error ? ('ERROR: ' + data.error) : data.turn.assistant.content, 'assistant');
        document.getElementById('chatMemory').textContent = JSON.stringify(data.memory || {}, null, 2);
        document.getElementById('chatEvalStatus').textContent = '对话已更新，当前评分需重新 Run Eval。';
      } catch (err) {
        updateMsg(thinking, 'ERROR: ' + err.message, 'assistant');
      }
    }
    async function runChatEval() {
      const btn = document.getElementById('chatEvalBtn');
      const status = document.getElementById('chatEvalStatus');
      const panel = document.getElementById('chatEvalPanel');
      btn.disabled = true;
      btn.textContent = 'Scoring...';
      status.innerHTML = '<span class="loading-row"><span class="spinner"></span><span>正在统一 eval 当前会话...</span></span>';
      panel.innerHTML = '';
      try {
        chatEvalReport = await (await fetch('/api/run-chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({judge:document.getElementById('judge').value})})).json();
        report = chatEvalReport;
        const c = chatEvalReport.cases?.[0];
        status.textContent = c ? `完成：${c.score}/100` : '没有可评估 eval。';
        panel.innerHTML = c ? renderEvalCase(c, {compact:true}) : '<div class="muted">当前对话为空。</div>';
        renderCases();
        renderStats();
      } catch (err) {
        status.textContent = '打分失败。';
        panel.innerHTML = `<div class="note" style="color:var(--bad)">ERROR: ${escapeHtml(err.message)}</div>`;
      } finally {
        btn.disabled = false;
        btn.textContent = 'Run Eval';
      }
    }
    async function resetSession() {
      await fetch('/api/reset-chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({agent:document.getElementById('agentMode').value})});
      document.getElementById('chatlog').innerHTML = '';
      document.getElementById('chatEvalStatus').textContent = 'Session 已重置；memory 保持不变。';
      document.getElementById('chatEvalPanel').innerHTML = '暂无评分。';
    }
    async function resetMemory() {
      const data = await (await fetch('/api/reset-memory', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({agent:document.getElementById('agentMode').value})})).json();
      appendMsg('assistant', data.response?.text || '已重置 memory。');
      document.getElementById('chatMemory').textContent = JSON.stringify(data.memory || {}, null, 2);
      document.getElementById('chatEvalStatus').textContent = 'Memory 已重置；Run Eval 后刷新评分。';
    }
    async function runGiftAB() {
      const btn = document.getElementById('abRunBtn');
      const status = document.getElementById('abStatus');
      const panel = document.getElementById('abPanel');
      btn.disabled = true;
      status.innerHTML = '<span class="loading-row"><span class="spinner"></span><span>正在回放当前 chat 主线...</span></span>';
      try {
        const data = await (await fetch('/api/run-current-ab', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({})})).json();
        if (!data.ok) {
          status.textContent = '当前对话还不能跑 A/B。';
          panel.innerHTML = `<div class="note">${escapeHtml(data.reason || '需要先完成礼物选择并确认满意。')}</div>`;
          return;
        }
        const s = data.summary || {};
        status.textContent = `当前 chat replay 完成：节省 ${s.effort_saved}，少 ${s.turns_saved} 轮。`;
        panel.innerHTML = `
          <div class="metrics">
            <div class="metric"><span class="muted">Memory 费力度</span><b>${s.memory_user_effort}</b></div>
            <div class="metric"><span class="muted">Baseline 费力度</span><b>${s.baseline_user_effort}</b></div>
            <div class="metric"><span class="muted">节省</span><b>${s.effort_saved}</b></div>
          </div>
          <div class="note">选定礼物：${escapeHtml(s.selected_gift || '')}。Baseline 最后推荐：${escapeHtml(s.baseline_last_pick || '未命中')}。${s.baseline_reached_same_gift ? 'Baseline 在满意节点前命中同一礼物。' : 'Baseline 未在满意节点前命中同一礼物，因此计入额外解释轮。'}</div>`;
      } catch (err) {
        status.textContent = 'A/B 失败。';
        panel.innerHTML = `<div class="note" style="color:var(--bad)">ERROR: ${escapeHtml(err.message)}</div>`;
      } finally {
        btn.disabled = false;
      }
    }
    function appendMsg(role, content) {
      const div = document.createElement('div');
      div.className = 'msg ' + role;
      div.innerHTML = `<b>${role === 'user' ? 'user' : 'assistant'}</b><div class="content">${escapeHtml(content)}</div>`;
      document.getElementById('chatlog').appendChild(div);
      div.scrollIntoView({block:'end'});
      return div;
    }
    function updateMsg(div, content, role) {
      div.className = 'msg ' + role;
      div.innerHTML = `<b>${role === 'user' ? 'user' : 'assistant'}</b><div class="content">${escapeHtml(content)}</div>`;
      div.scrollIntoView({block:'end'});
    }
    function historyCases() {
      const out = [];
      const history = report?.history || (report ? [report] : []);
      history.forEach(run => (run.cases || []).forEach(c => out.push({run, c, key:`${run.run_id || 'latest'}::${c.id}`})));
      return out;
    }
    function renderCases() {
      const rows = historyCases();
      const list = document.getElementById('caseList');
      if (!rows.length) {
        list.innerHTML = '<div class="muted">暂无历史 eval。</div>';
        document.getElementById('caseDetail').innerHTML = '暂无历史 eval。';
        return;
      }
      if (!selectedCaseKey || !rows.find(r => r.key === selectedCaseKey)) selectedCaseKey = rows[0].key;
      list.innerHTML = rows.map(r => `
        <button class="case-btn ${r.key === selectedCaseKey ? 'active' : ''}" onclick="selectCase('${escapeAttr(r.key)}')">
          <div class="case-head"><b>${escapeHtml(r.c.id)} ${escapeHtml(r.c.title || '')}</b><span class="score">${r.c.score}</span></div>
          <div class="muted">${escapeHtml(r.run.source || r.run.harness?.eval_source || '')} · ${formatTime(r.run.created_at)}</div>
          <div class="chips"><span class="chip">费力度 ${r.c.user_effort?.final_score ?? '-'}</span><span class="chip good">节省 ${r.c.user_effort?.saved_score ?? 0}</span></div>
        </button>`).join('');
      const selected = rows.find(r => r.key === selectedCaseKey);
      document.getElementById('caseDetail').innerHTML = renderEvalCase(selected.c, {run:selected.run});
    }
    function selectCase(key) { selectedCaseKey = key; renderCases(); }
    function renderStats() {
      const rows = historyCases();
      const latest = report?.summary || {};
      document.getElementById('stats').innerHTML = `
        <div class="metrics">
          <div class="metric"><span class="muted">历史 eval 数</span><b>${rows.length}</b></div>
          <div class="metric"><span class="muted">最新平均分</span><b>${latest.config_average ?? '-'}</b></div>
          <div class="metric"><span class="muted">最新平均费力度</span><b>${latest.effort_average ?? '-'}</b></div>
        </div>
        <div class="panel" style="margin-top:12px"><h2>历史运行</h2>
          <div class="turn-list">${(report?.history || []).map(run => `<div class="turn-card"><div class="case-head"><b>${escapeHtml(run.run_id || '')}</b><span class="score">${run.summary?.config_average ?? '-'}</span></div><div class="muted">${escapeHtml(run.source || '')} · ${formatTime(run.created_at)}</div></div>`).join('') || '<div class="muted">暂无历史运行。</div>'}</div>
        </div>`;
    }
    function renderEvalCase(c, opts = {}) {
      const effort = c.user_effort || {};
      const checks = c.checks || {};
      return `
        <div>
          <div class="case-head"><div><h2>${escapeHtml(c.id || '')} ${escapeHtml(c.title || '')}</h2><div class="muted">${escapeHtml(c.module || c.domain || '')}</div></div><span class="score">${c.score ?? '-'}/100</span></div>
          <div class="metrics">
            <div class="metric"><span class="muted">六维总分</span><b>${c.score ?? '-'}</b><div class="muted">六个维度的综合质量分，越高越好。</div></div>
            <div class="metric"><span class="muted">用户费力度</span><b>${effort.final_score ?? '-'}</b><div class="muted">累计成本点数，越低越省力。</div></div>
            <div class="metric"><span class="muted">记忆节省</span><b>${effort.saved_score ?? effort.reduction ?? 0}</b><div class="muted">因应用/更新记忆预计少解释的成本。</div></div>
          </div>
          <div class="note">费力度按加法计算：用户轮数、输入长度、追问、重复说明、纠错、不满、语义违规都会加成本；记忆应用和记忆变化只计入“记忆节省”，不再把成本扣成负数。</div>
          <div class="dims">${Object.entries(c.scores || {}).filter(([k])=>k!=='total').map(([k,v]) => `<div class="dim"><span class="muted">${dimNames[k] || k}</span><b>${v} / ${dimMax[k] || '-'}</b></div>`).join('')}</div>
          <div class="chips">
            <span class="chip">任务交付 ${checks.delivered_task_turns || 0}/${checks.task_turns || 0}</span>
            <span class="chip">记忆动作 ${c.memory_events?.length || 0}</span>
            <span class="chip ${checks.semantic_violations ? 'bad' : 'good'}">语义违规 ${checks.semantic_violations || 0}</span>
            <span class="chip ${checks.repeated_memory_turns ? 'bad' : 'good'}">重复说明 ${checks.repeated_memory_turns || 0}</span>
          </div>
          <h3>统一轨迹</h3>
          <div class="turn-list">${(c.eval_timeline || []).map(timelineCard).join('') || '<div class="muted">暂无轨迹。</div>'}</div>
        </div>`;
    }
    function timelineCard(t) {
      const effort = t.effort || {};
      const memory = t.memory || {};
      const actions = memory.actions || [];
      const applied = memory.applied || [];
      return `<div class="turn-card">
        <div class="turn-meta"><b>${escapeHtml(t.turn_id || '')} · ${escapeHtml(t.stage || '')}</b><span class="chip">M ${escapeHtml(memory.snapshot_version || '')}</span></div>
        <div class="dialog">
          <div class="bubble user"><b>user</b><div class="body">${escapeHtml(t.user || '')}</div></div>
          <div class="bubble agent"><b>agent</b><div class="body">${escapeHtml(brief(t.assistant || '', 260))}</div></div>
        </div>
        <div class="subgrid">
          <div class="mini"><b>Memory</b><div class="muted">${escapeHtml(memory.explanation || '')}</div>${applied.map(m => `<div class="event"><span class="chip">${escapeHtml(m.type)}</span><div class="body">${escapeHtml(m.content)}</div></div>`).join('')}${actions.map(a => `<div class="event"><span class="chip">${escapeHtml(a.action)}</span><div class="body">${escapeHtml(a.detail || '')}</div></div>`).join('')}</div>
          <div class="mini"><b>费力度</b><div class="body">成本 ${effort.before ?? 0} → ${effort.after ?? 0}，本轮 +${effort.delta ?? 0}</div><div class="body">节省 ${effort.saved_before ?? 0} → ${effort.saved_after ?? 0}，本轮 +${effort.saved_delta ?? 0}</div><div class="muted">${escapeHtml(t.evaluation?.explanation || '')}</div></div>
        </div>
      </div>`;
    }
    function brief(text, max = 180) {
      const clean = String(text || '').replace(/\n{2,}/g, '\n').trim();
      return clean.length > max ? `${clean.slice(0, max)}...` : clean;
    }
    function formatTime(value) {
      if (!value) return '';
      try { return new Date(value).toLocaleString(); } catch { return value; }
    }
    function escapeAttr(str) { return String(str ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;'); }
    function escapeHtml(str) { return String(str ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }
    fetchConfig().then(fetchReport).then(fetchSettings);
  </script>
</body>
</html>"""
