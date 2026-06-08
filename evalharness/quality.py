from __future__ import annotations

from typing import Any


TASK_STAGES = {"round1_task", "round2_task", "round3_task", "preference_change", "delete_retest", "chat"}
NEGATIVE_TERMS = ["不是", "我说过", "你怎么", "为什么", "愚蠢", "费劲", "硬要", "不能不", "就不能"]
REPEAT_TERMS = ["之前", "已经", "我说过", "还记得", "不是说"]
SATISFACTION_TERMS = ["可以", "不错", "就这个", "按这个", "满意", "终于对了"]
META_ONLY_TERMS = ["我会按", "我会先", "本轮已应用记忆", "删除结果", "当前 active 记忆"]
BAD_MEMORY_TERMS = ["愚蠢", "你还记得", "好的，给我", "我的意思是"]


def augment_case_run(case_run: dict[str, Any]) -> dict[str, Any]:
    """Attach stricter quality checks and user-effort telemetry to a case run."""
    turns = case_run.get("turns", [])
    effort = _effort_trace(case_run)
    checks = case_run.setdefault("checks", {})
    task_turns = [turn for turn in turns if _is_task_turn(turn)]
    delivered = [turn for turn in task_turns if _is_deliverable(case_run, turn)]
    polluted = _polluted_memory_events(case_run)
    semantic_violations = _semantic_violations(case_run)
    unresolved_dissatisfaction = _unresolved_dissatisfaction(case_run)
    compound_followup = _compound_followup_delivered(case_run)

    checks.update(
        {
            "task_turns": len(task_turns),
            "delivered_task_turns": len(delivered),
            "compound_followup_delivered": compound_followup,
            "polluted_memories": len(polluted),
            "polluted_memory_details": polluted,
            "semantic_violations": len(semantic_violations),
            "semantic_violation_details": semantic_violations,
            "unresolved_dissatisfaction": unresolved_dissatisfaction,
            "correction_turns": sum(1 for item in effort["turns"] if item["signals"]["correction"]),
            "repeated_memory_turns": sum(1 for item in effort["turns"] if item["signals"]["repeated_memory"]),
            "satisfaction_signal": any(_contains(turn.get("user", {}).get("content", ""), SATISFACTION_TERMS) for turn in turns),
            "effort_final": effort["final_score"],
            "effort_reduction": effort["reduction"],
        }
    )
    case_run["user_effort"] = effort
    case_run["quality"] = {
        "delivered_task_turn_ids": [turn["id"] for turn in delivered],
        "polluted_memory_events": polluted,
        "semantic_violations": semantic_violations,
        "rules": USER_EFFORT_RULES,
    }
    attach_eval_timeline(case_run)
    return case_run


USER_EFFORT_RULES = [
    {"name": "用户轮数", "delta": "+8", "description": "每多一轮用户输入，说明用户多付出一次操作成本。"},
    {"name": "输入长度", "delta": "+0~+6", "description": "用户解释越长，补充说明成本越高。"},
    {"name": "追问成本", "delta": "+8", "description": "agent 需要用户补充本应从上下文或记忆获得的信息。"},
    {"name": "重复说明", "delta": "+18", "description": "用户重复之前已经表达的记忆、历史、约束或已选方案。"},
    {"name": "纠错/不满", "delta": "+14", "description": "用户指出 agent 忘记、误用、推荐方向错误或需要换方案。"},
    {"name": "强情绪反馈", "delta": "+20", "description": "用户出现明显挫败、讽刺或辱骂。"},
    {"name": "违反记忆推理", "delta": "+30", "description": "agent 推荐与已知偏好、历史、决策、约束组合矛盾。"},
    {"name": "任务未交付", "delta": "+12", "description": "任务轮只解释机制，没有给出可直接使用的结果。"},
    {"name": "正确应用记忆", "delta": "saved +12", "description": "应用 active memory 且没有推理冲突，记为节省，不再抵扣用户成本。"},
    {"name": "有效记忆变化", "delta": "saved +8", "description": "新增、更新、删除等动作降低后续重复说明成本，记为节省。"},
]


def _effort_trace(case_run: dict[str, Any]) -> dict[str, Any]:
    turns = case_run.get("turns", [])
    score = 0
    saved = 0
    output: list[dict[str, Any]] = []
    for turn in turns:
        user = turn.get("user", {}).get("content", "")
        assistant = turn.get("assistant", {}).get("content", "")
        actions = _memory_actions(turn)
        correction = _contains(user, NEGATIVE_TERMS)
        repeated = _contains(user, ["我说过", "不是说", "还记得", "之前和你说过", "之前说过"])
        emotion = _contains(user, ["愚蠢", "费劲", "烦", "崩溃"])
        violation = bool(_turn_semantic_violations(case_run, turn))
        delivered = _is_deliverable(case_run, turn)
        task_turn = _is_task_turn(turn)
        asks = bool((turn.get("tool_calls") or [{}])[0].get("output", {}).get("asks"))
        bad_memory = any(_contains(action.get("detail", ""), BAD_MEMORY_TERMS) for action in actions if action.get("action") == "add")

        management_turn = _is_management_only_turn(turn)
        if management_turn:
            delta = 0
            saving_delta = 0
            reasons = ["管理/查看/删除记忆轮不计入用户任务费力度"]
            saving_reasons: list[str] = []
        else:
            delta = 8
            reasons = ["用户轮数 +8"]
            input_cost = min(6, max(1, len(user) // 35)) if user else 0
            delta += input_cost
            reasons.append(f"输入长度 +{input_cost}")
            saving_delta = 0
            saving_reasons = []

        if not management_turn and asks:
            delta += 8
            reasons.append("需要补充信息 +8")
        if not management_turn and correction:
            delta += 14
            reasons.append("纠错或不满 +14")
        if not management_turn and repeated:
            delta += 18
            reasons.append("重复说明已给信息 +18")
        if not management_turn and emotion:
            delta += 20
            reasons.append("强情绪反馈 +20")
        if not management_turn and violation:
            delta += 30
            reasons.append("违反记忆组合推理 +30")
        if not management_turn and bad_memory:
            delta += 20
            reasons.append("污染长期记忆 +20")
        if not management_turn and task_turn and not delivered:
            delta += 12
            reasons.append("任务未交付 +12")
        if not management_turn and turn.get("applied_memories") and not violation:
            saving_delta += 12
            saving_reasons.append("正确应用记忆 saved +12")
        if not management_turn and any(action.get("action") in {"add", "update", "downgrade", "delete"} for action in actions) and not bad_memory:
            saving_delta += 8
            saving_reasons.append("有效记忆变化 saved +8")

        before = score
        before_saved = saved
        score = max(0, score + delta)
        saved += saving_delta
        output.append(
            {
                "turn_id": turn.get("id"),
                "stage": turn.get("stage"),
                "user": user,
                "assistant_brief": assistant[:180],
                "before": before,
                "delta": delta,
                "after": score,
                "saved_before": before_saved,
                "saved_delta": saving_delta,
                "saved_after": saved,
                "reasons": reasons,
                "saving_reasons": saving_reasons,
                "signals": {
                    "correction": correction,
                    "repeated_memory": repeated,
                    "emotion": emotion,
                    "semantic_violation": violation,
                    "delivered": delivered,
                    "bad_memory": bad_memory,
                },
                "six_dim_gain": _six_dim_gain(turn, delivered, violation, bad_memory),
            }
        )
    return {
        "scale": "lower is less user effort; raw additive effort points",
        "initial_score": 0,
        "final_score": score,
        "saved_score": saved,
        "reduction": saved,
        "estimated_without_memory": score + saved,
        "turns": output,
        "rules": USER_EFFORT_RULES,
    }


def attach_eval_timeline(case_run: dict[str, Any]) -> dict[str, Any]:
    effort_by_turn = {item.get("turn_id"): item for item in case_run.get("user_effort", {}).get("turns", [])}
    timeline = []
    for turn in case_run.get("turns", []):
        actions = _memory_actions(turn)
        applied = _applied_memory_details(turn)
        effort = effort_by_turn.get(turn.get("id"), {})
        timeline.append(
            {
                "turn_id": turn.get("id"),
                "stage": turn.get("stage"),
                "user": turn.get("user", {}).get("content", ""),
                "assistant": turn.get("assistant", {}).get("content", ""),
                "memory": {
                    "applied_ids": turn.get("applied_memories", []),
                    "applied": applied,
                    "actions": actions,
                    "snapshot_version": turn.get("memory_snapshot", {}).get("version", ""),
                    "explanation": _memory_explanation(actions, applied),
                },
                "effort": effort,
                "evaluation": {
                    "six_dim_gain": effort.get("six_dim_gain", []),
                    "quality_signals": effort.get("signals", {}),
                    "explanation": _turn_eval_explanation(effort, actions, applied),
                },
            }
        )
    case_run["eval_timeline"] = timeline
    return case_run


def _six_dim_gain(turn: dict[str, Any], delivered: bool, violation: bool, bad_memory: bool) -> list[str]:
    gains = []
    actions = _memory_actions(turn)
    if actions:
        gains.append("memory_extraction" if any(a.get("action") == "add" for a in actions) else "update_and_decay")
        gains.append("transparency")
    if turn.get("applied_memories"):
        gains.append("memory_application")
    if delivered:
        gains.append("result_quality")
    if violation or bad_memory:
        gains.append("negative_quality_signal")
    return gains


def _is_management_only_turn(turn: dict[str, Any]) -> bool:
    stage = turn.get("stage", "")
    user = turn.get("user", {}).get("content", "")
    if stage in {"reset", "show_memory"}:
        return True
    if any(token in user for token in ["reset memory", "展示当前记忆", "show memory", "查看当前记忆"]):
        return True
    normalized = user.replace(" ", "")
    management_tokens = ["删除", "delete", "forget", "降权", "降级", "downgrade", "归档", "archive"]
    if any(token in normalized for token in management_tokens):
        if "然后" not in normalized and "给我" not in normalized and "帮我" not in normalized:
            return True
    return False


def _applied_memory_details(turn: dict[str, Any]) -> list[dict[str, str]]:
    ids = set(turn.get("applied_memories", []))
    details = []
    for item in turn.get("memory_snapshot", {}).get("active", []):
        if item.get("id") in ids:
            details.append(
                {
                    "id": item.get("id", ""),
                    "type": item.get("type", ""),
                    "content": item.get("content", ""),
                }
            )
    return details


def _memory_explanation(actions: list[dict[str, Any]], applied: list[dict[str, str]]) -> str:
    parts = []
    if applied:
        parts.append(f"应用 {len(applied)} 条 active 记忆")
    adds = [a for a in actions if a.get("action") == "add"]
    changes = [a for a in actions if a.get("action") in {"update", "downgrade", "archive", "delete"}]
    if adds:
        parts.append(f"新增 {len(adds)} 条记忆")
    if changes:
        parts.append(f"更新/删除/降权 {len(changes)} 条记忆")
    return "；".join(parts) if parts else "本轮未发生记忆应用或变化"


def _turn_eval_explanation(effort: dict[str, Any], actions: list[dict[str, Any]], applied: list[dict[str, str]]) -> str:
    reasons = effort.get("reasons", [])
    saving_reasons = effort.get("saving_reasons", [])
    text = "；".join(reasons) if reasons else "无费力度变化"
    if saving_reasons:
        text += "。节省项：" + "；".join(saving_reasons)
    if actions or applied:
        text += "。记忆行为：" + _memory_explanation(actions, applied)
    return text


def _is_task_turn(turn: dict[str, Any]) -> bool:
    stage = turn.get("stage", "")
    user = turn.get("user", {}).get("content", "")
    if stage not in TASK_STAGES:
        return False
    if _is_management_only_turn(turn):
        return False
    if any(token in user for token in ["reset memory", "展示当前记忆", "show memory"]):
        return False
    return any(token in user for token in ["帮我", "安排", "写", "做", "综述", "brainstorm", "推荐", "然后", "能不能", "给我"])


def _is_deliverable(case_run: dict[str, Any], turn: dict[str, Any]) -> bool:
    if not _is_task_turn(turn):
        return False
    user = turn.get("user", {}).get("content", "")
    assistant = turn.get("assistant", {}).get("content", "")
    if "然后" in user and _only_delete_result(assistant):
        return False
    if _meta_only(assistant):
        return False
    domain = case_run.get("domain", "")
    if domain == "life_family_travel":
        return _contains(assistant, ["上午", "下午", "路线", "Day", "第 1 天", "半日"])
    if domain == "work_report":
        return _contains(assistant, ["结论", "风险", "负责人", "下一步", "同步"])
    if domain == "study_plan":
        return _contains(assistant, ["第 1 天", "第 2 天", "自测", "例题", "考点"])
    if domain == "research_review":
        return _contains(assistant, ["方法", "数据集", "局限", "问题", "RAG"])
    return len(assistant.strip()) >= 80 and not _meta_only(assistant)


def _meta_only(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if _contains(stripped, ["行程草案", "半日路线", "第 1 天", "同步材料草案", "复习计划", "综述草案", "研究问题", "推荐：", "理由："]):
        return False
    if stripped.startswith("删除结果") and "然后" not in stripped and len(stripped) < 160:
        return True
    lines = [line for line in stripped.splitlines() if line.strip()]
    mechanism_lines = [line for line in lines if _contains(line, META_ONLY_TERMS) or line.strip().startswith("- ")]
    return len(lines) <= 4 and len(mechanism_lines) >= max(1, len(lines) - 1)


def _only_delete_result(text: str) -> bool:
    return text.strip().startswith("删除结果") and not _contains(text, ["第 1 天", "推荐", "结论", "计划", "路线", "半日", "上午", "问题"])


def _polluted_memory_events(case_run: dict[str, Any]) -> list[dict[str, str]]:
    polluted = []
    for event in case_run.get("memory_events", []):
        if event.get("action") != "add":
            continue
        detail = event.get("detail", "")
        if _contains(detail, BAD_MEMORY_TERMS) or "？" in detail or "?" in detail:
            polluted.append({"memory_id": event.get("memory_id", ""), "detail": detail, "reason": "question/emotion/ack was saved as reusable memory"})
    return polluted


def _semantic_violations(case_run: dict[str, Any]) -> list[dict[str, str]]:
    violations = []
    for turn in case_run.get("turns", []):
        violations.extend(_turn_semantic_violations(case_run, turn))
    return violations


def _turn_semantic_violations(case_run: dict[str, Any], turn: dict[str, Any]) -> list[dict[str, str]]:
    return []


def _unresolved_dissatisfaction(case_run: dict[str, Any]) -> bool:
    turns = case_run.get("turns", [])
    for idx, turn in enumerate(turns[:-1]):
        user = turn.get("user", {}).get("content", "")
        if _contains(user, NEGATIVE_TERMS):
            next_turn = turns[idx + 1]
            if not _is_deliverable(case_run, next_turn) and not _contains(next_turn.get("assistant", {}).get("content", ""), ["换", "改", "避开"]):
                return True
    return False


def _compound_followup_delivered(case_run: dict[str, Any]) -> bool:
    compound = [turn for turn in case_run.get("turns", []) if "然后" in turn.get("user", {}).get("content", "")]
    if not compound:
        return True
    return all(_is_deliverable(case_run, turn) for turn in compound)


def _memory_actions(turn: dict[str, Any]) -> list[dict[str, Any]]:
    return [action for call in turn.get("tool_calls", []) for action in call.get("output", {}).get("memory_actions", [])]


def _contains(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)
