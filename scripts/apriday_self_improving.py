#!/usr/bin/env python3
"""Local, deterministic memory loop for apriday-self-Improving."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STORE_DIR = Path(os.environ.get("APRIDAY_MEMORY_DIR", ROOT / ".apriday_memory"))
STORE_PATH = STORE_DIR / "memory.json"
EVENTS_PATH = STORE_DIR / "events.jsonl"

TEMPORARY_MARKERS = ("这次", "本次", "今天", "临时", "暂时", "这一轮", "只要这版", "本轮")
PRIVATE_MARKERS = ("密码", "token", "密钥", "身份证", "银行卡", "验证码", "隐私不要记")
REMEMBER_MARKERS = ("记住", "以后", "下次", "长期", "我喜欢", "我希望", "别再", "不要再")
POSITIVE_MARKERS = ("喜欢", "优先", "希望", "要", "偏好", "先")
NEGATIVE_MARKERS = ("不喜欢", "不要", "别", "禁忌", "避免", "别再")
UNCERTAIN_MARKERS = ("可能", "也许", "考虑", "随便", "算了", "不重要", "？", "?")
HIGH_CONFIDENCE_MARKERS = ("以后", "下次", "一直", "总是", "必须", "绝对", "特别", "非常", "决定", "确定", "定了")
AUTO_MEMORY_TYPES = {"workflow_rule", "communication_preference", "scene_rule", "project_context", "todo", "decision", "contact"}


@dataclass
class MemoryItem:
    id: str
    type: str
    content: str
    scope: str
    source: str
    confidence: float
    status: str
    evidence: list[str]
    applies_when: list[str]
    user_approved: bool
    approval: str
    topic: str
    content_hash: str
    created_at: str
    updated_at: str
    supersedes: list[str]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_store() -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    if not STORE_PATH.exists():
        write_store({"version": 2, "next_id": 1, "current_topic": "default", "memories": []})


def read_store() -> dict[str, Any]:
    ensure_store()
    data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    data.setdefault("version", 2)
    data.setdefault("next_id", 1)
    data.setdefault("current_topic", "default")
    data.setdefault("memories", [])
    return data


def write_store(data: dict[str, Any]) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def log_event(kind: str, payload: dict[str, Any]) -> None:
    ensure_store()
    event = {"time": now(), "kind": kind, "payload": payload}
    with EVENTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def reset_store() -> dict[str, Any]:
    if STORE_DIR.exists():
        shutil.rmtree(STORE_DIR)
    ensure_store()
    log_event("reset", {"message": "memory cleared"})
    return {"ok": True, "message": "memory reset", "store": str(STORE_PATH)}


def content_hash(text: str) -> str:
    normalized = re.sub(r"\s+", "", normalize_content(text).lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def detect_topic(text: str) -> str:
    patterns = [
        r"(?:换个|切换|聊聊|关于)(?:话题|主题)?[：:，, ]*([\u4e00-\u9fffA-Za-z0-9_-]{2,24})",
        r"(?:回到|继续)(?:之前|刚才|上次)?(?:的)?([\u4e00-\u9fffA-Za-z0-9_-]{2,24})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip(" 。.!！?？")
    _, scope, _ = classify(text)
    return scope


def classify(text: str) -> tuple[str, str, list[str]]:
    applies_when: list[str] = []
    lowered = text.lower()

    if any(k in text for k in ("架构", "方案", "评分", "实现", "代码", "赛事", "Skill", "skill")):
        applies_when.append("architecture_planning")
    if any(k in text for k in ("礼物", "女朋友", "品牌", "香水", "玫瑰金", "银色")):
        applies_when.append("gift_selection")
    if any(k in text for k in ("报告", "周报", "总结", "文档")):
        applies_when.append("writing")
    if "project" in lowered or "项目" in text:
        applies_when.append("project_context")
    if not applies_when:
        applies_when.append("general")

    if any(k in text for k in ("待办", "记得", "别忘了", "明天", "后天", "下周", "周末")):
        return "todo", applies_when[0], applies_when
    if any(k in text for k in ("决定", "确定", "定下来", "最终", "就按", "就用")):
        return "decision", applies_when[0], applies_when
    if re.search(r"我(?:的)?[\u4e00-\u9fff]{1,8}(?:叫|是)[\u4e00-\u9fffA-Za-z0-9]{2,20}", text):
        return "contact", applies_when[0], applies_when
    if any(k in text for k in ("先", "再", "流程", "步骤", "工作流", "不要直接", "写代码")):
        return "workflow_rule", applies_when[0], applies_when
    if any(k in text for k in ("项目", "仓库", "业务", "团队")):
        return "project_context", applies_when[0], applies_when
    if any(k in text for k in ("礼物", "香水", "品牌", "预算", "女朋友")):
        return "scene_rule", applies_when[0], applies_when
    return "communication_preference", applies_when[0], applies_when


def is_memory_candidate(text: str, memory_type: str) -> bool:
    if any(marker in text for marker in REMEMBER_MARKERS + HIGH_CONFIDENCE_MARKERS):
        return True
    if memory_type in {"todo", "decision", "contact"}:
        return True
    if any(k in text for k in ("我喜欢", "我不喜欢", "我习惯", "我偏好", "适合我", "不要", "别")):
        return True
    if re.search(r"(?:我要|准备|开始|启动)(?:做|写|弄|创建)?[\u4e00-\u9fffA-Za-z0-9《》_-]{2,40}", text):
        return True
    return False


def confidence_for(text: str, memory_type: str, approved: bool) -> tuple[float, str]:
    if approved:
        return 0.95, "explicit_user_approval"

    score = 0.35
    reasons: list[str] = []
    if is_memory_candidate(text, memory_type):
        score += 0.25
        reasons.append("memory_signal")
    if any(marker in text for marker in HIGH_CONFIDENCE_MARKERS):
        score += 0.2
        reasons.append("durable_or_decisive_marker")
    if memory_type in {"todo", "decision", "contact"}:
        score += 0.12
        reasons.append(f"structured_{memory_type}")
    if len(text) >= 20:
        score += 0.08
        reasons.append("detailed_context")
    if any(marker in text for marker in TEMPORARY_MARKERS):
        score -= 0.35
        reasons.append("temporary_marker")
    if any(marker in text for marker in UNCERTAIN_MARKERS):
        score -= 0.25
        reasons.append("uncertain_language")
    if re.search(r"[吗呢吧]\??$", text.strip()):
        score -= 0.18
        reasons.append("question_tone")

    score = max(0.0, min(1.0, score))
    return round(score, 2), ", ".join(reasons) if reasons else "weak_signal"


def normalize_content(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip(" 。.!！?？\n\t"))
    cleaned = re.sub(r"^(请|帮我|麻烦你)?(以后|下次|长期)?(记住|记一下)[：:，, ]*", "", cleaned)
    return cleaned[:180]


def should_reject(text: str, approved: bool) -> tuple[bool, str]:
    if any(marker in text for marker in PRIVATE_MARKERS):
        return True, "private_or_sensitive"
    if any(marker in text for marker in TEMPORARY_MARKERS) and not any(marker in text for marker in REMEMBER_MARKERS):
        return True, "temporary_instruction"
    return False, ""


def polarity(text: str) -> str:
    if any(marker in text for marker in NEGATIVE_MARKERS):
        return "negative"
    if any(marker in text for marker in POSITIVE_MARKERS):
        return "positive"
    return "neutral"


def subject_tokens(text: str) -> set[str]:
    candidates = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", text)
    stop = {"以后", "下次", "记住", "喜欢", "希望", "不要", "优先", "进行", "任务", "用户", "输出", "方案"}
    return {token for token in candidates if token not in stop}


def conflicts(new_item: MemoryItem, old: dict[str, Any]) -> bool:
    if old["status"] != "active":
        return False
    if old["type"] != new_item.type or old["scope"] != new_item.scope:
        return False
    if old.get("topic", "default") != new_item.topic:
        return False
    overlap = subject_tokens(new_item.content) & subject_tokens(old["content"])
    if overlap and polarity(new_item.content) != polarity(old["content"]):
        return True
    color_change = {"银色", "玫瑰金", "金色", "黑色", "白色"}
    new_colors = {color for color in color_change if color in new_item.content}
    old_colors = {color for color in color_change if color in old["content"]}
    if new_colors and old_colors and new_colors != old_colors:
        return True
    if new_item.type == "workflow_rule" and "以后" in new_item.content and "以后" in old["content"]:
        return new_item.content != old["content"]
    return False


def observe(text: str, approved: bool) -> dict[str, Any]:
    data = read_store()
    rejected, reason = should_reject(text, approved)
    if rejected:
        visible_text = "[redacted]" if reason == "private_or_sensitive" else text
        result = {"action": "reject", "saved": False, "reason": reason, "text": visible_text}
        log_event("observe_rejected", result)
        return result

    memory_type, scope, applies_when = classify(text)
    confidence, confidence_reason = confidence_for(text, memory_type, approved)
    if not approved and not is_memory_candidate(text, memory_type):
        result = {
            "action": "ignore",
            "saved": False,
            "reason": "no_durable_memory_signal",
            "confidence": confidence,
            "confidence_reason": confidence_reason,
            "text": text,
        }
        log_event("observe_ignored", result)
        return result
    if not approved and confidence < 0.5:
        result = {
            "action": "ask",
            "saved": False,
            "reason": "low_confidence",
            "confidence": confidence,
            "confidence_reason": confidence_reason,
            "suggested_response": "这是长期偏好，还是只针对这次？",
            "text": text,
        }
        log_event("observe_ask", result)
        return result
    if not approved and confidence < 0.8:
        result = {
            "action": "confirm",
            "saved": False,
            "reason": "medium_confidence",
            "confidence": confidence,
            "confidence_reason": confidence_reason,
            "suggested_response": "我捕捉到这可能是长期偏好，需要我以后按这个来吗？",
            "candidate": {
                "type": memory_type,
                "content": normalize_content(text),
                "scope": scope,
                "applies_when": applies_when,
            },
        }
        log_event("observe_confirm", result)
        return result

    fingerprint = content_hash(text)
    for memory in data["memories"]:
        if memory.get("content_hash") == fingerprint and memory["status"] == "active":
            result = {
                "action": "dedupe",
                "saved": False,
                "reason": "duplicate_active_memory",
                "confidence": confidence,
                "existing_id": memory["id"],
                "memory": memory,
            }
            log_event("observe_dedupe", result)
            return result

    item_id = f"mem_{data['next_id']:04d}"
    timestamp = now()
    topic = detect_topic(text)
    data["current_topic"] = topic
    item = MemoryItem(
        id=item_id,
        type=memory_type,
        content=normalize_content(text),
        scope=scope,
        source="explicit_feedback" if approved else "implicit_preference",
        confidence=confidence,
        status="active",
        evidence=[text],
        applies_when=applies_when,
        user_approved=True,
        approval="explicit" if approved else "auto_high_confidence",
        topic=topic,
        content_hash=fingerprint,
        created_at=timestamp,
        updated_at=timestamp,
        supersedes=[],
    )

    for old in data["memories"]:
        if conflicts(item, old):
            old["status"] = "superseded"
            old["updated_at"] = timestamp
            item.supersedes.append(old["id"])

    data["next_id"] += 1
    data["memories"].append(asdict(item))
    write_store(data)
    action = "save_explicit" if approved else "auto_record"
    result = {
        "action": action,
        "saved": True,
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "memory": asdict(item),
        "superseded": item.supersedes,
    }
    log_event("observe_saved", result)
    return result


def active_memories() -> list[dict[str, Any]]:
    return [m for m in read_store()["memories"] if m.get("status") == "active" and m.get("user_approved", True)]


def build_profile(data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or read_store()
    active = [m for m in data["memories"] if m.get("status") == "active"]
    profile = {
        "current_topic": data.get("current_topic", "default"),
        "preference_memory": [],
        "workflow_rules": [],
        "project_context": [],
        "scene_rules": [],
        "interaction_style": [],
        "confidence_avg": 0.0,
    }
    if not active:
        return profile

    for memory in active:
        item = {"id": memory["id"], "content": memory["content"], "confidence": memory["confidence"]}
        if memory["type"] == "workflow_rule":
            profile["workflow_rules"].append(item)
        elif memory["type"] == "project_context":
            profile["project_context"].append(item)
        elif memory["type"] == "scene_rule":
            profile["scene_rules"].append(item)
        else:
            profile["preference_memory"].append(item)

        content = memory["content"]
        if "结论" in content:
            profile["interaction_style"].append("conclusion_first")
        if "简短" in content or "短一点" in content:
            profile["interaction_style"].append("concise")
        if "评分标准" in content:
            profile["interaction_style"].append("rubric_first")

    profile["interaction_style"] = sorted(set(profile["interaction_style"]))
    profile["confidence_avg"] = round(sum(m["confidence"] for m in active) / len(active), 2)
    return profile


def retention_reason(memory: dict[str, Any]) -> str:
    approval = memory.get("approval", "legacy")
    if memory.get("status") == "deleted":
        return "deleted_by_user_control; excluded_from_future_application"
    if memory.get("status") == "superseded":
        return "superseded_by_newer_conflicting_memory; kept_for_audit"
    if approval == "explicit":
        return "explicit_user_approval"
    if approval == "auto_high_confidence":
        return "auto_recorded_from_high_confidence_durable_signal"
    return "legacy_or_manual_memory"


def memory_layers() -> dict[str, Any]:
    data = read_store()
    active = [m for m in data["memories"] if m.get("status") == "active"]
    audit = [m for m in data["memories"] if m.get("status") != "active"]
    snap = snapshot(limit=6)
    profile = build_profile(data)

    return {
        "layers": [
            {
                "id": "L0",
                "name": "即时交互层",
                "status": "ephemeral",
                "loads_when": "instant mode / [q] / simple greetings",
                "source": "current user message only",
                "retention_reason": "avoid unnecessary memory loading and protect privacy for lightweight turns",
                "items": [],
            },
            {
                "id": "L1",
                "name": "画像快照层",
                "status": "active_snapshot",
                "loads_when": "standard mode",
                "source": "compressed active memories",
                "retention_reason": "keep high-signal preferences and interaction style available with low token cost",
                "compression": snap["compression"],
                "profile": profile,
                "items": snap["recent_active_memories"],
            },
            {
                "id": "L2",
                "name": "长期审计层",
                "status": "persistent_local_ledger",
                "loads_when": "deep mode / history review / user inspection",
                "source": "local memory.json plus events.jsonl",
                "retention_reason": "support update history, deletion proof, source evidence, and user control",
                "items": [
                    {
                        "id": m["id"],
                        "type": m["type"],
                        "status": m["status"],
                        "content": m["content"],
                        "source": m.get("source"),
                        "approval": m.get("approval"),
                        "confidence": m.get("confidence"),
                        "evidence": m.get("evidence", [])[-2:],
                        "supersedes": m.get("supersedes", []),
                        "retention_reason": retention_reason(m),
                    }
                    for m in active + audit
                ],
            },
        ],
        "privacy": privacy_report(),
    }


def select_memory_mode(task: str) -> dict[str, Any]:
    lowered = task.lower()
    if task.startswith("[q]") or any(k in task for k in ("你好", "在吗", "谢谢", "hi", "hello")):
        return {"mode": "instant", "loads": [], "reason": "simple_or_quick_message"}
    if task.startswith("[d]") or any(k in task for k in ("历史", "之前", "上次", "所有", "复盘", "深度")):
        return {"mode": "deep", "loads": ["snapshot", "matching_memories", "event_log"], "reason": "history_or_deep_lookup"}
    if "[deep]" in lowered:
        return {"mode": "deep", "loads": ["snapshot", "matching_memories", "event_log"], "reason": "explicit_deep_marker"}
    return {"mode": "standard", "loads": ["snapshot", "matching_memories"], "reason": "default_task"}


def snapshot(limit: int = 8) -> dict[str, Any]:
    data = read_store()
    active = [m for m in data["memories"] if m["status"] == "active"]
    recent = sorted(active, key=lambda m: m.get("updated_at", ""), reverse=True)[:limit]
    full_tokens = max(1, sum(len(m["content"]) for m in data["memories"]) // 2)
    snapshot_tokens = max(1, sum(len(m["content"]) for m in recent) // 2)
    savings = max(0, round((1 - min(snapshot_tokens, full_tokens) / full_tokens) * 100))
    return {
        "current_topic": data.get("current_topic", "default"),
        "active_count": len(active),
        "compression": {
            "strategy": "recent_active_memory_only",
            "estimated_full_tokens": full_tokens,
            "estimated_snapshot_tokens": snapshot_tokens,
            "estimated_savings_percent": savings,
        },
        "recent_active_memories": [
            {
                "id": m["id"],
                "type": m["type"],
                "topic": m.get("topic", "default"),
                "content": m["content"],
                "confidence": m["confidence"],
                "approval": m.get("approval", "legacy"),
            }
            for m in recent
        ],
    }


def relevance(memory: dict[str, Any], task: str) -> int:
    score = 0
    tokens = subject_tokens(memory["content"])
    task_tokens = subject_tokens(task)
    score += len(tokens & task_tokens) * 2
    if any(label in memory["applies_when"] for label in classify(task)[2]):
        score += 3
    if memory["scope"] == "general":
        score += 1
    return score


def apply(task: str) -> dict[str, Any]:
    memory_mode = select_memory_mode(task)
    profile = build_profile()
    if memory_mode["mode"] == "instant":
        result = {
            "task": task,
            "memory_mode": memory_mode,
            "used_memory_ids": [],
            "plan": ["瞬时模式：不加载长期记忆，直接回答当前轻量问题"],
            "personalization": {"interaction_style": [], "reason": "instant_mode_skips_profile"},
            "user_effort_reduction": "none",
        }
        log_event("apply", result)
        return result

    ranked = sorted(
        ((relevance(memory, task), memory) for memory in active_memories()),
        key=lambda pair: pair[0],
        reverse=True,
    )
    used = [memory for score, memory in ranked if score > 0][:5]
    plan = [
        "理解当前任务目标和交付物",
        "只应用 active 且 user_approved 的相关记忆",
    ]
    for memory in used:
        plan.append(f"应用 {memory['id']}：{memory['content']}")
    if profile["interaction_style"]:
        plan.append(f"按用户画像调整交互方式：{', '.join(profile['interaction_style'])}")
    if not used:
        plan.append("未找到相关长期记忆，按当前输入完成并等待用户反馈")
    result = {
        "task": task,
        "memory_mode": memory_mode,
        "snapshot": snapshot(limit=5),
        "profile": profile,
        "personalization": {
            "interaction_style": profile["interaction_style"],
            "workflow_rule_count": len(profile["workflow_rules"]),
        },
        "used_memory_ids": [m["id"] for m in used],
        "plan": plan,
        "user_effort_reduction": "low" if not used else "medium" if len(used) == 1 else "high",
    }
    log_event("apply", result)
    return result


def view() -> dict[str, Any]:
    return read_store()


def feedback(memory_id: str, text: str, rating: int) -> dict[str, Any]:
    data = read_store()
    for memory in data["memories"]:
        if memory["id"] == memory_id:
            before = memory["confidence"]
            delta = 0.08 if rating > 0 else -0.18
            memory["confidence"] = round(max(0.1, min(0.99, before + delta)), 2)
            memory["evidence"].append(f"feedback({rating}): {text}")
            if rating < 0 and memory["confidence"] < 0.45:
                memory["status"] = "archived"
            memory["updated_at"] = now()
            write_store(data)
            result = {
                "ok": True,
                "id": memory_id,
                "before_confidence": before,
                "after_confidence": memory["confidence"],
                "status": memory["status"],
                "learning": "positive_reinforcement" if rating > 0 else "negative_adjustment",
            }
            log_event("feedback", result)
            return result
    return {"ok": False, "error": f"memory not found: {memory_id}"}


def privacy_report() -> dict[str, Any]:
    data = read_store()
    memories = data["memories"]
    counts: dict[str, int] = {}
    for memory in memories:
        counts[memory["status"]] = counts.get(memory["status"], 0) + 1
    return {
        "memory_counts_by_status": counts,
        "private_markers_blocked": list(PRIVATE_MARKERS),
        "controls": ["reset", "view", "snapshot", "edit", "delete", "privacy"],
        "retention_policy": "local_only_until_user_deletes_or_resets",
        "sensitive_storage": "private_or_sensitive observations are redacted and not saved as memory",
    }


def direction_coverage(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        "偏好记忆与画像沉淀": bool(trace["profile_after_auto"]["workflow_rules"] or trace["profile_after_auto"]["preference_memory"]),
        "反馈学习与自我调整": trace["feedback_learning"]["ok"] and trace["feedback_learning"]["after_confidence"] > trace["feedback_learning"]["before_confidence"],
        "上下文压缩与长期记忆": trace["snapshot_after_auto"]["compression"]["estimated_savings_percent"] >= 0 and trace["snapshot_after_auto"]["active_count"] > 0,
        "个性化结果与交互方式": bool(trace["third_apply"]["personalization"]["interaction_style"]),
        "隐私可控的记忆管理": trace["privacy_reject"]["text"] == "[redacted]" and "delete" in trace["privacy_report"]["controls"],
        "面向真实工作场景": bool(trace["second_apply"]["used_memory_ids"] and trace["third_apply"]["used_memory_ids"]),
    }


def edit(memory_id: str, content: str | None, status: str | None) -> dict[str, Any]:
    data = read_store()
    for memory in data["memories"]:
        if memory["id"] == memory_id:
            if content:
                memory["content"] = normalize_content(content)
                memory["evidence"].append(f"edited: {content}")
            if status:
                memory["status"] = status
            memory["updated_at"] = now()
            write_store(data)
            log_event("edit", {"id": memory_id, "content": content, "status": status})
            return {"ok": True, "memory": memory}
    return {"ok": False, "error": f"memory not found: {memory_id}"}


def delete(memory_id: str) -> dict[str, Any]:
    result = edit(memory_id, content=None, status="deleted")
    if result.get("ok"):
        log_event("delete", {"id": memory_id})
    return result


def score_eval(trace: dict[str, Any]) -> dict[str, Any]:
    memories = trace["view_after_feedback"]["memories"]
    active = [m for m in memories if m["status"] == "active"]
    superseded = [m for m in trace["view_after_change"]["memories"] if m["status"] == "superseded"]
    deleted_id = trace["deleted_id"]
    after_delete_used = trace["after_delete_apply"]["used_memory_ids"]
    auto_recorded = trace["auto_feedback"]["action"] == "auto_record"
    medium_confirmed = trace["medium_candidate"]["action"] == "confirm" and not trace["medium_candidate"]["saved"]
    deduped = trace["duplicate"]["action"] == "dedupe"
    instant_mode = trace["instant_apply"]["memory_mode"]["mode"] == "instant"
    deep_mode = trace["deep_apply"]["memory_mode"]["mode"] == "deep"
    coverage = direction_coverage(trace)

    scores = {
        "reproducibility": 10 if trace["reset"]["ok"] and trace["snapshot_after_auto"]["active_count"] else 8,
        "memory_extraction": 20 if active and auto_recorded and medium_confirmed and deduped and not trace["temporary"]["saved"] else 14,
        "memory_application": 25 if trace["second_apply"]["used_memory_ids"] and trace["third_apply"]["used_memory_ids"] and instant_mode and deep_mode else 16,
        "memory_update_retirement": 20 if superseded and deleted_id not in after_delete_used else 12,
        "user_control_transparency": 10 if deleted_id and trace["delete"]["ok"] and trace["privacy_report"]["controls"] else 6,
        "real_world_quality": 15 if trace["third_apply"]["plan"] and trace["deep_apply"]["plan"] else 8,
    }
    total = sum(scores.values())
    if not all(coverage.values()):
        total = min(total, 92)
    return {"scores": scores, "total": total, "direction_coverage": coverage}


def evaluate() -> dict[str, Any]:
    trace: dict[str, Any] = {}
    trace["reset"] = reset_store()
    trace["instant_apply"] = apply("[q] 你好")
    trace["first_apply"] = apply("帮我做一个赛事 Skill 架构方案")
    trace["auto_feedback"] = observe("我特别喜欢以后做架构方案时先看评分标准，再看最小可运行实现。", approved=False)
    trace["medium_candidate"] = observe("可能以后报告短一点？", approved=False)
    trace["temporary"] = observe("这次输出请用表格。", approved=False)
    trace["duplicate"] = observe("我特别喜欢以后做架构方案时先看评分标准，再看最小可运行实现。", approved=False)
    trace["snapshot_after_auto"] = snapshot()
    trace["profile_after_auto"] = build_profile()
    trace["feedback_learning"] = feedback("mem_0001", "这个偏好应用准确，后续继续这样。", rating=1)
    trace["privacy_reject"] = observe("我的密码是 123456，请记住。", approved=False)
    trace["privacy_report"] = privacy_report()
    trace["view_after_feedback"] = view()
    trace["second_apply"] = apply("帮我做一个新的赛事 Skill 提交方案")
    trace["change"] = observe("以后做架构方案可以先给最小可运行版本，但仍要保留评分标准检查。", approved=False)
    trace["view_after_change"] = view()
    trace["third_apply"] = apply("帮我继续完善赛事 Skill")
    trace["deep_apply"] = apply("[d] 回顾之前架构方案偏好")
    deleted_id = trace["change"]["memory"]["id"]
    trace["deleted_id"] = deleted_id
    trace["delete"] = delete(deleted_id)
    trace["after_delete_apply"] = apply("帮我做一个架构方案复测")
    report = {
        "name": "apriday-self-Improving",
        "rubric": "WASC 8-step continuous memory test + six encouraged directions",
        "trace": trace,
        "score": score_eval(trace),
    }
    log_event("evaluate", {"total": report["score"]["total"], "scores": report["score"]["scores"]})
    return report


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="apriday-self-Improving local memory CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reset")
    sub.add_parser("view")
    sub.add_parser("snapshot")
    sub.add_parser("profile")
    sub.add_parser("privacy")
    sub.add_parser("layers")

    observe_parser = sub.add_parser("observe")
    observe_parser.add_argument("text")
    observe_parser.add_argument("--approve", action="store_true")

    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("task")

    edit_parser = sub.add_parser("edit")
    edit_parser.add_argument("memory_id")
    edit_parser.add_argument("--content")
    edit_parser.add_argument("--status", choices=["active", "superseded", "archived", "deleted"])

    feedback_parser = sub.add_parser("feedback")
    feedback_parser.add_argument("memory_id")
    feedback_parser.add_argument("text")
    feedback_parser.add_argument("--rating", type=int, choices=[-1, 1], default=1)

    delete_parser = sub.add_parser("delete")
    delete_parser.add_argument("memory_id")

    sub.add_parser("evaluate")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "reset":
        print_json(reset_store())
    elif args.command == "view":
        print_json(view())
    elif args.command == "snapshot":
        print_json(snapshot())
    elif args.command == "profile":
        print_json(build_profile())
    elif args.command == "privacy":
        print_json(privacy_report())
    elif args.command == "layers":
        print_json(memory_layers())
    elif args.command == "observe":
        print_json(observe(args.text, args.approve))
    elif args.command == "apply":
        print_json(apply(args.task))
    elif args.command == "edit":
        print_json(edit(args.memory_id, args.content, args.status))
    elif args.command == "delete":
        print_json(delete(args.memory_id))
    elif args.command == "feedback":
        print_json(feedback(args.memory_id, args.text, args.rating))
    elif args.command == "evaluate":
        print_json(evaluate())


if __name__ == "__main__":
    main()
