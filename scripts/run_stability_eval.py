from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from assist_everything_betterandbetter_skill.cases import CASES, EvalCase
from assist_everything_betterandbetter_skill.mem0_backend import Mem0Config
from evalharness.agent import HarnessAgent
from evalharness.env import load_env
from evalharness.evaluation import build_report, evaluate_case_run, save_report
from evalharness.llm import llm_configured
from evalharness.runner import _gift_round_cards, _round_cards


def main() -> None:
    parser = argparse.ArgumentParser(description="Run real-LLM stability evals across memory backends.")
    parser.add_argument("--provider", default="deepseek_pro")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--backends", default="local,mem0_hosted")
    parser.add_argument("--cases", default="all", help="Comma list like C01,C02,GIFT or all")
    parser.add_argument("--output", default="eval/output/latest/stability_real_llm")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()

    load_env(args.env_file, override=True)
    if not llm_configured(args.provider):
        raise SystemExit(f"{args.provider} is not configured")

    selected_backends = [item.strip() for item in args.backends.split(",") if item.strip()]
    selected_cases = _select_cases(args.cases)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    evaluated: list[dict[str, Any]] = []
    raw_runs: list[dict[str, Any]] = []
    for backend in selected_backends:
        for case_id, case in selected_cases:
            for index in range(1, args.rounds + 1):
                print(f"[stability] backend={backend} case={case_id} round={index}/{args.rounds}", flush=True)
                run = _run_one(case_id, case, backend=backend, provider=args.provider, round_index=index)
                raw_runs.append(run)
                evaluated_case = evaluate_case_run(run, args.provider, allow_judge_fallback=False)
                evaluated.append(evaluated_case)
                _write_partial(output, evaluated, raw_runs, args.provider)
                print(f"[stability] score={evaluated_case['score']} id={evaluated_case['id']}", flush=True)

    report = build_report(evaluated, judge_mode=args.provider, agent_mode=args.provider, source="stability_real_llm")
    report["stability"] = _stability_summary(evaluated, rounds=args.rounds)
    save_report(output, report, save_history=True)
    (output / "stability_summary.json").write_text(json.dumps(report["stability"], ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["stability"], ensure_ascii=False, indent=2), flush=True)


def _select_cases(spec: str) -> list[tuple[str, EvalCase | None]]:
    if spec == "all":
        return [(case.id, case) for case in CASES] + [("GIFT", None)]
    wanted = {item.strip().upper() for item in spec.split(",") if item.strip()}
    output: list[tuple[str, EvalCase | None]] = []
    for case in CASES:
        if case.id.upper() in wanted:
            output.append((case.id, case))
    if "GIFT" in wanted or "GIFT-01" in wanted:
        output.append(("GIFT", None))
    if not output:
        raise SystemExit(f"no cases selected: {spec}")
    return output


def _run_one(case_id: str, case: EvalCase | None, *, backend: str, provider: str, round_index: int) -> dict[str, Any]:
    if backend == "local":
        with tempfile.TemporaryDirectory(prefix=f"stability-{case_id.lower()}-local-") as tmp:
            agent = HarnessAgent(llm_mode=provider, require_llm=True, memory_dir=tmp, persist_memory=True, memory_backend="local")
            return _run_script(agent, case_id, case, backend=backend, provider=provider, round_index=round_index)
    if backend == "mem0_hosted":
        config = _mem0_eval_config(case_id, round_index)
        with tempfile.TemporaryDirectory(prefix=f"stability-{case_id.lower()}-hosted-") as tmp:
            agent = HarnessAgent(
                llm_mode=provider,
                require_llm=True,
                memory_dir=tmp,
                persist_memory=True,
                memory_backend="mem0_hosted",
                mem0_config=config,
            )
            try:
                return _run_script(agent, case_id, case, backend=backend, provider=provider, round_index=round_index, mem0_user_id=config.user_id)
            finally:
                try:
                    agent.toolbox.skill._reset_remote_memory(agent.toolbox.skill.mem0_client, "mem0_hosted")
                except Exception as exc:
                    print(f"[stability] cleanup failed user={config.user_id}: {exc}", flush=True)
    raise ValueError(f"unsupported backend: {backend}")


def _mem0_eval_config(case_id: str, round_index: int) -> Mem0Config:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    user_id = f"stability-{case_id.lower()}-{round_index}-{stamp}"
    config = Mem0Config(
        enabled=True,
        base_url=os.getenv("MEM0_BASE_URL", "").strip(),
        api_key=os.getenv("MEM0_API_KEY", "").strip(),
        user_id=user_id,
        app_id=os.getenv("MEM0_APP_ID", "assist-everything-betterandbetter-skill").strip(),
        project_id=os.getenv("MEM0_PROJECT_ID", "").strip(),
        project_name=os.getenv("MEM0_PROJECT_NAME", "").strip(),
        timeout=float(os.getenv("MEM0_TIMEOUT", "15") or 15),
    )
    if not config.ready:
        raise RuntimeError("Mem0 Hosted config is not ready")
    return config


def _run_script(
    agent: HarnessAgent,
    case_id: str,
    case: EvalCase | None,
    *,
    backend: str,
    provider: str,
    round_index: int,
    mem0_user_id: str = "",
) -> dict[str, Any]:
    if case is None:
        script = _gift_script()
        title = "女朋友生日礼物"
        domain = "gift_planning"
        module = "复杂送礼协作记忆"
    else:
        script = [
            ("reset", "reset memory"),
            ("round1_task", case.initial_task),
            ("feedback", case.feedback),
            ("show_memory", case.memory_query),
            ("round2_task", case.second_task),
            ("preference_change", case.preference_change),
            ("round3_task", case.third_task),
            ("delete_retest", f"{case.delete_query}。然后：{case.delete_retest_task}"),
        ]
        title = case.title
        domain = case.domain
        module = case.module

    turns = [agent.reply(message, stage=stage).to_dict() for stage, message in script]
    snapshots = [turn["memory_snapshot"] for turn in turns]
    events = _events_from_turns(turns)
    run_id = f"{case_id}-{backend}-r{round_index}"
    delete_actions = [event for event in events if event.get("action") == "delete" and event.get("ok", True) is not False]
    run = {
        "id": run_id,
        "title": f"{title} / {backend} / round {round_index}",
        "domain": domain,
        "module": module,
        "script": {
            "source": "stability_real_llm",
            "backend": backend,
            "provider": provider,
            "round_index": round_index,
            "mem0_user_id": mem0_user_id,
        },
        "turns": turns,
        "rounds": _gift_round_cards(turns) if case is None else _round_cards(turns),
        "snapshots": snapshots,
        "memory_events": events,
        "checks": {
            "reset": any(event.get("action") == "reset" for event in events),
            "snapshot_count": len(snapshots),
            "created": sum(1 for event in events if event.get("action") == "add" and event.get("ok", True) is not False),
            "show_memory": any(turn["stage"] == "show_memory" and ("记忆" in turn["assistant"]["content"] or "active" in turn["assistant"]["content"]) for turn in turns),
            "round2_applied": _applied_at(turns, "round2_task"),
            "round3_applied": _applied_at(turns, "round3_task"),
            "updated": any(event.get("action") in {"downgrade", "archive", "update", "add", "delete"} for event in events[1:]),
            "deleted_filtered": bool(delete_actions) or bool(snapshots[-1].get("deleted")),
            "delete_reported": "删除" in turns[-1]["assistant"]["content"],
            "deliverable_turns": sum(1 for turn in turns if len(turn["assistant"]["content"]) > 20),
        },
        "ablation": {
            "module": module,
            "off": ["后续轮次需要重复说明", "旧偏好可能污染新任务", "删除后仍可能被召回"],
            "on": ["结构化保存关键记忆", "后续轮次直接召回应用", "删除和更新能改变后续结果"],
        },
    }
    return run


def _gift_script() -> list[tuple[str, str]]:
    return [
        ("reset", "reset memory"),
        ("round1_task", "帮我给女朋友选个生日礼物。"),
        ("feedback", "预算1000元左右；她喜欢紫色；如果是首饰，她喜欢玫瑰金；以前送过玫瑰金项链，送过的不要再送。"),
        ("approve_memory", "同意保存。"),
        ("show_memory", "展示当前记忆。"),
        ("round2_task", "给我一个礼物推荐。"),
        ("preference_change", "不是，我想换个非首饰品类。"),
        ("round3_task", "那再给一个推荐。"),
        ("delete_retest", "删除 她喜欢紫色。然后：再给一个不重复的礼物方向。"),
    ]


def _events_from_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for turn in turns:
        for call in turn.get("tool_calls", []):
            events.extend(call.get("output", {}).get("memory_actions", []))
    return events


def _applied_at(turns: list[dict[str, Any]], stage: str) -> bool:
    return any(turn.get("stage") == stage and bool(turn.get("applied_memories")) for turn in turns)


def _write_partial(output: Path, evaluated: list[dict[str, Any]], raw_runs: list[dict[str, Any]], provider: str) -> None:
    partial = build_report(evaluated, judge_mode=provider, agent_mode=provider, source="stability_real_llm_partial")
    partial["raw_case_count"] = len(raw_runs)
    partial["stability"] = _stability_summary(evaluated, rounds=0)
    (output / "partial_eval_report.json").write_text(json.dumps(partial, ensure_ascii=False, indent=2), encoding="utf-8")


def _stability_summary(cases: list[dict[str, Any]], *, rounds: int) -> dict[str, Any]:
    grouped: dict[str, list[int]] = {}
    failures: list[dict[str, Any]] = []
    for case in cases:
        key = "-".join(str(case["id"]).split("-")[:-1]) or case["id"]
        grouped.setdefault(key, []).append(int(case["score"]))
        if case["score"] < 90:
            failures.append(
                {
                    "id": case["id"],
                    "score": case["score"],
                    "reasons": case.get("judge", {}).get("reasons", {}),
                    "checks": case.get("checks", {}),
                }
            )
    return {
        "target_rounds": rounds,
        "groups": {
            key: {
                "runs": len(scores),
                "scores": scores,
                "min": min(scores),
                "avg": round(sum(scores) / len(scores), 1),
                "stable_90": len(scores) >= rounds and min(scores) >= 90 if rounds else min(scores) >= 90,
            }
            for key, scores in sorted(grouped.items())
        },
        "all_stable_90": all(min(scores) >= 90 and (len(scores) >= rounds if rounds else True) for scores in grouped.values()) if grouped else False,
        "failures": failures,
    }


if __name__ == "__main__":
    main()
