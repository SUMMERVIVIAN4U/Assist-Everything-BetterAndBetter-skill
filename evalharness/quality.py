from __future__ import annotations

import re
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
    {"name": "用户轮次", "delta": "+1", "description": "每个非管理类用户输入计一次操作成本。"},
    {"name": "输入长度", "delta": "每 50 字 +1", "description": "用户输入越长，说明补充信息成本越高。"},
    {"name": "被追问", "delta": "+2", "description": "agent 需要用户补充本应从上下文或记忆获得的信息。"},
    {"name": "重复说明", "delta": "+3", "description": "用户重复之前已经表达的记忆、历史、约束或已选方案。"},
    {"name": "纠错/不满", "delta": "+3", "description": "用户指出 agent 忘记、误用、推荐方向错误或需要换方案。"},
    {"name": "严重错误", "delta": "+5", "description": "任务未交付、记忆误用、污染记忆或明显挫败信号。"},
    {"name": "记忆节省信息点", "delta": "每个信息点 +1", "description": "本轮由记忆提供、用户未重复说明、且被正确应用的信息点。"},
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
        serious_error = emotion or violation or bad_memory or (task_turn and not delivered)
        if management_turn:
            delta = 0
            saving_delta = 0
            reasons = ["管理/查看/删除记忆轮不计入用户任务费力度"]
            saving_points: list[str] = []
        else:
            delta = 1
            reasons = ["用户轮次 +1"]
            input_cost = _input_length_cost(user)
            delta += input_cost
            reasons.append(f"输入长度 +{input_cost}")
            saving_delta = 0
            saving_points = _memory_saving_points(turn, user, violation=violation)

        if not management_turn and asks:
            delta += 2
            reasons.append("被追问 +2")
        if not management_turn and correction:
            delta += 3
            reasons.append("纠错/不满 +3")
        if not management_turn and repeated:
            delta += 3
            reasons.append("重复说明 +3")
        if not management_turn and serious_error:
            delta += 5
            reasons.append("严重错误 +5")
        if not management_turn and saving_points and not serious_error:
            saving_delta = len(saving_points)

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
                "saving_reasons": [f"复用信息点：{point}" for point in saving_points],
                "memory_saving_points": saving_points,
                "signals": {
                    "correction": correction,
                    "repeated_memory": repeated,
                    "emotion": emotion,
                    "semantic_violation": violation,
                    "delivered": delivered,
                    "bad_memory": bad_memory,
                    "serious_error": serious_error,
                },
                "six_dim_gain": _six_dim_gain(turn, delivered, violation, bad_memory),
            }
        )
    return {
        "scale": "费力度越低越省力；记忆节省信息点越多，说明用户少重复说明的信息越多。Agent Chat 中一次 Eval 对应当前 session，final_score 是该 session 的费力度。",
        "initial_score": 0,
        "final_score": score,
        "memory_saving_points": saved,
        "saved_score": saved,
        "reduction": saved,
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


def _input_length_cost(user: str) -> int:
    length = len(re.sub(r"\s+", "", user or ""))
    return (length + 49) // 50 if length else 0


def _memory_saving_points(turn: dict[str, Any], user: str, *, violation: bool) -> list[str]:
    if violation:
        return []
    user_norm = _normalize_info_point(user)
    points: list[str] = []
    seen: set[str] = set()
    for item in _applied_memory_details(turn):
        for point in _memory_info_points(item.get("content", "")):
            normalized = _normalize_info_point(point)
            if not normalized or normalized in seen:
                continue
            if normalized in user_norm:
                continue
            seen.add(normalized)
            points.append(point)
    return points


def _memory_info_points(content: str) -> list[str]:
    text = str(content or "").strip()
    if not text:
        return []
    text = re.sub(r"^.+?[:：]", "", text) if any(marker in text for marker in ["偏好/背景", "约束"]) else text
    fragments = re.split(r"[；;\n]+", text)
    points: list[str] = []
    for fragment in fragments:
        point = fragment.strip(" ，,。:：-")
        if not point:
            continue
        if point in {"暂无", "待补充"}:
            continue
        points.append(point)
    return points or [text]


def _normalize_info_point(text: str) -> str:
    return re.sub(r"[\s，,。；;：:、\-—_（）()【】\[\]\"'“”]+", "", str(text or "").lower())


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
        text += "。记忆节省信息点：" + "；".join(saving_reasons)
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
    if _looks_like_gift_answer(user, assistant):
        return True
    return len(assistant.strip()) >= 80 and not _meta_only(assistant)


def _looks_like_gift_answer(user: str, assistant: str) -> bool:
    if not _contains(user, ["礼物", "推荐", "选", "送"]):
        return False
    text = assistant.strip()
    if len(text) < 30:
        return False
    if _meta_only(text):
        return False
    return _contains(text, ["礼物", "推荐", "理由", "预算", "元", "选定", "锁定", "方向"])


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
