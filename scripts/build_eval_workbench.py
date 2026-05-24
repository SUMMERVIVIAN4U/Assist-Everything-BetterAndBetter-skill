from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "eval" / "output" / "latest" / "eval_report.json"
OUT = ROOT / "reports" / "eval-workbench.html"


def build(report_path: Path = REPORT, out_path: Path = OUT) -> Path:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(data), encoding="utf-8")
    return out_path


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Assist Everything BetterAndBetter Eval Workbench</title>
  <style>
    :root {{ --bg:#f6f7f9; --panel:#fff; --text:#172033; --muted:#68758a; --line:#dfe6ef; --blue:#1769e0; --green:#13864e; --orange:#d36b18; --red:#c74444; --radius:8px; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; }}
    .page {{ max-width:1440px; margin:0 auto; padding:22px; }}
    header {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:16px; }}
    h1 {{ margin:0; font-size:25px; }}
    h2 {{ margin:0 0 12px; font-size:17px; }}
    .sub,.muted {{ color:var(--muted); font-size:13px; line-height:1.45; }}
    .stats {{ display:grid; grid-template-columns:repeat(4, minmax(130px,1fr)); gap:10px; min-width:560px; }}
    .card,.stat {{ background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); box-shadow:0 8px 22px rgba(24,38,66,.07); }}
    .stat {{ padding:12px; }}
    .stat b {{ display:block; margin-top:6px; font-size:24px; }}
    .layout {{ display:grid; grid-template-columns:330px minmax(0,1fr); gap:14px; align-items:start; }}
    .card {{ padding:15px; }}
    .case-list {{ display:grid; gap:8px; }}
    button.case {{ width:100%; border:1px solid var(--line); border-radius:var(--radius); background:#fff; padding:12px; text-align:left; cursor:pointer; }}
    button.case.active {{ border-color:var(--blue); box-shadow:0 0 0 3px #e4efff; }}
    .row {{ display:flex; justify-content:space-between; gap:10px; align-items:center; }}
    .score {{ color:var(--blue); font-weight:900; }}
    .chips {{ display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; }}
    .chip {{ display:inline-flex; align-items:center; min-height:24px; padding:2px 8px; border-radius:999px; background:#eef4ff; color:var(--blue); font-size:12px; font-weight:700; }}
    .chip.green {{ background:#eaf8f0; color:var(--green); }} .chip.orange {{ background:#fff2e8; color:var(--orange); }} .chip.red {{ background:#fff0f0; color:var(--red); }}
    .rounds {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:12px; }}
    .round {{ border:1px solid var(--line); border-radius:var(--radius); padding:13px; background:#fff; min-height:285px; }}
    .field {{ margin-top:10px; }} .label {{ color:var(--muted); font-size:12px; font-weight:700; margin-bottom:4px; }} .body {{ font-size:13px; line-height:1.45; }}
    .gain {{ margin-top:10px; border-left:4px solid var(--green); background:#f3fbf6; padding:9px; border-radius:6px; font-size:13px; }}
    .sections {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:14px; }}
    .memory {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }}
    .mem {{ border:1px solid var(--line); border-radius:var(--radius); padding:12px; background:#fff; }}
    .bar {{ height:9px; border-radius:999px; background:#e8eef5; overflow:hidden; margin-top:8px; }} .bar span {{ display:block; height:100%; background:var(--green); }}
    .compare {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }} .box {{ border:1px solid var(--line); border-radius:var(--radius); padding:12px; background:#fff; }}
    .box.off {{ border-color:#f0c3c3; background:#fffafa; }} .box.on {{ border-color:#b8dfc8; background:#f7fdf9; }}
    .scores {{ display:grid; grid-template-columns:repeat(3,1fr); gap:9px; }}
    .dim {{ border:1px solid var(--line); border-radius:var(--radius); padding:10px; background:#fff; }}
    @media(max-width:1100px) {{ header,.layout,.sections,.rounds,.memory,.scores {{ display:grid; grid-template-columns:1fr; }} .stats {{ min-width:0; grid-template-columns:repeat(2,1fr); }} }}
  </style>
</head>
<body>
<div class="page">
  <header>
    <div><h1>Assist Everything BetterAndBetter Eval Workbench</h1><div class="sub">自动 eval 结果：三轮 Case、记忆状态跃迁、模块 ON/OFF 对比与六维评分。</div></div>
    <div class="stats">
      <div class="stat"><span class="muted">评测对象</span><b>Skill</b><span class="muted">当前模块配置</span></div>
      <div class="stat"><span class="muted">历史 Case</span><b id="case-count"></b><span class="muted">已运行</span></div>
      <div class="stat"><span class="muted">配置平均分</span><b id="avg"></b><span class="muted">Case 汇总</span></div>
      <div class="stat"><span class="muted">核心增益</span><b id="effort"></b><span class="muted">用户费力度</span></div>
    </div>
  </header>
  <div class="layout">
    <aside class="card"><h2>历史执行 Case</h2><div id="case-list" class="case-list"></div><p class="muted">Round 不显示综合总分；Case 三轮完成后才汇总六维总分。</p></aside>
    <main>
      <section class="card"><div class="row"><div><h2 id="title"></h2><div id="goal" class="muted"></div></div><div class="score" id="case-score"></div></div><div id="rounds" class="rounds"></div></section>
      <section class="sections"><div class="card"><h2>Memory 状态跃迁</h2><div id="memory" class="memory"></div></div><div class="card"><h2>模块消融对比</h2><div id="compare" class="compare"></div></div></section>
      <section class="card" style="margin-top:14px"><h2>Case 六维评分</h2><div id="scores" class="scores"></div></section>
    </main>
  </div>
</div>
<script>
const DATA = {payload};
const dims = [
  ['reproducibility','可复测性',10], ['memory_extraction','有效记忆提取',20], ['memory_application','记忆应用效果',25],
  ['update_and_decay','记忆更新与淘汰',20], ['transparency','用户控制与透明度',10], ['result_quality','结果质量与可用性',15]
];
let selected = DATA.cases[0].id;
function chip(text) {{ const cls = text.includes('删除') ? 'red' : text.includes('更新') || text.includes('降权') ? 'orange' : 'green'; return `<span class="chip ${{cls}}">${{text}}</span>`; }}
function pct(v,max) {{ return Math.max(0, Math.min(100, Math.round(v/max*100))); }}
function render() {{
  document.getElementById('case-count').textContent = DATA.summary.case_count;
  document.getElementById('avg').textContent = DATA.summary.config_average;
  document.getElementById('effort').textContent = DATA.summary.effort_reduction;
  const list = document.getElementById('case-list');
  list.innerHTML = DATA.cases.map(c => `<button class="case ${{c.id===selected?'active':''}}" onclick="selected='${{c.id}}';render()"><div class="row"><b>${{c.id}} ${{c.title}}</b><span class="score">${{c.score}}/100</span></div><div class="muted">${{c.module}}</div></button>`).join('');
  const c = DATA.cases.find(x => x.id === selected);
  document.getElementById('title').textContent = `${{c.id}} ${{c.title}}`;
  document.getElementById('goal').textContent = c.goal;
  document.getElementById('case-score').textContent = `${{c.score}}/100`;
  document.getElementById('rounds').innerHTML = c.rounds.map(r => `<article class="round"><div class="row"><h2>${{r.name}}：${{r.title}}</h2>${{chip(r.action)}}</div><div class="field"><div class="label">用户输入</div><div class="body">${{r.input}}</div></div><div class="field"><div class="label">Skill 动作</div><div class="body">${{r.skill}}</div></div><div class="field"><div class="label">记忆动作</div><div class="body">${{r.memory}}</div></div><div class="gain"><b>增益：</b>${{r.gain}}</div></article>`).join('');
  document.getElementById('memory').innerHTML = c.memory_journey.map(m => `<div class="mem"><b>${{m.state}} ${{m.title}}</b><div class="muted">${{m.reason}}</div><div class="bar"><span style="width:${{m.effort_score}}%"></span></div><div class="muted">${{m.effort_level}}费力度：${{m.effort_score}}</div></div>`).join('');
  document.getElementById('compare').innerHTML = `<div class="box off"><b>${{c.ablation.module}} OFF</b><ul>${{c.ablation.off.map(x=>`<li>${{x}}</li>`).join('')}}</ul></div><div class="box on"><b>${{c.ablation.module}} ON</b><ul>${{c.ablation.on.map(x=>`<li>${{x}}</li>`).join('')}}</ul></div>`;
  document.getElementById('scores').innerHTML = dims.map(([k,n,max]) => `<div class="dim"><div class="muted">${{n}}</div><h2>${{c.scores[k]}} / ${{max}}</h2><div class="bar"><span style="width:${{pct(c.scores[k],max)}}%"></span></div></div>`).join('');
}}
render();
</script>
</body>
</html>"""


if __name__ == "__main__":
    print(build())
