from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Any

from assist_everything_betterandbetter_skill.cases import DIMENSIONS

from .llm import normalize_llm_provider
from .judge import score_with_fallback
from .quality import augment_case_run


HISTORY_DIR = Path("eval/output/history")


def evaluate_case_run(case_run: dict[str, Any], judge_mode: str, *, allow_judge_fallback: bool = True) -> dict[str, Any]:
    """Single eval entrypoint for preset cases and Agent Chat sessions."""
    augment_case_run(case_run)
    judgement = score_with_fallback(case_run, judge_mode, allow_fallback=allow_judge_fallback)
    case_run["judge"] = judgement
    case_run["scores"] = judgement["scores"]
    case_run["score"] = judgement["scores"]["total"]
    return case_run


def build_report(
    cases: list[dict[str, Any]],
    *,
    judge_mode: str,
    agent_mode: str,
    source: str,
) -> dict[str, Any]:
    avg = round(sum(case["score"] for case in cases) / len(cases), 1) if cases else 0
    effort_avg = round(sum(case.get("user_effort", {}).get("final_score", 100) for case in cases) / len(cases), 1) if cases else 0
    saving_points_avg = round(sum(case.get("user_effort", {}).get("memory_saving_points", case.get("user_effort", {}).get("saved_score", 0)) for case in cases) / len(cases), 1) if cases else 0
    return {
        "run_id": _run_id(source),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "harness": {
            "name": "assist-everything-betterandbetter-evalharness",
            "agent_mode": "local_tool_agent" if agent_mode == "local" else f"{normalize_llm_provider(agent_mode)}_tool_agent",
            "judge_mode": cases[0]["judge"]["mode"] if cases else judge_mode,
            "supports_external_llm_judge": True,
            "supports_agent_chat": True,
            "eval_source": source,
        },
        "summary": {
            "case_count": len(cases),
            "config_average": avg,
            "all_cases_above_90": all(case["score"] >= 90 for case in cases),
            "effort_average": effort_avg,
            "memory_saving_points_average": saving_points_avg,
            "effort_reduction": saving_points_avg,
            "saved_effort_average": saving_points_avg,
        },
        "dimensions": DIMENSIONS,
        "cases": cases,
    }


def save_report(output_dir: str | Path, report: dict[str, Any], *, save_history: bool) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "eval_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Eval Harness Report",
        "",
        f"Run: {report.get('run_id', '-')}",
        f"Average: {report['summary']['config_average']}",
        f"Effort average: {report['summary']['effort_average']}",
        f"Memory saving points average: {report['summary']['memory_saving_points_average']}",
        "",
    ]
    for case in report["cases"]:
        effort = case.get("user_effort", {})
        lines.append(
            f"- {case['id']} {case['title']}: {case['score']}/100 "
            f"effort={effort.get('final_score', '-')} memory_saving_points={effort.get('memory_saving_points', effort.get('saved_score', '-'))}"
            f" ({case['judge']['judge']})"
        )
    (out / "eval_report.md").write_text("\n".join(lines), encoding="utf-8")
    if save_history:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        history_path = HISTORY_DIR / f"{report['run_id']}.json"
        history_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def load_history(latest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    if HISTORY_DIR.exists():
        for path in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
            try:
                reports.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
    if latest and not any(item.get("run_id") == latest.get("run_id") for item in reports):
        reports.insert(0, latest)
    return reports


def with_history(report: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(report)
    history = load_history(report)
    enriched["history"] = [
        {
            "run_id": item.get("run_id", ""),
            "created_at": item.get("created_at", ""),
            "source": item.get("harness", {}).get("eval_source", ""),
            "summary": item.get("summary", {}),
            "cases": item.get("cases", []),
        }
        for item in history
    ]
    return enriched


def _run_id(source: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{source}-{stamp}"
