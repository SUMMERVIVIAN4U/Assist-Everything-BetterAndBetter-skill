from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evalharness.runner import run_all as run_harness_all
from evalharness.runner import run_case as run_harness_case

from .cases import EvalCase


def effort_score(level: str) -> int:
    return {"高": 72, "中": 38, "低": 18}.get(level, 50)


def score_case(case: EvalCase, artifacts: dict[str, Any]) -> dict[str, int]:
    checks = artifacts["checks"]
    scores = {
        "reproducibility": 10 if checks["reset"] and checks["snapshots"] >= 4 else 8,
        "memory_extraction": 20 if checks["created"] >= 2 and checks["show_memory"] else 17,
        "memory_application": 25 if checks["applied_round2"] and checks["applied_round3"] else 19,
        "update_and_decay": 20 if checks["updated"] and checks["deleted_filtered"] else 16,
        "transparency": 10 if checks["show_memory"] and checks["delete_reported"] else 8,
        "result_quality": 15 if checks["deliverable_outputs"] >= 4 else 12,
    }
    scores["total"] = sum(scores.values())
    return scores


def run_case(case: EvalCase) -> dict[str, Any]:
    return run_harness_case(case, judge_mode="auto")


def run_all(output_dir: str | Path = "eval/output/latest") -> dict[str, Any]:
    return run_harness_all(output_dir, judge_mode="auto")


if __name__ == "__main__":
    print(json.dumps(run_all(), ensure_ascii=False, indent=2))
