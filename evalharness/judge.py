from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from assist_everything_betterandbetter_skill.cases import DIMENSIONS


class HeuristicJudge:
    """Offline judge used when no external LLM judge is configured."""

    name = "heuristic-trace-judge"

    def score(self, case_run: dict[str, Any]) -> dict[str, Any]:
        checks = case_run["checks"]
        scores = {
            "reproducibility": 10 if checks["reset"] and checks["snapshot_count"] >= 5 else 7,
            "memory_extraction": 20 if checks["created"] >= 2 and checks["show_memory"] else 14,
            "memory_application": 25 if checks["round2_applied"] and checks["round3_applied"] else 16,
            "update_and_decay": 20 if checks["updated"] and checks["deleted_filtered"] else 13,
            "transparency": 10 if checks["show_memory"] and checks["delete_reported"] else 7,
            "result_quality": 15 if checks["deliverable_turns"] >= 4 else 11,
        }
        reasons = {
            "reproducibility": "trace 包含 reset、固定 case 脚本和多次 memory snapshot。",
            "memory_extraction": "反馈被结构化成 active memory，并可展示。",
            "memory_application": "Round 2/3 的回答记录了 applied_memories。",
            "update_and_decay": "偏好变化产生降权/新规则，删除复测过滤 deleted memory。",
            "transparency": "用户可 show/delete，并在 trace 中看到工具调用。",
            "result_quality": "每轮均返回可交付任务结果，而不是只展示机制。",
        }
        return {
            "judge": self.name,
            "mode": "offline",
            "dimensions": DIMENSIONS,
            "scores": {**scores, "total": sum(scores.values())},
            "reasons": reasons,
        }


class ExternalCommandJudge:
    """LLM judge adapter. Command reads case-run JSON stdin and returns score JSON."""

    name = "external-command-llm-judge"

    def __init__(self, command: str | None = None) -> None:
        self.command = command or os.getenv("EVALHARNESS_JUDGE_CMD", "")
        if not self.command:
            raise ValueError("EVALHARNESS_JUDGE_CMD is not configured")

    def score(self, case_run: dict[str, Any]) -> dict[str, Any]:
        completed = subprocess.run(
            self.command,
            input=json.dumps(case_run, ensure_ascii=False),
            text=True,
            shell=True,
            check=True,
            capture_output=True,
        )
        data = json.loads(completed.stdout)
        data.setdefault("judge", self.name)
        data.setdefault("mode", "external_llm")
        return data


def build_judge(mode: str = "auto") -> HeuristicJudge | ExternalCommandJudge:
    if mode == "external" or (mode == "auto" and os.getenv("EVALHARNESS_JUDGE_CMD")):
        return ExternalCommandJudge()
    return HeuristicJudge()
