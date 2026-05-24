from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .agent import HarnessAgent
from .runner import run_all

LATEST = Path("eval/output/latest/eval_report.json")


class WorkbenchState:
    def __init__(self, agent_mode: str = "auto") -> None:
        self.agent_mode = agent_mode
        self.chat_agent = HarnessAgent(name="workbench-chat-agent", llm_mode=agent_mode)


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
            print(f"[workbench] run eval agent={body.get('agent', 'local')} judge={body.get('judge', 'auto')}")
            report = run_all(judge_mode=body.get("judge", "auto"), agent_mode=body.get("agent", "local"))
            self._send_json(report)
        elif path == "/api/chat":
            message = str(body.get("message", "")).strip()
            mode = str(body.get("agent", STATE.agent_mode))
            if mode != STATE.agent_mode:
                STATE.agent_mode = mode
                STATE.chat_agent = HarnessAgent(name="workbench-chat-agent", llm_mode=mode)
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
            STATE.chat_agent = HarnessAgent(name="workbench-chat-agent", llm_mode=mode)
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
    .rounds { display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:10px; margin-top:12px; }
    .round { background:var(--panel); border:1px solid var(--line); border-radius:7px; padding:10px; min-height:116px; }
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
    @media (max-width: 900px) { .grid, .rounds, .dims, .chat-layout { grid-template-columns:1fr; } header { align-items:flex-start; flex-direction:column; } }
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
      <button class="primary" onclick="runEval()">Run Eval</button>
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
    async function runEval() {
      report = await (await fetch('/api/run', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({judge:document.getElementById('judge').value, agent:document.getElementById('agentMode').value})})).json();
      renderAll();
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
      document.getElementById('cases').innerHTML = report.cases.map(c => `
        <article class="case">
          <div class="case-head"><div><b>${c.id} ${c.title}</b><div class="muted">${c.module}</div></div><div class="score">${c.score}/100</div></div>
          <div class="dims">${Object.entries(c.scores).filter(([k])=>k!=='total').map(([k,v])=>`<div class="dim"><span class="muted">${dimNames[k]}</span><b>${v}</b></div>`).join('')}</div>
          <div class="rounds">${c.rounds.map(r=>`<div class="round"><b>${r.name} · ${r.title}</b><p>${r.highlight}</p><div class="chips">${r.actions.map(a=>`<span class="chip">${a}</span>`).join('')}</div><div class="muted">${r.gain}</div></div>`).join('')}</div>
        </article>`).join('');
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
