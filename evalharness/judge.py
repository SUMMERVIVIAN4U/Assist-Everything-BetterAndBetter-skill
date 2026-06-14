from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from assist_everything_betterandbetter_skill.cases import DIMENSIONS

from .llm import (
    LLM_PROVIDER_LABELS,
    OpenAICompatibleClient,
    any_llm_configured,
    default_configured_provider,
    llm_client_from_env,
    llm_configured,
    normalize_llm_provider,
)
from .quality import _applied_memory_details


SESSION_EFFORT_MEMORY_POLICY = {
    "eval_unit": "一次 Agent Chat Eval 对应当前 session 的全部对话；多轮比赛展示时，用多个 session 的 eval 结果做横向比较。",
    "effort_score": {
        "meaning": "user_effort.final_score 是本 session 的用户实际费力度遥测，不是 LLM judge 的唯一真值。",
        "count_as_agent_induced": [
            "用户因为 assistant 忽略已知约束而纠错或重复说明。",
            "用户因为 assistant 推荐了已排除/已删除/已送过的选项而纠错。",
            "用户因为 assistant 没有交付当前任务、只追问或跑题而继续催促。",
            "用户因为 assistant 错误理解选择意图而重复确认。",
        ],
        "do_not_count_as_agent_induced": [
            "用户自然缩小范围、换品类、选择候选、补充新偏好。",
            "用户主动探索多个方案，且 assistant 上一轮没有违反已有约束。",
            "用户输入较长但主要是在表达新需求、新约束或最终选择。",
        ],
    },
    "memory_saving_points": {
        "meaning": "记忆节省信息点表示本 session 中用户本可以重复说明、但因系统正确复用已有记忆而省掉的信息点。",
        "certify_only_if_all_true": [
            "该信息点在本 session 开始前已经存在，或至少不是本 session 刚由用户重述后才创建。",
            "用户在本 session 没有再次显式说出同一信息点。",
            "该信息点对当前任务达成是关键约束，而不是可有可无的背景。",
            "assistant 的回答可见地使用了该信息点。",
            "该信息点处于 active 状态，没有被删除、压制或被当前用户指令反转。",
            "同一语义信息点每个 session 只计一次。",
        ],
        "candidate_not_credit": "applied_memory_details 只是候选证据；不能因为被召回就自动算作节省。",
    },
}


EFFORT_MEMORY_RUBRIC = """
费力度与记忆节省评分规则：
1. Eval 单位是当前 Agent Chat session 的全部对话。多个 session 的比较，才体现第一轮、第二轮、第三轮是否越来越省力。
2. user_effort.final_score 是遥测输入，不是最终裁判。你必须根据 turns 判断这些成本来自用户主动探索，还是 agent 失误造成的额外成本。
3. 不要把用户自然缩小范围、换品类、选择候选、补充新偏好，算成 agent-induced correction；只有 assistant 忽略已知约束、推荐已排除项、跑题、未交付、或误解选择意图时，才把后续纠错计为 agent 造成的费力度。
4. 记忆节省信息点必须经过认证：它需要在本 session 前已经存在，用户本 session 没有重复说，且它对当前任务关键，并被 assistant 明确使用。applied_memory_details 只是候选召回，不是自动得分。
5. 同一语义信息点每个 session 只计一次；本 session 刚写入的信息、无关召回、被删除/反转的记忆，都不能算记忆节省。
6. 如果 final_score 偏高主要来自用户主动选择和细化，不应重扣 result_quality；如果偏高来自 assistant 违反记忆或让用户重复劳动，必须在 result_quality 或 memory_application 中扣分。
请在 JSON 中额外返回 effort_review，用于解释费力度和记忆节省判断：
{
  "session_effort_judgement": "数值化解释本 session 费力度来自哪些用户动作",
  "agent_induced_corrections": [{"turn": "turn_002", "reason": "..."}],
  "user_driven_refinements": [{"turn": "turn_002", "reason": "..."}],
  "certified_memory_savings": [{"memory": "...", "turn": "turn_001", "reason": "..."}],
  "rejected_memory_savings": [{"memory": "...", "reason": "..."}]
}
""".strip()


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
        memory_saving_points = effort.get("memory_saving_points", effort.get("saved_score", checks.get("effort_reduction", 0)))
        low_effort = (
            effort.get("final_score", checks.get("effort_final", 100)) <= (80 if is_chat_session else 45)
            or memory_saving_points >= (6 if is_chat_session else 3)
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


class LLMJudge:
    """LLM judge backed by the selected OpenAI-compatible provider."""

    name = "llm-judge"

    def __init__(self, provider: str, client: OpenAICompatibleClient | None = None) -> None:
        self.provider = normalize_llm_provider(provider)
        self.client = client or _judge_llm_client(self.provider)

    def score(self, case_run: dict[str, Any]) -> dict[str, Any]:
        compact = {
            "id": case_run["id"],
            "title": case_run["title"],
            "module": case_run["module"],
            "script": case_run["script"],
            "rounds": case_run["rounds"],
            "checks": case_run["checks"],
            "user_effort": case_run.get("user_effort", {}),
            "session_eval_policy": SESSION_EFFORT_MEMORY_POLICY,
            "quality": case_run.get("quality", {}),
            "memory_events": case_run["memory_events"],
            "turns": [
                {
                    "stage": turn["stage"],
                    "user": turn["user"]["content"],
                    "assistant": turn["assistant"]["content"],
                    "tools": [call["name"] for call in turn["tool_calls"]],
                    "applied_memories": turn["applied_memories"],
                    "applied_memory_details": _applied_memory_details(turn),
                }
                for turn in case_run["turns"]
            ],
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 WAISC 5 月自我进化 Skill 赛事评委。"
                    "请按六个维度严格评分，返回 JSON。"
                    "分值上限：reproducibility 10, memory_extraction 20, "
                    "memory_application 25, update_and_decay 20, transparency 10, result_quality 15。"
                    "评分必须和 checks、quality、memory_events、turns 中的证据一致；不要因为主观偏好或未要求的能力扣分。"
                    f"\n\n{EFFORT_MEMORY_RUBRIC}\n\n"
                    "如果 reset/snapshot/show_memory/round2_applied/round3_applied/updated/deleted_filtered/delete_reported/compound_followup_delivered 均为 true，"
                    "且 polluted_memories=0、semantic_violations=0、unresolved_dissatisfaction=false、delivered_task_turns=task_turns，"
                    "总分通常不应低于 90；除非 turns 中存在明确证据说明记忆误用、任务未交付、删除后仍被应用、或用户纠错。"
                    "每个维度扣分必须指出具体 turn 或 memory_id；不能只说“缺少更细机制”“可以更好”就大幅扣分。"
                    "必须包含 scores.total、reasons 和 effort_review。scores 必须包含六个维度的整数分。"
                ),
            },
            {"role": "user", "content": json.dumps(compact, ensure_ascii=False)},
        ]
        data: dict[str, Any] = {}
        attempts = max(1, int(os.getenv("EVALHARNESS_JUDGE_RETRIES", "3")))
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                data = self.client.json_chat(messages, temperature=0.0)
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    raise
                continue
            scores_payload = data.get("scores", {})
            if isinstance(scores_payload, dict) and any(key in scores_payload for key in DIMENSIONS):
                break
            if attempt + 1 < attempts:
                messages.append(
                    {
                        "role": "user",
                        "content": "上一次返回缺少 scores 六个维度。请只返回合法 JSON，必须包含 scores 和 reasons。",
                    }
                )
        if not data and last_error:
            raise last_error
        scores = data.get("scores", {})
        if not isinstance(scores, dict) or not any(key in scores for key in DIMENSIONS):
            raise ValueError(f"LLM judge returned invalid scores: {data}")
        for key, max_score in DIMENSIONS.items():
            scores[key] = max(0, min(int(scores.get(key, 0)), max_score))
        scores["total"] = sum(scores[key] for key in DIMENSIONS)
        return {
            "judge": f"{self.provider}-llm-judge",
            "mode": f"{self.provider}_llm",
            "dimensions": DIMENSIONS,
            "scores": scores,
            "reasons": data.get("reasons", {}),
            "effort_review": data.get("effort_review", {}),
        }


class MimoJudge(LLMJudge):
    """Backward-compatible Mimo judge wrapper."""

    def __init__(self, client: OpenAICompatibleClient | None = None) -> None:
        super().__init__("mimo", client=client)


def build_judge(mode: str = "auto") -> HeuristicJudge | ExternalCommandJudge | LLMJudge:
    normalized = normalize_llm_provider(mode)
    if mode not in {"auto", "heuristic", "external"} and normalized in LLM_PROVIDER_LABELS and llm_configured(normalized):
        return LLMJudge(normalized)
    if mode == "auto" and any_llm_configured():
        return LLMJudge(default_configured_provider())
    if mode == "external" or (mode == "auto" and os.getenv("EVALHARNESS_JUDGE_CMD")):
        return ExternalCommandJudge()
    return HeuristicJudge()


def score_with_fallback(case_run: dict[str, Any], mode: str = "heuristic", *, allow_fallback: bool = True) -> dict[str, Any]:
    try:
        return build_judge(mode).score(case_run)
    except Exception as exc:
        if not allow_fallback:
            raise
        judgement = HeuristicJudge().score(case_run)
        judgement["fallback_from"] = mode
        judgement["fallback_error"] = str(exc)
        judgement["mode"] = f"{judgement['mode']}_fallback"
        return judgement


def _judge_llm_client(provider: str) -> OpenAICompatibleClient:
    timeout = float(os.getenv("EVALHARNESS_JUDGE_TIMEOUT", os.getenv("EVALHARNESS_MIMO_TIMEOUT", "120")))
    return llm_client_from_env(provider, timeout=timeout)
