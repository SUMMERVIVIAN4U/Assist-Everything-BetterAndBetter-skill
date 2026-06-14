# Mem0 Large Memory Performance Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Workbench `Performance Demo` tab that demonstrates Mem0 large-memory write/search/ranking/reset behavior with safe dry-run defaults and isolated real-run user IDs.

**Architecture:** Add a focused backend module `evalharness/mem0_performance.py` for deterministic demo data, dry-run execution, real-run execution, metrics, and reset behavior. Keep `evalharness/server.py` as a thin HTTP adapter and keep UI work in `evalharness/static/workbench.html`, `workbench.js`, and `workbench.css`.

**Tech Stack:** Python stdlib `unittest`, `http.server`, existing `HostedMem0Client` / `Mem0SdkClient`, vanilla HTML/CSS/JS Workbench.

---

## File Structure

- Create `evalharness/mem0_performance.py`
  - Owns constants, deterministic dataset generation, query generation, metrics, dry-run runner, real-run runner, latest report persistence, and demo-user reset.
- Create `tests/test_mem0_performance_demo.py`
  - Covers generator determinism, dry-run report shape, demo user isolation, and reset scoping.
- Modify `evalharness/server.py`
  - Adds three routes:
    - `GET /api/mem0-performance-demo/latest`
    - `POST /api/mem0-performance-demo/run`
    - `POST /api/mem0-performance-demo/reset`
  - Delegates implementation to `evalharness.mem0_performance`.
- Modify `evalharness/static/workbench.html`
  - Adds top-level `Performance Demo` tab and section.
- Modify `evalharness/static/workbench.js`
  - Adds render/run/reset/latest functions for the new tab.
- Modify `evalharness/static/workbench.css`
  - Adds small layout helpers for demo controls, progress, metric cards, and timeline.
- Modify `skill/SKILL.md`
  - Documents the new Workbench capability and the isolation rule.

## Task 1: Deterministic Data And Dry-Run Runner

**Files:**
- Create: `evalharness/mem0_performance.py`
- Create: `tests/test_mem0_performance_demo.py`

- [ ] **Step 1: Write failing tests for deterministic data and dry-run report**

Add this to `tests/test_mem0_performance_demo.py`:

```python
import unittest

from evalharness.mem0_performance import DEMO_USER_ID, generate_demo_memories, run_performance_demo


class Mem0PerformanceDemoTest(unittest.TestCase):
    def test_generate_demo_memories_is_deterministic(self):
        first = generate_demo_memories(scale=5, seed=7)
        second = generate_demo_memories(scale=5, seed=7)

        self.assertEqual(first, second)
        self.assertEqual(5, len(first))
        self.assertEqual("demo_mem_000001", first[0]["id"])
        self.assertIn("content", first[0])
        self.assertIn("updated_at", first[0])

    def test_dry_run_report_has_metrics_examples_and_demo_user(self):
        report = run_performance_demo(engine="mem0_hosted", mode="dry_run", scale=1000, query_count=5)

        self.assertTrue(report["ok"])
        self.assertEqual("dry_run", report["mode"])
        self.assertEqual("mem0_hosted", report["engine"])
        self.assertEqual(1000, report["scale"])
        self.assertEqual(DEMO_USER_ID, report["demo_user_id"])
        self.assertGreater(report["metrics"]["write_qps"], 0)
        self.assertGreaterEqual(report["metrics"]["search_p95_ms"], report["metrics"]["search_p50_ms"])
        self.assertEqual(5, len(report["examples"]))
        self.assertEqual("score_time", report["examples"][0]["top_k"][0]["retrieval_rank_strategy"])
        self.assertEqual([], report["reset"]["errors"])
```

- [ ] **Step 2: Run tests and verify they fail because the module does not exist**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_mem0_performance_demo.py'
```

Expected: `ModuleNotFoundError: No module named 'evalharness.mem0_performance'`.

- [ ] **Step 3: Implement minimal deterministic generator and dry-run runner**

Create `evalharness/mem0_performance.py`:

```python
from __future__ import annotations

import random
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEMO_USER_ID = "workbench-demo-large-memory"
LATEST_PERFORMANCE_REPORT = Path("eval/output/latest/mem0_performance_demo.json")
ALLOWED_ENGINES = {"mem0_hosted", "mem0_sdk"}
ALLOWED_MODES = {"dry_run", "real_run"}
ALLOWED_SCALES = {1000, 10000, 50000}

_DOMAINS = [
    ("life_family_travel", ["上海", "亲子", "少走路", "室内", "动物园"]),
    ("work_report", ["老板", "结论", "风险", "负责人", "下一步"]),
    ("study_plan", ["复习", "例题", "自测", "高频考点", "番茄钟"]),
    ("research_review", ["文献综述", "方法类别", "数据集", "局限", "可复现"]),
    ("shopping_preference", ["预算", "礼物", "材质", "颜色", "实用"]),
]


def generate_demo_memories(*, scale: int, seed: int = 42) -> list[dict[str, Any]]:
    _validate_scale(scale)
    rng = random.Random(seed)
    base_time = datetime(2026, 6, 11, tzinfo=timezone.utc)
    rows: list[dict[str, Any]] = []
    for index in range(scale):
        scope, tags = _DOMAINS[index % len(_DOMAINS)]
        chosen = rng.sample(tags, k=3)
        updated_at = base_time + timedelta(seconds=index)
        rows.append(
            {
                "id": f"demo_mem_{index + 1:06d}",
                "content": f"用户在{scope}场景关注{'、'.join(chosen)}，偏好编号 {index + 1}。",
                "scope": scope,
                "tags": chosen,
                "created_at": base_time.isoformat(),
                "updated_at": updated_at.isoformat(),
                "status": "active",
            }
        )
    return rows


def generate_demo_queries(*, query_count: int, seed: int = 42) -> list[str]:
    rng = random.Random(seed + 1000)
    queries = []
    for index in range(max(1, min(query_count, 100))):
        scope, tags = _DOMAINS[index % len(_DOMAINS)]
        chosen = rng.sample(tags, k=2)
        queries.append(f"{scope} {' '.join(chosen)}")
    return queries


def run_performance_demo(
    *,
    engine: str,
    mode: str,
    scale: int,
    query_count: int = 20,
    client: Any | None = None,
) -> dict[str, Any]:
    _validate_engine(engine)
    _validate_mode(mode)
    _validate_scale(scale)
    started = datetime.now(timezone.utc)
    phase_reports: list[dict[str, Any]] = []

    memories, generate_ms = _measure(lambda: generate_demo_memories(scale=scale))
    phase_reports.append({"name": "generate", "elapsed_ms": generate_ms, "ok": True})

    if mode == "real_run":
        write_count, write_ms, errors = _real_write(client, memories)
    else:
        write_count, write_ms, errors = _dry_write(memories)
    phase_reports.append({"name": "write", "elapsed_ms": write_ms, "ok": not errors, "count": write_count, "errors": errors[:3]})

    queries = generate_demo_queries(query_count=query_count)
    examples, latencies, search_errors = _dry_search(memories, queries)
    phase_reports.append({"name": "search", "elapsed_ms": round(sum(latencies), 2), "ok": not search_errors, "count": len(queries), "errors": search_errors[:3]})

    report = {
        "ok": not errors and not search_errors,
        "run_id": f"perf_{started.strftime('%Y%m%d_%H%M%S')}",
        "engine": engine,
        "mode": mode,
        "scale": scale,
        "demo_user_id": DEMO_USER_ID,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "phases": phase_reports,
        "metrics": {
            "write_qps": round(write_count / max(write_ms / 1000, 0.001), 2),
            "search_p50_ms": _percentile(latencies, 50),
            "search_p95_ms": _percentile(latencies, 95),
            "error_rate": round((len(errors) + len(search_errors)) / max(write_count + len(queries), 1), 4),
        },
        "examples": examples,
        "reset": {"found_count": scale if mode == "dry_run" else 0, "deleted_count": scale if mode == "dry_run" else 0, "errors": []},
    }
    save_latest_report(report)
    return report


def latest_report() -> dict[str, Any]:
    if not LATEST_PERFORMANCE_REPORT.exists():
        return {"ok": False, "stage": "empty", "error": "No performance demo has run yet."}
    import json

    return json.loads(LATEST_PERFORMANCE_REPORT.read_text(encoding="utf-8"))


def save_latest_report(report: dict[str, Any]) -> None:
    import json

    LATEST_PERFORMANCE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    LATEST_PERFORMANCE_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _dry_write(memories: list[dict[str, Any]]) -> tuple[int, float, list[str]]:
    _, elapsed_ms = _measure(lambda: len(memories))
    return len(memories), max(elapsed_ms, 1.0), []


def _real_write(client: Any, memories: list[dict[str, Any]]) -> tuple[int, float, list[str]]:
    if client is None:
        return 0, 0.0, ["real_run requires a configured Mem0 client"]
    errors: list[str] = []
    start = time.perf_counter()
    written = 0
    for memory in memories:
        try:
            client.add_text(memory["content"], context="mem0_large_memory_performance_demo")
            written += 1
        except Exception as exc:
            errors.append(str(exc))
            if len(errors) >= 10:
                break
    return written, round((time.perf_counter() - start) * 1000, 2), errors


def _dry_search(memories: list[dict[str, Any]], queries: list[str]) -> tuple[list[dict[str, Any]], list[float], list[str]]:
    examples = []
    latencies = []
    for query in queries:
        start = time.perf_counter()
        terms = [term for term in query.split() if term]
        ranked = []
        for memory in memories:
            haystack = " ".join([memory["content"], memory["scope"], *memory["tags"]])
            hits = sum(1 for term in terms if term in haystack)
            if hits:
                ranked.append(
                    {
                        "content": memory["content"],
                        "scope": memory["scope"],
                        "score": round(min(1.0, 0.5 + hits * 0.2), 4),
                        "retrieval_score": round(min(1.0, 0.5 + hits * 0.2), 4),
                        "retrieval_rank_strategy": "score_time",
                        "updated_at": memory["updated_at"],
                    }
                )
        ranked.sort(key=lambda item: (item["retrieval_score"], item["updated_at"]), reverse=True)
        latencies.append(round((time.perf_counter() - start) * 1000, 2))
        examples.append({"query": query, "top_k": ranked[:5]})
    return examples, latencies, []


def _measure(fn):
    start = time.perf_counter()
    value = fn()
    return value, round((time.perf_counter() - start) * 1000, 2)


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    sorted_values = sorted(values)
    if percentile == 50:
        return round(statistics.median(sorted_values), 2)
    index = min(len(sorted_values) - 1, max(0, round((percentile / 100) * len(sorted_values)) - 1))
    return round(sorted_values[index], 2)


def _validate_engine(engine: str) -> None:
    if engine not in ALLOWED_ENGINES:
        raise ValueError(f"engine must be one of {sorted(ALLOWED_ENGINES)}")


def _validate_mode(mode: str) -> None:
    if mode not in ALLOWED_MODES:
        raise ValueError(f"mode must be one of {sorted(ALLOWED_MODES)}")


def _validate_scale(scale: int) -> None:
    if scale not in ALLOWED_SCALES:
        raise ValueError(f"scale must be one of {sorted(ALLOWED_SCALES)}")
```

- [ ] **Step 4: Run tests and verify Task 1 passes**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_mem0_performance_demo.py'
```

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 5: Commit Task 1**

```bash
git add evalharness/mem0_performance.py tests/test_mem0_performance_demo.py
git commit -m "feat: add mem0 performance dry run"
```

## Task 2: Demo User Isolation And Reset

**Files:**
- Modify: `evalharness/mem0_performance.py`
- Modify: `tests/test_mem0_performance_demo.py`

- [ ] **Step 1: Write failing tests for real-run demo user isolation and reset scope**

Append to `tests/test_mem0_performance_demo.py`:

```python
from assist_everything_betterandbetter_skill.mem0_backend import Mem0Config
from evalharness.mem0_performance import config_for_demo_user, reset_demo_memory


class Mem0PerformanceIsolationTest(unittest.TestCase):
    def test_config_for_demo_user_never_reuses_chat_user_id(self):
        original = Mem0Config(enabled=True, base_url="https://mem0.example", api_key="k", user_id="workbench-user")

        demo = config_for_demo_user(original)

        self.assertEqual(DEMO_USER_ID, demo.user_id)
        self.assertNotEqual(original.user_id, demo.user_id)
        self.assertEqual(original.base_url, demo.base_url)
        self.assertEqual(original.api_key, demo.api_key)

    def test_reset_demo_memory_uses_demo_scoped_client(self):
        class FakeClient:
            def __init__(self):
                self.deleted = False

            def delete_all(self, page_size=200):
                self.deleted = True
                return {"mode": "user_scoped", "found_count": 3, "deleted_count": 3, "errors": []}

        client = FakeClient()

        result = reset_demo_memory(client)

        self.assertTrue(client.deleted)
        self.assertTrue(result["ok"])
        self.assertEqual(DEMO_USER_ID, result["demo_user_id"])
        self.assertEqual(3, result["deleted_count"])
```

- [ ] **Step 2: Run tests and verify they fail with missing functions**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_mem0_performance_demo.py'
```

Expected: import failure for `config_for_demo_user` or `reset_demo_memory`.

- [ ] **Step 3: Implement isolation helpers**

Add imports and functions to `evalharness/mem0_performance.py`:

```python
from dataclasses import replace

from assist_everything_betterandbetter_skill.mem0_backend import Mem0Config


def config_for_demo_user(config: Mem0Config) -> Mem0Config:
    return replace(config, user_id=DEMO_USER_ID)


def reset_demo_memory(client: Any | None) -> dict[str, Any]:
    if client is None:
        return {"ok": False, "stage": "config", "demo_user_id": DEMO_USER_ID, "found_count": 0, "deleted_count": 0, "errors": ["Mem0 client is not configured"]}
    try:
        result = client.delete_all(page_size=200)
    except Exception as exc:
        return {"ok": False, "stage": "delete_all", "demo_user_id": DEMO_USER_ID, "found_count": 0, "deleted_count": 0, "errors": [str(exc)]}
    errors = result.get("errors", []) if isinstance(result, dict) else []
    return {
        "ok": not errors,
        "stage": "delete_all",
        "demo_user_id": DEMO_USER_ID,
        "found_count": int(result.get("found_count", 0)) if isinstance(result, dict) else 0,
        "deleted_count": int(result.get("deleted_count", 0)) if isinstance(result, dict) else 0,
        "errors": errors,
        "result": result,
    }
```

- [ ] **Step 4: Run tests and verify Task 2 passes**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_mem0_performance_demo.py'
```

Expected: all tests in the file pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add evalharness/mem0_performance.py tests/test_mem0_performance_demo.py
git commit -m "feat: isolate mem0 performance demo user"
```

## Task 3: Workbench Performance Demo API

**Files:**
- Modify: `evalharness/server.py`
- Modify: `tests/test_mem0_performance_demo.py`

- [ ] **Step 1: Write failing tests for server helper functions**

Append to `tests/test_mem0_performance_demo.py`:

```python
from unittest.mock import patch

from evalharness import server


class Mem0PerformanceApiTest(unittest.TestCase):
    def test_run_mem0_performance_demo_defaults_to_dry_run(self):
        with patch("evalharness.server._mem0_client_for_backend") as client_factory:
            result = server._run_mem0_performance_demo({"engine": "mem0_hosted", "scale": 1000, "query_count": 3})

        client_factory.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual("dry_run", result["mode"])
        self.assertEqual(1000, result["scale"])

    def test_reset_mem0_performance_demo_uses_demo_config(self):
        class FakeClient:
            def __init__(self, config):
                self.config = config

            def delete_all(self, page_size=200):
                return {"mode": "user_scoped", "found_count": 1, "deleted_count": 1, "errors": []}

        with patch("evalharness.server._mem0_client_for_backend", side_effect=lambda backend, config: FakeClient(config)):
            result = server._reset_mem0_performance_demo({"engine": "mem0_hosted"})

        self.assertTrue(result["ok"])
        self.assertEqual(DEMO_USER_ID, result["demo_user_id"])
```

- [ ] **Step 2: Run tests and verify they fail with missing server helpers**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_mem0_performance_demo.py'
```

Expected: `AttributeError` for `_run_mem0_performance_demo`.

- [ ] **Step 3: Add server imports, routes, and helper functions**

Modify `evalharness/server.py` imports:

```python
from .mem0_performance import (
    config_for_demo_user,
    latest_report as latest_performance_report,
    reset_demo_memory,
    run_performance_demo,
)
```

Add GET route in `do_GET` after `/api/current-memory`:

```python
        elif path == "/api/mem0-performance-demo/latest":
            self._send_json(latest_performance_report())
```

Add POST routes in `do_POST` before `else`:

```python
        elif path == "/api/mem0-performance-demo/run":
            self._send_json(_run_mem0_performance_demo(body))
        elif path == "/api/mem0-performance-demo/reset":
            self._send_json(_reset_mem0_performance_demo(body))
```

Add helpers near `_reset_mem0_memory()`:

```python
def _run_mem0_performance_demo(body: dict[str, Any]) -> dict[str, Any]:
    engine = str(body.get("engine") or _memory_backend_config()["backend"])
    if engine == "mem0":
        engine = "mem0_hosted"
    mode = str(body.get("mode") or "dry_run")
    scale = int(body.get("scale") or 1000)
    query_count = int(body.get("query_count") or 20)
    client = None
    if mode == "real_run":
        config = config_for_demo_user(_mem0_config())
        client = _mem0_client_for_backend(engine, config)
    try:
        return run_performance_demo(engine=engine, mode=mode, scale=scale, query_count=query_count, client=client)
    except Exception as exc:
        return {"ok": False, "stage": "run", "error": str(exc)}


def _reset_mem0_performance_demo(body: dict[str, Any]) -> dict[str, Any]:
    engine = str(body.get("engine") or _memory_backend_config()["backend"])
    if engine == "mem0":
        engine = "mem0_hosted"
    try:
        config = config_for_demo_user(_mem0_config())
        client = _mem0_client_for_backend(engine, config)
        return reset_demo_memory(client)
    except Exception as exc:
        return {"ok": False, "stage": "reset", "error": str(exc)}
```

- [ ] **Step 4: Run tests and verify Task 3 passes**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_mem0_performance_demo.py'
```

Expected: all tests in the file pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add evalharness/server.py tests/test_mem0_performance_demo.py
git commit -m "feat: expose mem0 performance demo api"
```

## Task 4: Workbench Performance Demo UI

**Files:**
- Modify: `evalharness/static/workbench.html`
- Modify: `evalharness/static/workbench.js`
- Modify: `evalharness/static/workbench.css`

- [ ] **Step 1: Add the tab and section HTML**

Modify `evalharness/static/workbench.html`.

Add nav button after `设置`:

```html
    <button class="tab" onclick="setTab('performance', this)">Performance Demo</button>
```

Add this section before `</main>`:

```html
    <section id="performance" class="hidden">
      <div class="perf-layout">
        <aside class="panel perf-controls">
          <h2>Mem0 Performance Demo</h2>
          <div class="muted">端到端演示超大记忆下的写入、检索、排序和清理。</div>
          <label>记忆引擎</label>
          <select id="perfEngine"><option value="mem0_hosted">Mem0</option><option value="mem0_sdk">Mem0 SDK</option></select>
          <label>运行模式</label>
          <select id="perfMode"><option value="dry_run">Dry Run</option><option value="real_run">Real Run</option></select>
          <label>数据规模</label>
          <select id="perfScale"><option value="1000">1k</option><option value="10000">10k</option><option value="50000">50k</option></select>
          <label>Query 轮数</label>
          <input id="perfQueryCount" type="number" min="1" max="100" value="20">
          <div class="privacy-actions">
            <button id="perfRunBtn" class="primary" onclick="runPerformanceDemo()">Run Demo</button>
            <button onclick="resetPerformanceDemo()">Reset Demo Memory</button>
          </div>
          <div id="perfStatus" class="status-line">尚未运行。</div>
        </aside>
        <article class="panel perf-report">
          <div class="case-head">
            <div><h2>Live Report</h2><div id="perfSummary" class="muted">等待运行。</div></div>
            <button onclick="fetchPerformanceLatest()">刷新报告</button>
          </div>
          <div id="perfMetrics" class="metrics"></div>
          <div id="perfTimeline" class="perf-timeline"></div>
          <h3>Top-K Evidence</h3>
          <div id="perfExamples" class="turn-list"></div>
        </article>
        <aside class="panel">
          <h2>Raw JSON</h2>
          <pre id="perfRaw">{}</pre>
        </aside>
      </div>
    </section>
```

- [ ] **Step 2: Add JS functions**

Append to `evalharness/static/workbench.js` before the initial bootstrapping code at the bottom:

```javascript
    async function fetchPerformanceLatest() {
      const data = await (await fetch('/api/mem0-performance-demo/latest')).json();
      renderPerformanceReport(data);
    }
    async function runPerformanceDemo() {
      const btn = document.getElementById('perfRunBtn');
      btn.disabled = true;
      document.getElementById('perfStatus').textContent = 'running...';
      const payload = {
        engine: document.getElementById('perfEngine').value,
        mode: document.getElementById('perfMode').value,
        scale: Number(document.getElementById('perfScale').value),
        query_count: Number(document.getElementById('perfQueryCount').value)
      };
      try {
        const data = await (await fetch('/api/mem0-performance-demo/run', {
          method:'POST',
          headers:{'content-type':'application/json'},
          body:JSON.stringify(payload)
        })).json();
        renderPerformanceReport(data);
        document.getElementById('perfStatus').textContent = data.ok ? '完成' : `失败：${data.error || data.stage || 'unknown'}`;
      } catch (err) {
        document.getElementById('perfStatus').textContent = `失败：${err.message}`;
      } finally {
        btn.disabled = false;
      }
    }
    async function resetPerformanceDemo() {
      document.getElementById('perfStatus').textContent = 'resetting demo memory...';
      const data = await (await fetch('/api/mem0-performance-demo/reset', {
        method:'POST',
        headers:{'content-type':'application/json'},
        body:JSON.stringify({engine:document.getElementById('perfEngine').value})
      })).json();
      document.getElementById('perfStatus').textContent = data.ok
        ? `Demo Memory 已清理：${data.deleted_count || 0}/${data.found_count || 0}`
        : `清理失败：${data.error || (data.errors || []).join('; ')}`;
      const currentRaw = safeJsonParse(document.getElementById('perfRaw').textContent);
      renderPerformanceReport({...currentRaw, reset:data});
    }
    function renderPerformanceReport(report) {
      const data = report || {};
      document.getElementById('perfRaw').textContent = JSON.stringify(data, null, 2);
      if (data.ok === false && data.stage === 'empty') {
        document.getElementById('perfSummary').textContent = data.error || '暂无报告。';
        document.getElementById('perfMetrics').innerHTML = '';
        document.getElementById('perfTimeline').innerHTML = '';
        document.getElementById('perfExamples').innerHTML = '<div class="muted">暂无样例。</div>';
        return;
      }
      document.getElementById('perfSummary').textContent = `${data.engine || '-'} · ${data.mode || '-'} · ${data.scale || 0} 条 · ${data.demo_user_id || '-'}`;
      const metrics = data.metrics || {};
      document.getElementById('perfMetrics').innerHTML = [
        metricHtml('Write QPS', metrics.write_qps),
        metricHtml('P50', `${metrics.search_p50_ms ?? 0}ms`),
        metricHtml('P95', `${metrics.search_p95_ms ?? 0}ms`),
        metricHtml('Error Rate', metrics.error_rate ?? 0)
      ].join('');
      document.getElementById('perfTimeline').innerHTML = (data.phases || []).map(phase => `
        <div class="perf-phase ${phase.ok === false ? 'bad' : ''}">
          <b>${escapeHtml(phase.name || '')}</b>
          <span>${phase.elapsed_ms ?? 0}ms</span>
        </div>
      `).join('');
      document.getElementById('perfExamples').innerHTML = (data.examples || []).slice(0, 5).map(example => `
        <div class="turn-card">
          <div class="turn-meta"><b>${escapeHtml(example.query || '')}</b><span class="muted">${(example.top_k || []).length} hits</span></div>
          <div class="chips">${(example.top_k || []).slice(0, 5).map(item => `<span class="chip">${escapeHtml(String(item.retrieval_score ?? item.score ?? 0))}</span>`).join('')}</div>
          <div class="body">${escapeHtml((example.top_k || []).map(item => item.content).join('\\n'))}</div>
        </div>
      `).join('') || '<div class="muted">暂无样例。</div>';
    }
    function metricHtml(label, value) {
      return `<div class="metric"><span class="muted">${escapeHtml(label)}</span><b>${escapeHtml(String(value ?? '-'))}</b></div>`;
    }
    function safeJsonParse(text) {
      try { return JSON.parse(text || '{}'); } catch (_err) { return {}; }
    }
```

Modify `setTab` in `evalharness/static/workbench.js` so selecting the tab loads latest:

```javascript
      if (id === 'settings') fetchSettings();
      if (id === 'performance') fetchPerformanceLatest();
```

- [ ] **Step 3: Add CSS**

Append to `evalharness/static/workbench.css`:

```css
    .perf-layout { display:grid; grid-template-columns:260px minmax(0,1fr) 340px; gap:14px; align-items:start; }
    .perf-controls { display:grid; gap:8px; }
    .perf-controls label { color:var(--muted); font-size:13px; margin-top:4px; }
    .perf-report { min-width:0; }
    .perf-timeline { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:8px; margin:12px 0; }
    .perf-phase { border:1px solid var(--line); border-radius:7px; padding:8px; background:#fbfcff; min-height:58px; }
    .perf-phase.bad { border-color:#f0c5bd; background:#fff7f5; color:var(--bad); }
    .perf-phase b, .perf-phase span { display:block; }
    @media (max-width: 1180px) { .perf-layout { grid-template-columns:1fr; } .perf-timeline { grid-template-columns:repeat(2,minmax(0,1fr)); } }
```

- [ ] **Step 4: Run JS syntax check**

Run:

```bash
node --check evalharness/static/workbench.js
```

Expected: no output and exit code 0.

- [ ] **Step 5: Commit Task 4**

```bash
git add evalharness/static/workbench.html evalharness/static/workbench.js evalharness/static/workbench.css
git commit -m "feat: add performance demo workbench tab"
```

## Task 5: Real Run Wiring And Validation

**Files:**
- Modify: `evalharness/mem0_performance.py`
- Modify: `evalharness/server.py`
- Modify: `skill/SKILL.md`
- Modify: `tests/test_mem0_performance_demo.py`

- [ ] **Step 1: Add tests for real-run write path with fake client**

Append to `tests/test_mem0_performance_demo.py`:

```python
class Mem0PerformanceRealRunTest(unittest.TestCase):
    def test_real_run_writes_demo_context_to_client(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def add_text(self, text, context=""):
                self.calls.append({"text": text, "context": context})
                return {"ok": True}

        client = FakeClient()

        report = run_performance_demo(engine="mem0_hosted", mode="real_run", scale=1000, query_count=2, client=client)

        self.assertTrue(client.calls)
        self.assertEqual("mem0_large_memory_performance_demo", client.calls[0]["context"])
        self.assertEqual("real_run", report["mode"])
        self.assertEqual(1000, report["scale"])
```

- [ ] **Step 2: Run tests and fix any real-run gaps**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_mem0_performance_demo.py'
```

Expected: all tests pass. If it fails because real-run still uses dry-run reset counts, update `run_performance_demo()` so real-run `reset` is `{"found_count": 0, "deleted_count": 0, "errors": [], "note": "Use Reset Demo Memory to clean real backend"}`.

- [ ] **Step 3: Update `skill/SKILL.md` Workbench Features**

Add this bullet under Workbench tabs:

```markdown
- `Performance Demo`: Mem0 large-memory end-to-end demo with dry-run defaults, isolated demo user ID, latency metrics, Top-K evidence, and scoped reset.
```

Add this rule near reset/backend rules:

```markdown
Performance Demo must use `workbench-demo-large-memory` as an isolated user ID for all real Mem0 writes, searches, and deletes. Dry Run must not call remote Mem0.
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_mem0_performance_demo.py'
python3 -m unittest discover -s tests -p 'test_skill_markdown_contract.py'
```

Expected: both commands pass.

- [ ] **Step 5: Commit Task 5**

```bash
git add evalharness/mem0_performance.py evalharness/server.py skill/SKILL.md tests/test_mem0_performance_demo.py
git commit -m "feat: wire mem0 performance real run"
```

## Task 6: Full Verification And Local Workbench Restart

**Files:**
- No new source files unless verification exposes issues.

- [ ] **Step 1: Run full Python test suite**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: all tests pass.

- [ ] **Step 2: Run Python compile checks**

Run:

```bash
python3 -m py_compile assist_everything_betterandbetter_skill/skill.py assist_everything_betterandbetter_skill/mem0_backend.py evalharness/server.py evalharness/tools.py evalharness/agent.py evalharness/mem0_performance.py
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run JS syntax check**

Run:

```bash
node --check evalharness/static/workbench.js
```

Expected: no output and exit code 0.

- [ ] **Step 4: Restart Workbench on port 8787**

Run:

```bash
PID="$(lsof -tiTCP:8787 -sTCP:LISTEN || true)"
if [ -n "$PID" ]; then kill "$PID"; fi
python3 -c 'import subprocess; subprocess.Popen(["python3","-m","evalharness.cli","serve","--port","8787"], stdout=open("/tmp/assist-eval-workbench-8787.log","ab"), stderr=subprocess.STDOUT, start_new_session=True)'
```

Expected: `lsof -tiTCP:8787 -sTCP:LISTEN` returns one PID.

- [ ] **Step 5: Smoke test API**

Run:

```bash
curl -sS -X POST http://127.0.0.1:8787/api/mem0-performance-demo/run \
  -H 'content-type: application/json' \
  -d '{"engine":"mem0_hosted","mode":"dry_run","scale":1000,"query_count":3}' \
  | python3 -m json.tool | head -80
```

Expected JSON includes:

```json
{
  "ok": true,
  "mode": "dry_run",
  "scale": 1000,
  "demo_user_id": "workbench-demo-large-memory"
}
```

- [ ] **Step 6: Final commit if verification required fixes**

If Step 1-5 required fixes, commit them:

```bash
git add <fixed-files>
git commit -m "fix: verify mem0 performance demo"
```

If no fixes were needed, do not create an empty commit.

## Self-Review

- Spec coverage:
  - Independent Workbench tab: Task 4.
  - Dry Run default and report: Task 1, Task 3, Task 4.
  - Real Run explicit path: Task 3, Task 5.
  - Demo user isolation: Task 2, Task 3.
  - Reset scoped to demo user: Task 2, Task 3, Task 4.
  - Metrics and Top-K evidence: Task 1, Task 4.
  - Unified score+time ranking field in report: Task 1.
  - Tests and verification: Task 1-6.
- Placeholder scan:
  - No placeholder markers or vague implementation instructions are present.
- Type consistency:
  - Routes use `/api/mem0-performance-demo/*`.
  - The demo user constant is `DEMO_USER_ID`.
  - Report fields match the approved design: `metrics`, `phases`, `examples`, `reset`.
