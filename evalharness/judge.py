from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from assist_everything_betterandbetter_skill.cases import DIMENSIONS

from .llm import OpenAICompatibleClient, build_llm_client, llm_configured, provider_env_key


class HeuristicJudge:
    """Offline judge used when no external LLM judge is configured."""

    name = "heuristic-trace-judge"

    def score(self, case_run: dict[str, Any]) -> dict[str, Any]:
        checks = case_run["checks"]
        is_chat_session = case_run.get("script", {}).get("source") == "agent_chat"
        task_turns = max(1, int(checks.get("task_turns", 0) or 1))
        delivered = int(checks.get("delivered_task_turns", 0) or 0)
        delivery_ratio = delivered / task_turns
        no_pollution = checks.get("polluted_memories", 0) == 0
        no_semantic_violations = checks.get("semantic_violations", 0) == 0
        effort = case_run.get("user_effort", {})
        low_effort = (
            effort.get("final_score", checks.get("effort_final", 100)) <= (80 if is_chat_session else 45)
            or effort.get("saved_score", checks.get("effort_reduction", 0)) >= 25
        )
        compound_ok = checks.get("compound_followup_delivered", False)
        no_unresolved_dissatisfaction = not checks.get("unresolved_dissatisfaction", False)
        reproducibility_ok = (
            checks["reset"] and checks["snapshot_count"] >= 1 and case_run.get("user_effort")
            if is_chat_session
            else checks["reset"] and checks["snapshot_count"] >= 5 and case_run.get("user_effort")
        )
        update_ok = (
            checks["updated"] and no_semantic_violations
            if is_chat_session
            else checks["updated"] and checks["deleted_filtered"] and compound_ok and no_semantic_violations
        )
        partial_update_ok = (
            checks["updated"]
            if is_chat_session
            else checks["updated"] and checks["deleted_filtered"]
        )
        transparency_ok = (
            checks["show_memory"] and no_pollution
            if is_chat_session
            else checks["show_memory"] and checks["delete_reported"] and no_pollution
        )
        result_quality_ok = (
            delivery_ratio >= 0.85 and no_unresolved_dissatisfaction and low_effort and no_semantic_violations
            if is_chat_session
            else delivery_ratio >= 0.85 and compound_ok and no_unresolved_dissatisfaction and low_effort
        )
        partial_result_quality_ok = (
            delivery_ratio >= 0.6 and no_unresolved_dissatisfaction
            if is_chat_session
            else delivery_ratio >= 0.6 and compound_ok
        )
        scores = {
            "reproducibility": 10 if reproducibility_ok else 7,
            "memory_extraction": 20
            if checks["created"] >= 2 and checks["show_memory"] and no_pollution
            else (16 if checks["created"] >= 1 and no_pollution else 10),
            "memory_application": 25
            if checks["round2_applied"] and checks["round3_applied"] and no_semantic_violations
            else (18 if checks["round2_applied"] or checks["round3_applied"] else 10),
            "update_and_decay": 20
            if update_ok
            else (14 if partial_update_ok else 9),
            "transparency": 10
            if transparency_ok
            else (7 if checks["show_memory"] else 4),
            "result_quality": 15
            if result_quality_ok
            else (10 if partial_result_quality_ok else 5),
        }
        if not no_semantic_violations:
            scores["memory_application"] = min(scores["memory_application"], 15)
            scores["result_quality"] = min(scores["result_quality"], 8)
        if not no_pollution:
            scores["memory_extraction"] = min(scores["memory_extraction"], 12)
            scores["transparency"] = min(scores["transparency"], 6)
        reasons = {
            "reproducibility": "trace 必须包含 reset、脚本回放、snapshot 和用户费力度轨迹。",
            "memory_extraction": "明确反馈应结构化保存；问题句、情绪反问、敷衍确认不能污染长期记忆。",
            "memory_application": "不仅要检索记忆，还要正确组合偏好、历史、约束和当前决策。",
            "update_and_decay": "更新/删除必须影响后续输出；复合请求中的删除后任务也要完成。",
            "transparency": "用户可 show/delete，回复中的保存承诺必须和真实 memory_actions 对齐。",
            "result_quality": "任务轮必须给可直接使用的结果；用户费力度应下降，不能只复述机制。",
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


class OpenAICompatibleJudge:
    """LLM judge backed by a configured OpenAI-compatible chat endpoint."""

    def __init__(self, mode: str, client: OpenAICompatibleClient | None = None) -> None:
        self.mode = mode
        self.client = client or _judge_llm_client(mode)
        provider = self.client.config.provider.lower()
        model_slug = self.client.config.model.lower().replace("/", "-")
        self.name = f"{provider}-{model_slug}-llm-judge"

    def score(self, case_run: dict[str, Any]) -> dict[str, Any]:
        compact = {
            "id": case_run["id"],
            "title": case_run["title"],
            "module": case_run["module"],
            "script": case_run["script"],
            "rounds": case_run["rounds"],
            "checks": case_run["checks"],
            "user_effort": case_run.get("user_effort", {}),
            "quality": case_run.get("quality", {}),
            "memory_events": case_run["memory_events"],
            "turns": [
                {
                    "stage": turn["stage"],
                    "user": turn["user"]["content"],
                    "assistant": turn["assistant"]["content"],
                    "tools": [call["name"] for call in turn["tool_calls"]],
                    "applied_memories": turn["applied_memories"],
                }
                for turn in case_run["turns"]
            ],
        }
        data = self.client.json_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是 WAISC 5 月自我进化 Skill 赛事评委。"
                        "请按六个维度严格评分，返回 JSON。"
                        "分值上限：reproducibility 10, memory_extraction 20, "
                        "memory_application 25, update_and_decay 20, transparency 10, result_quality 15。"
                        "必须包含 scores.total 和 reasons。"
                    ),
                },
                {"role": "user", "content": json.dumps(compact, ensure_ascii=False)},
            ],
            temperature=0.0,
        )
        scores = data.get("scores", {})
        for key, max_score in DIMENSIONS.items():
            scores[key] = max(0, min(int(scores.get(key, 0)), max_score))
        scores["total"] = sum(scores[key] for key in DIMENSIONS)
        return {
            "judge": self.name,
            "mode": f"{self.client.config.provider.lower()}_llm",
            "dimensions": DIMENSIONS,
            "scores": scores,
            "reasons": data.get("reasons", {}),
        }


class MimoJudge(OpenAICompatibleJudge):
    """Compatibility wrapper for the configured Mimo chat endpoint."""

    def __init__(self, client: OpenAICompatibleClient | None = None) -> None:
        super().__init__("mimo", client)


class DeepSeekJudge(OpenAICompatibleJudge):
    """Compatibility wrapper for configured DeepSeek chat endpoints."""

    def __init__(self, mode: str = "deepseek-flash", client: OpenAICompatibleClient | None = None) -> None:
        super().__init__(mode, client)


def build_judge(mode: str = "auto") -> HeuristicJudge | ExternalCommandJudge | OpenAICompatibleJudge:
    if mode == "mimo":
        return MimoJudge()
    if mode in {"deepseek", "deepseek-flash", "deepseek-pro"}:
        return DeepSeekJudge(mode)
    if mode == "auto" and llm_configured():
        return OpenAICompatibleJudge("auto")
    if mode == "external" or (mode == "auto" and os.getenv("EVALHARNESS_JUDGE_CMD")):
        return ExternalCommandJudge()
    return HeuristicJudge()


def score_with_fallback(case_run: dict[str, Any], mode: str = "heuristic") -> dict[str, Any]:
    try:
        return build_judge(mode).score(case_run)
    except Exception as exc:
        judgement = HeuristicJudge().score(case_run)
        judgement["fallback_from"] = mode
        judgement["fallback_error"] = str(exc)
        judgement["mode"] = f"{judgement['mode']}_fallback"
        return judgement


def _judge_llm_client(mode: str) -> OpenAICompatibleClient:
    provider = provider_env_key(mode)
    timeout = float(
        os.getenv(
            f"EVALHARNESS_JUDGE_{provider}_TIMEOUT",
            os.getenv("EVALHARNESS_JUDGE_TIMEOUT", os.getenv(f"EVALHARNESS_{provider}_TIMEOUT", "120")),
        )
    )
    return build_llm_client(mode, timeout=timeout)
