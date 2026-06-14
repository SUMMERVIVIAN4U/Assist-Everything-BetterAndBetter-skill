from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evalharness.runner import run_all


def main() -> None:
    runs = []
    for idx in range(5):
        report = run_all(f"eval/output/verify_run_{idx + 1}", judge_mode="heuristic", agent_mode="local")
        runs.append(report)
        for case in report["cases"]:
            assert case["score"] >= 90, (idx + 1, case["id"], case["score"])
            assert case["checks"]["effort_reduction"] > 0, (idx + 1, case["id"], "effort_reduction")
            assert len(case["rounds"]) == 3, (idx + 1, case["id"], "round_count")
            assert case["checks"]["deleted_filtered"], (idx + 1, case["id"], "delete_filter")
            assert case["checks"]["round2_applied"], (idx + 1, case["id"], "round2_application")
            assert case["checks"]["round3_applied"], (idx + 1, case["id"], "round3_application")
    print("verify_eval passed")
    for idx, report in enumerate(runs, 1):
        scores = ", ".join(f"{case['id']}={case['score']}" for case in report["cases"])
        print(f"run {idx}: avg={report['summary']['config_average']} {scores}")


if __name__ == "__main__":
    main()
