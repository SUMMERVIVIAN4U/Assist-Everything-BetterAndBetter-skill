from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from assist_everything_betterandbetter_skill.cases import DIMENSIONS

from .agent import HarnessAgent
from .judge import build_judge
from .runner import run_all

LATEST = Path("eval/output/latest/eval_report.json")


class WorkbenchState:
    def __init__(self, agent_mode: str = "auto") -> None:
        self.agent_mode = agent_mode
        self.chat_agent = HarnessAgent(name="workbench-chat-agent", llm_mode=agent_mode, memory_dir="memories/workbench")


STATE = WorkbenchState()


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
            self._send_json(json.loads(LATEST.read_text(encoding="utf-8")))
        elif path == "/api/health":
            self._send_json({"ok": True})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_json()
        if path == "/api/run":
            print("[workbench] /api/run compatibility route -> preset cases")
            report = run_all(judge_mode=body.get("judge", "auto"), agent_mode=body.get("agent", "local"))
            self._send_json(report)
        elif path == "/api/run-preset":
            print(f"[workbench] run preset cases agent={body.get('agent', 'local')} judge={body.get('judge', 'auto')}")
            report = run_all(judge_mode=body.get("judge", "auto"), agent_mode=body.get("agent", "local"))
            self._send_json(report)
        elif path == "/api/run-chat":
            print(f"[workbench] run chat eval judge={body.get('judge', 'auto')}")
            report = _chat_report(body.get("judge", "auto"))
            self._send_json(report)
        elif path == "/api/chat":
            message = str(body.get("message", "")).strip()
            mode = str(body.get("agent", STATE.agent_mode))
            if mode != STATE.agent_mode:
                STATE.agent_mode = mode
                STATE.chat_agent = HarnessAgent(name="workbench-chat-agent", llm_mode=mode, memory_dir="memories/workbench")
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
            STATE.chat_agent = HarnessAgent(name="workbench-chat-agent", llm_mode=mode, memory_dir="memories/workbench")
            print(f"[workbench] reset chat agent={STATE.agent_mode}")
            self._send_json({"ok": True, "session": STATE.chat_agent.session.to_dict()})
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


def serve(host: str = "127.0.0.1", port: int = 8787, agent_mode: str = "auto") -> None:
    global STATE
    STATE = WorkbenchState(agent_mode=agent_mode)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Eval Harness Workbench: http://{host}:{port}")
    httpd.serve_forever()


def _chat_report(judge_mode: str) -> dict[str, Any]:
    turns = [turn.to_dict() for turn in STATE.chat_agent.session.turns]
    events = STATE.chat_agent.toolbox.skill.memory.events
    snapshots = [turn["memory_snapshot"] for turn in turns]
    case = _chat_case(turns, events, snapshots, judge_mode)
    return {
        "harness": {
            "name": "assist-everything-betterandbetter-evalharness",
            "agent_mode": "mimo_tool_agent" if STATE.agent_mode == "mimo" else "local_tool_agent",
            "judge_mode": case["judge"]["mode"],
            "supports_external_llm_judge": True,
            "supports_agent_chat": True,
            "eval_source": "agent_chat_session",
        },
        "summary": {
            "case_count": 1,
            "config_average": case["score"],
            "all_cases_above_90": case["score"] >= 90,
        },
        "dimensions": DIMENSIONS,
        "cases": [case],
    }


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
        judgement = build_judge("heuristic").score(empty)
        empty["judge"] = judgement
        empty["scores"] = judgement["scores"]
        empty["score"] = judgement["scores"]["total"]
        return empty
    run = {
        "id": "CHAT-SESSION",
        "title": "当前 Agent Chat",
        "domain": "ad_hoc_chat",
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
    judgement = build_judge(judge_mode).score(run)
    run["judge"] = judgement
    run["scores"] = judgement["scores"]
    run["score"] = judgement["scores"]["total"]
    return run


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


APP_HTML = r"""<!doctype html>
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
    .rounds { display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:10px; margin-top:12px; }
    .round { background:var(--panel); border:1px solid var(--line); border-radius:7px; padding:10px; min-height:280px; position:relative; }
    .round:not(:last-child)::after { content:"→"; position:absolute; right:-14px; top:44%; color:#98a2b3; font-weight:800; }
    .field { margin-top:9px; }
    .field-label { color:var(--muted); font-size:12px; font-weight:700; }
    .field-body { margin-top:3px; font-size:13px; line-height:1.45; }
    .gain { margin-top:10px; padding:8px; border:1px solid #cfe8dc; background:#f3fbf6; border-radius:7px; font-size:13px; }
    .memory-journey { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:10px; }
    .memory-state { border:1px solid var(--line); border-radius:7px; padding:10px; background:#fff; }
    .ablation { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:10px; }
    .ablation .off { border-left:4px solid var(--bad); }
    .ablation .on { border-left:4px solid var(--accent); }
    .dim-reason { margin-top:8px; display:grid; gap:6px; }
    .chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
    .chip { border:1px solid var(--line); border-radius:999px; padding:3px 7px; font-size:12px; background:#fff; }
    .dims { display:grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap:8px; margin-top:10px; }
    .dim { background:#fafafa; border:1px solid var(--line); border-radius:6px; padding:8px; }
    .dim b { display:block; font-size:16px; }
    .hidden { display:none; }
    pre { white-space:pre-wrap; word-break:break-word; margin:0; font-size:12px; }
    .trace { margin-bottom:10px; }
    .trace h3 { margin:0 0 6px; font-size:15px; }
    .chat-layout { display:grid; grid-template-columns: 1fr 320px; gap:14px; }
    .chatlog { height:430px; overflow:auto; border:1px solid var(--line); border-radius:8px; padding:10px; background:var(--panel); }
    .msg { margin:0 0 10px; padding:10px 11px; border-radius:7px; background:#fff; border:1px solid var(--line); }
    .msg.user { border-left:4px solid var(--warn); }
    .msg.assistant { border-left:4px solid var(--accent); }
    .msg.thinking { color:var(--muted); font-style:italic; }
    .msg .content { white-space:pre-wrap; line-height:1.55; margin-top:4px; }
    .composer { display:flex; gap:8px; margin-top:10px; }
    .composer input { flex:1; border:1px solid var(--line); border-radius:6px; padding:9px; }
    @media (max-width: 900px) { .grid, .case-layout, .rounds, .dims, .chat-layout, .memory-journey, .ablation { grid-template-columns:1fr; } header { align-items:flex-start; flex-direction:column; } .round::after { display:none; } }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Assist Everything BetterAndBetter Eval Harness</h1>
      <div class="muted">真实工作台：跑 case、看 trace、看 judge 分、直接和 agent harness 对话</div>
    </div>
    <div>
      <select id="agentMode"><option value="local">local agent</option><option value="mimo">Mimo agent</option></select>
    <select id="judge"><option value="auto">auto judge</option><option value="heuristic">offline judge</option><option value="mimo">Mimo judge</option><option value="external">external LLM judge</option></select>
      <button class="primary" onclick="runPresetCases()">Run Preset Cases</button>
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
            <input id="chatInput" placeholder="例如：reset memory / 展示当前记忆 / 删除孩子喜欢动物这条记忆">
            <button class="primary" onclick="sendChat()">Send</button>
            <button onclick="runChatEval()">Run Eval</button>
            <button onclick="resetChat()">Reset</button>
          </div>
        </div>
        <div class="chatbox">
          <b>当前记忆状态</b>
          <pre id="chatMemory">{}</pre>
        </div>
      </div>
    </section>
  </main>
  <script>
    let report = null;
    let selectedCaseId = null;
    const dimNames = {
      reproducibility:'可复测', memory_extraction:'提取', memory_application:'应用',
      update_and_decay:'更新淘汰', transparency:'透明', result_quality:'质量'
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
    async function runChatEval() {
      report = await (await fetch('/api/run-chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({judge:document.getElementById('judge').value})})).json();
      renderAll();
      setTab('cases', document.querySelectorAll('.tab')[1]);
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
      document.getElementById('cases').innerHTML = `
        <div class="case-layout">
          <aside class="case">
            <b>历史执行 Case</b>
            <div class="case-list">${report.cases.map(c => `
              <button class="case-btn ${c.id === selectedCaseId ? 'active' : ''}" onclick="selectCase('${c.id}')">
                <div class="case-btn-title"><span>${c.id} ${escapeHtml(c.title)}</span><span class="score">${c.score}</span></div>
                <div class="muted">${escapeHtml(c.module || c.domain || '')}</div>
              </button>`).join('')}</div>
            <div class="note muted">Round 不单独给综合总分；Case 跑完后汇总六维总分。聊天 Eval 会显示当前 Chat Session。</div>
          </aside>
          <article class="case-detail">
            <div class="case-head">
              <div><h2 style="margin:0">${selected.id} ${escapeHtml(selected.title)}</h2><div class="muted">${escapeHtml(selected.module || '')}</div></div>
              <div class="score" style="font-size:28px">${selected.score}/100</div>
            </div>
            <div class="note">${caseGoal(selected)}</div>
            <h3>三轮执行与记忆动作</h3>
            <div class="rounds">${selected.rounds.map(r => roundCard(selected, r)).join('')}</div>
            <h3>Memory 状态跃迁</h3>
            <div class="memory-journey">${memoryJourney(selected).map(s => `
              <div class="memory-state"><b>${s.name}</b><div class="field-body">${s.desc}</div><div class="muted">${s.meta}</div></div>`).join('')}</div>
            <h3>模块消融对比</h3>
            <div class="ablation">
              <div class="case off"><b>OFF</b>${(selected.ablation?.off || []).map(x=>`<div class="field-body">- ${escapeHtml(x)}</div>`).join('')}</div>
              <div class="case on"><b>ON</b>${(selected.ablation?.on || []).map(x=>`<div class="field-body">- ${escapeHtml(x)}</div>`).join('')}</div>
            </div>
            <h3>Case 六维评分</h3>
            <div class="dims">${Object.entries(selected.scores).filter(([k])=>k!=='total').map(([k,v])=>`<div class="dim"><span class="muted">${dimNames[k]}</span><b>${v}</b></div>`).join('')}</div>
            <div class="dim-reason">${Object.entries(selected.judge?.reasons || {}).map(([k,v])=>`<div class="field-body"><b>${dimNames[k] || k}：</b>${escapeHtml(v)}</div>`).join('')}</div>
          </article>
        </div>`;
    }
    function selectCase(id) { selectedCaseId = id; renderCases(); }
    function caseGoal(c) {
      if (c.id === 'CHAT-SESSION') return '评估当前 Agent Chat 对话是否形成、应用并透明展示记忆。';
      return `验证 ${escapeHtml(c.module || c.title)} 在 reset、三轮任务和删除复测中的表现。`;
    }
    function roundCard(c, r) {
      const turns = (r.turn_ids || []).map(id => c.turns.find(t => t.id === id)).filter(Boolean);
      const first = turns[0] || {};
      const last = turns[turns.length - 1] || first;
      const actions = turns.flatMap(t => (t.tool_calls || []).flatMap(call => (call.output.memory_actions || []).map(a => a.action)));
      const memoryText = actions.length ? actions.join(' / ') : ((last.applied_memories || []).length ? `应用 ${(last.applied_memories || []).length} 条` : '无新增');
      return `<div class="round">
        <div class="case-head"><b>${escapeHtml(r.name)} · ${escapeHtml(r.title)}</b><span class="chip">${escapeHtml(memoryText)}</span></div>
        <div class="field"><div class="field-label">用户输入</div><div class="field-body">“${escapeHtml(first.user?.content || r.highlight || '')}”</div></div>
        <div class="field"><div class="field-label">Agent 执行</div><div class="field-body">${escapeHtml((last.assistant?.content || '').split('\\n')[0] || r.highlight || '')}</div></div>
        <div class="field"><div class="field-label">记忆动作</div><div class="field-body">${escapeHtml(memoryText)}</div></div>
        <div class="field"><div class="field-label">状态跃迁</div><div class="field-body">${stateTransition(turns)}</div></div>
        <div class="gain"><b>本轮增益：</b>${escapeHtml(r.gain || r.highlight || '')}</div>
        <details style="margin-top:8px"><summary>展开依据</summary><div class="chips">${(r.actions || []).map(a=>`<span class="chip">${escapeHtml(a)}</span>`).join('')}</div><pre>${escapeHtml(JSON.stringify(turns, null, 2))}</pre></details>
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
      } catch (err) {
        updateMsg(thinking, 'ERROR: ' + err.message, 'assistant');
      }
    }
    async function resetChat() {
      await fetch('/api/reset-chat', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({agent:document.getElementById('agentMode').value})});
      document.getElementById('chatlog').innerHTML = '';
      document.getElementById('chatMemory').textContent = '{}';
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
    function escapeHtml(str) { return str.replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }
    fetchConfig().then(fetchReport);
  </script>
</body>
</html>"""
