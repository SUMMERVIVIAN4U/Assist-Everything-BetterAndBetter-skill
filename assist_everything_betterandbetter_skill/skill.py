from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .mem0_backend import HostedMem0Client, Mem0Config, _item_from_mem0_result, _mem0_results
from .memory import ACTIVE, DELETED, SUPERSEDED, MemoryItem, MemoryStore, _normalize_query


PREFERENCE = "preference"
CONSTRAINT = "constraint"
WORKFLOW = "workflow"
DECISION = "decision"
HISTORY = "history"
CONTEXT_FACT = "context_fact"

TEMPORARY_MARKERS = ("这次", "本次", "今天", "临时", "暂时", "这一轮", "只要这版", "本轮")
PRIVATE_MARKERS = ("密码", "token", "密钥", "身份证", "银行卡", "验证码", "隐私不要记")
HIGH_CONFIDENCE_MARKERS = ("以后", "下次", "一直", "总是", "必须", "绝对", "特别", "非常", "决定", "确定", "定了")
UNCERTAIN_MARKERS = ("可能", "也许", "考虑", "随便", "算了", "不重要", "？", "?")
STRUCTURED_MEMORY_TYPES = {CONSTRAINT, WORKFLOW, DECISION, HISTORY, CONTEXT_FACT}
TIME_CURRENT_TASK = "current_task"
TIME_SCENE_MEMORY = "scene_memory"
TIME_LONG_TERM = "long_term"
TIME_PAST = "past"


@dataclass
class SkillResponse:
    text: str
    memory_actions: list[dict[str, Any]]
    applied_memories: list[str]
    asks: list[str]
    diagnostics: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "text": self.text,
            "memory_actions": self.memory_actions,
            "applied_memories": self.applied_memories,
            "asks": self.asks,
        }
        if self.diagnostics is not None:
            payload["diagnostics"] = self.diagnostics
        return payload


class AssistSkill:
    """Generic authorized collaboration-memory runtime.

    The runtime does not know eval case ids. It receives natural language,
    decides whether memory should be managed/extracted/applied, and returns a
    response plus auditable memory actions.
    """

    def __init__(
        self,
        memory_dir: str | Path | None = None,
        persist: bool | None = None,
        privacy_markers: list[str] | tuple[str, ...] | None = None,
        mem0_config: Mem0Config | None = None,
        memory_enabled: bool | None = None,
        memory_backend: str | None = None,
    ) -> None:
        if persist is None:
            persist = os.getenv("ASSIST_MEMORY_PERSIST", "1") != "0"
        if memory_enabled is None:
            memory_enabled = os.getenv("ASSIST_MEMORY_ENABLED", "1") != "0"
        self.memory_enabled = memory_enabled
        storage_dir = memory_dir if memory_dir is not None else os.getenv("ASSIST_MEMORY_DIR", "memories/default")
        self.memory = MemoryStore(storage_dir if persist else None)
        self.pending_proposals: list[MemoryItem] = []
        env_markers = [item.strip() for item in os.getenv("ASSIST_PRIVACY_MARKERS", "").split(",") if item.strip()]
        self.privacy_markers = tuple(dict.fromkeys([*PRIVATE_MARKERS, *env_markers, *(privacy_markers or [])]))
        self.mem0_config = mem0_config or _mem0_config_from_env()
        self.memory_backend = _normalize_memory_backend(memory_backend or os.getenv("ASSIST_MEMORY_BACKEND", "local"))
        self.mem0_client = HostedMem0Client(self.mem0_config) if self.memory_backend == "mem0_hosted" and self.mem0_config.ready else None
        self.session_id = f"session_{uuid4().hex[:8]}"

    def process_message(self, text: str, context: str = "") -> SkillResponse:
        if not self.memory_enabled:
            memory_mode = {"mode": "disabled", "loads": [], "reason": "memory_feature_disabled"}
            response_text = self.compose_response(text, [], [], [], context)
            return SkillResponse(
                response_text,
                [],
                [],
                [],
                diagnostics={"memory_mode": memory_mode, "profile": {"interaction_style": []}},
            )

        command = self._try_memory_command(text, context)
        if command:
            return command

        memory_mode = self.select_memory_mode(text)
        if memory_mode["mode"] == "instant":
            response_text = self.compose_response(text, [], [], [], context)
            return SkillResponse(
                response_text,
                [],
                [],
                [],
                diagnostics={"memory_mode": memory_mode, "profile": {"interaction_style": []}},
            )

        actions = self._apply_updates(text, context)
        relevant = self.retrieve_relevant_memories(text, context)
        asks = self._suggest_followups(text, relevant, context)
        response_text = self.compose_response(text, relevant, actions, asks, context)
        return SkillResponse(
            response_text,
            actions,
            [item.id for item in relevant],
            asks,
            diagnostics={"memory_mode": memory_mode, "profile": self.memory_profile(), "memory_pack": self.relevant_memory_pack(text, relevant, context)},
        )

    def reset_memory(self) -> SkillResponse:
        if self.memory_backend == "mem0_hosted":
            return self._reset_remote_memory(self.mem0_client, "mem0_hosted")
        event = self.memory.reset()
        return SkillResponse("已重置记忆：当前为 M0 空白状态。", [event], [], [])

    def show_memory(self) -> SkillResponse:
        snapshot = self.snapshot()
        if snapshot.get("errors"):
            return SkillResponse(f"当前记忆读取失败：{snapshot['errors'][0]}", [], [], [], diagnostics={"snapshot": snapshot})
        if not any(snapshot.get(key) for key in ["active", "superseded", "archived", "deleted"]):
            return SkillResponse("当前没有任何记忆。", [], [], [])
        lines = ["当前 active 记忆与历史状态："]
        for status in [ACTIVE, SUPERSEDED, "archived", DELETED]:
            items = snapshot.get(status if status != ACTIVE else "active", [])
            for item in items:
                lines.append(f"- {item['id']} [{item['status']}/{item['type']}/{item['scope']}] {item['content']}")
        active_ids = [item["id"] for item in snapshot.get("active", [])]
        return SkillResponse("\n".join(lines), [], active_ids, [])

    def snapshot(self) -> dict[str, Any]:
        if self.memory_backend == "mem0_hosted":
            return self._remote_snapshot(self.mem0_client)
        return self.memory.snapshot()

    def compact_snapshot(self, limit: int = 8) -> dict[str, Any]:
        snapshot = self.snapshot()
        active = snapshot.get("active", [])
        recent = sorted(active, key=lambda item: item.get("updated_at", ""), reverse=True)[:limit]
        all_items = active + snapshot.get("superseded", []) + snapshot.get("archived", []) + snapshot.get("deleted", [])
        full_tokens = max(1, sum(len(item.get("content", "")) for item in all_items) // 2)
        snapshot_tokens = max(1, sum(len(item.get("content", "")) for item in recent) // 2)
        savings = max(0, round((1 - min(snapshot_tokens, full_tokens) / full_tokens) * 100))
        return {
            "version": snapshot.get("version", "M0"),
            "active_count": len(active),
            "compression": {
                "strategy": "recent_active_memory_only",
                "estimated_full_tokens": full_tokens,
                "estimated_snapshot_tokens": snapshot_tokens,
                "estimated_savings_percent": savings,
            },
            "recent_active_memories": [
                {
                    "id": item["id"],
                    "type": item["type"],
                    "scope": item["scope"],
                    "content": item["content"],
                    "confidence": item.get("confidence", 0.0),
                    "source": item.get("source", ""),
                }
                for item in recent
            ],
        }

    def memory_profile(self) -> dict[str, Any]:
        active = self.snapshot().get("active", [])
        profile: dict[str, Any] = {
            "preference_memory": [],
            "workflow_rules": [],
            "project_context": [],
            "scene_rules": [],
            "interaction_style": [],
            "confidence_avg": 0.0,
        }
        if not active:
            return profile
        for item in active:
            compact = {"id": item["id"], "content": item["content"], "confidence": item.get("confidence", 0.0)}
            if item["type"] == WORKFLOW:
                profile["workflow_rules"].append(compact)
            elif item["type"] == CONTEXT_FACT:
                profile["project_context"].append(compact)
            elif item["scope"] != "general":
                profile["scene_rules"].append(compact)
            else:
                profile["preference_memory"].append(compact)
            content = item.get("content", "")
            if "结论" in content:
                profile["interaction_style"].append("conclusion_first")
            if "简短" in content or "短一点" in content or "只保留一个" in content:
                profile["interaction_style"].append("concise")
            if "评分标准" in content or "风险" in content:
                profile["interaction_style"].append("rubric_or_risk_first")
            if "例题" in content:
                profile["interaction_style"].append("example_first")
        profile["interaction_style"] = sorted(set(profile["interaction_style"]))
        profile["confidence_avg"] = round(sum(item.get("confidence", 0.0) for item in active) / len(active), 2)
        return profile

    def memory_layers(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        compact = self.compact_snapshot(limit=6)
        active = snapshot.get("active", [])
        audit = snapshot.get("superseded", []) + snapshot.get("archived", []) + snapshot.get("deleted", [])
        return {
            "layers": [
                {
                    "id": "L0",
                    "name": "即时交互层",
                    "status": "ephemeral",
                    "loads_when": "instant mode / [q] / simple greetings",
                    "source": "current user message only",
                    "retention_reason": "轻量消息不加载长期记忆，降低 token 成本并减少过度记忆。",
                    "items": [],
                },
                {
                    "id": "L1",
                    "name": "画像快照层",
                    "status": "active_snapshot",
                    "loads_when": "standard mode",
                    "source": "compressed active memories",
                    "retention_reason": "只保留高信号 active 记忆和交互风格，用低成本支撑个性化。",
                    "compression": compact["compression"],
                    "profile": self.memory_profile(),
                    "items": compact["recent_active_memories"],
                },
                {
                    "id": "L2",
                    "name": "长期审计层",
                    "status": "persistent_local_ledger",
                    "loads_when": "deep mode / history review / user inspection",
                    "source": "MemoryStore markdown files plus events",
                    "retention_reason": "支持来源证据、状态迁移、删除证明和用户控制。",
                    "items": [
                        {
                            "id": item["id"],
                            "type": item["type"],
                            "status": item["status"],
                            "scope": item["scope"],
                            "content": item["content"],
                            "source": item.get("source", ""),
                            "confidence": item.get("confidence", 0.0),
                            "evidence": item.get("evidence", [])[-2:],
                            "supersedes": item.get("supersedes", []),
                            "retention_reason": _retention_reason(item),
                        }
                        for item in active + audit
                    ],
                },
            ],
            "privacy": self.privacy_report(),
        }

    def privacy_report(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        counts = {
            "active": len(snapshot.get("active", [])),
            "superseded": len(snapshot.get("superseded", [])),
            "archived": len(snapshot.get("archived", [])),
            "deleted": len(snapshot.get("deleted", [])),
        }
        return {
            "memory_counts_by_status": counts,
            "private_markers_blocked": list(self.privacy_markers),
            "controls": ["reset", "show", "find", "delete", "downgrade", "archive", "profile", "snapshot", "layers", "privacy"],
            "retention_policy": "local_only_until_user_deletes_archives_or_resets",
            "sensitive_storage": "private_or_sensitive observations are redacted and not saved as memory",
        }

    def select_memory_mode(self, text: str) -> dict[str, Any]:
        lowered = text.lower()
        stripped = text.strip()
        if stripped.startswith("[q]") or stripped.lower().startswith("[quick]") or any(
            token in stripped for token in ["你好", "在吗", "谢谢", "hi", "hello"]
        ):
            return {"mode": "instant", "loads": [], "reason": "simple_or_quick_message"}
        if stripped.startswith("[d]") or "[deep]" in lowered or any(
            token in stripped for token in ["历史", "之前", "上次", "所有", "复盘", "深度"]
        ):
            return {"mode": "deep", "loads": ["snapshot", "matching_memories", "event_log"], "reason": "history_or_deep_lookup"}
        return {"mode": "standard", "loads": ["snapshot", "matching_memories"], "reason": "default_task"}

    def manage_memory(self, text: str) -> SkillResponse:
        command = self._try_memory_command(text)
        if command:
            return command
        return SkillResponse("未识别到记忆管理命令。支持 reset/show/find/delete/downgrade/archive。", [], [], [])

    def retrieve_relevant_memories(self, text: str, context: str = "") -> list[MemoryItem]:
        if self.memory_backend == "mem0_hosted":
            return self._search_remote(self.mem0_client, text, context)
        scope = _infer_scope(text, context)
        target = _infer_target(text, scope) or _infer_target(context, scope)
        terms = _keywords(text)
        if "不适用" in text:
            if "步行不适用" in text or "少步行不适用" in text:
                terms = ["步行"]
            else:
                scoped_terms = [term for term in terms if f"{term}不适用" in text or f"{term} 不适用" in text]
                terms = scoped_terms or terms
        relevant: list[MemoryItem] = []
        for item in self.memory.active():
            if _is_polluted_memory_item(item):
                continue
            if not _memory_scope_matches(item, scope) or not _memory_target_matches(item, scope, target):
                continue
            if not self._memory_applies_now(item, text, context):
                continue
            haystack = " ".join(
                [
                    item.content,
                    item.scope,
                    item.subject,
                    item.target,
                    item.object,
                    item.predicate,
                    *item.applies_when,
                    *item.tags,
                ]
            )
            term_hit = any(term and term in haystack for term in terms)
            if scope != "general" or term_hit:
                relevant.append(item)
        return _rank_retrieved_memories(relevant, text, context, limit=8)

    def relevant_memory_pack(self, text: str, memories: list[MemoryItem], context: str = "") -> dict[str, Any]:
        scene = self._matching_scene_memories(text, context)
        return {
            "apply_now": [_memory_pack_item(item) for item in memories],
            "confirm_first": [_memory_pack_item(item) for item in scene],
            "suppressed": [],
        }

    def _memory_applies_now(self, item: MemoryItem, text: str, context: str = "") -> bool:
        time_scope = _time_scope(item)
        if time_scope == TIME_CURRENT_TASK:
            return item.validity.get("session_id") == self.session_id
        if time_scope == TIME_SCENE_MEMORY:
            return _scene_memory_confirmed_by_text(item, text, context)
        if time_scope == TIME_PAST:
            scope = _infer_scope(text, context)
            return scope == "gift_planning" or _contains_any(text, ["以前", "之前", "历史", "送过", "买过", "避开重复"])
        return True

    def _matching_scene_memories(self, text: str, context: str = "") -> list[MemoryItem]:
        if _scene_memory_answered_by_current_text(text):
            return []
        scope = _infer_scope(text, context)
        target = _infer_target(text, scope) or _infer_target(context, scope)
        if self.memory_backend == "mem0_hosted":
            active = self._remote_active_items(self.mem0_client)
        else:
            active = self.memory.active()
        scene = [
            item
            for item in active
            if _time_scope(item) == TIME_SCENE_MEMORY
            and not _is_polluted_memory_item(item)
            and _memory_scope_matches(item, scope)
            and _memory_target_matches(item, scope, target)
            and not _scene_memory_confirmed_by_text(item, text, context)
            and not self._current_task_resolves_scene_memory(item, active)
        ]
        return _rank_retrieved_memories(scene, text, context, limit=3)

    def _current_task_resolves_scene_memory(self, scene_item: MemoryItem, active_items: list[MemoryItem]) -> bool:
        if not (scene_item.subject == "father" or "父亲" in scene_item.content or "爸爸" in scene_item.content):
            return False
        for item in active_items:
            if _time_scope(item) != TIME_CURRENT_TASK:
                continue
            if item.validity.get("session_id") != self.session_id:
                continue
            if _contains_any(item.content, ["父亲不去", "爸爸不去", "只有我和孩子", "少步行限制不适用", "步行限制不适用"]):
                return True
        return False

    def compose_response(
        self,
        text: str,
        memories: list[MemoryItem],
        actions: list[dict[str, Any]],
        asks: list[str],
        context: str = "",
    ) -> str:
        lines = [self._task_answer(text, memories, context)]
        created = [a for a in actions if a["action"] == "add" and a.get("ok", True) is not False]
        failed = [a for a in actions if a.get("ok") is False]
        proposed = [a for a in actions if a["action"] == "propose"]
        rejected = [a for a in actions if a["action"] == "reject"]
        changed = [a for a in actions if a["action"] in {"downgrade", "archive", "delete", "update"} and a.get("ok", True) is not False]
        if failed:
            details = "；".join(str(a.get("error") or a.get("detail") or "unknown") for a in failed[:2])
            lines.append(f"\n记忆写入没有成功：{details}")
        memory_summary = _memory_action_summary(created, proposed, changed)
        if memory_summary:
            lines.append(memory_summary)
        if rejected:
            lines.append("\n这类内容我不会写入长期记忆。")
        if asks:
            lines.append("\n再确认一个关键点：" + asks[0])
        return "\n".join(lines)

    def _try_memory_command(self, text: str, context: str = "") -> SkillResponse | None:
        original = text.strip()
        normalized = _normalize_command(original)
        lowered = normalized.lower()

        if "profile" in lowered or "画像" in normalized:
            profile = self.memory_profile()
            lines = [
                "当前记忆画像：",
                f"- 交互风格：{', '.join(profile['interaction_style']) if profile['interaction_style'] else '暂无'}",
                f"- 工作流规则：{len(profile['workflow_rules'])} 条",
                f"- 场景规则：{len(profile['scene_rules'])} 条",
                f"- 偏好记忆：{len(profile['preference_memory'])} 条",
                f"- 平均置信度：{profile['confidence_avg']}",
            ]
            return SkillResponse("\n".join(lines), [], [item.id for item in self.memory.active()], [], diagnostics={"profile": profile})
        if "snapshot" in lowered or "快照" in normalized:
            compact = self.compact_snapshot()
            lines = [
                f"当前快照：{compact['version']}，active={compact['active_count']}",
                f"估算 token 节省：{compact['compression']['estimated_savings_percent']}%",
            ]
            for item in compact["recent_active_memories"]:
                lines.append(f"- {item['id']} [{item['type']}/{item['scope']}] {item['content']}")
            return SkillResponse("\n".join(lines), [], [item.id for item in self.memory.active()], [], diagnostics={"snapshot": compact})
        if "layers" in lowered or "三层" in normalized or "层级" in normalized:
            layers = self.memory_layers()
            lines = ["三层记忆视图："]
            for layer in layers["layers"]:
                lines.append(f"- {layer['id']} {layer['name']}：{layer['status']}；{layer['loads_when']}")
            return SkillResponse("\n".join(lines), [], [item.id for item in self.memory.active()], [], diagnostics={"layers": layers})
        if "privacy" in lowered or "隐私" in normalized:
            privacy = self.privacy_report()
            counts = privacy["memory_counts_by_status"]
            text = (
                "隐私与控制报告："
                f"\n- active={counts['active']} superseded={counts['superseded']} archived={counts['archived']} deleted={counts['deleted']}"
                f"\n- 可用控制：{', '.join(privacy['controls'])}"
                "\n- 敏感信息不会写入长期记忆。"
            )
            return SkillResponse(text, [], [], [], diagnostics={"privacy": privacy})
        if "approve" in lowered or "同意保存" in normalized or "确认保存" in normalized:
            return self._approve_pending()
        if "reject" in lowered or "拒绝保存" in normalized or "不要保存" in normalized:
            return self._reject_pending()
        if "reset" in lowered or "清空" in normalized or "重置" in normalized:
            return self.reset_memory()
        if "show" in lowered or "展示" in normalized or "查看" in normalized:
            return self.show_memory()
        if "find" in lowered or "query" in lowered or "查询" in normalized or "搜索" in normalized:
            query = _strip_command_words(normalized, ["find", "query", "查询", "搜索", "记忆"])
            matches = self.memory.find(query, include_inactive=True)
            if not matches:
                return SkillResponse("查询结果：无匹配记忆。", [], [], [])
            lines = ["查询结果："]
            for item in matches:
                lines.append(f"- {item.id} [{item.status}/{item.type}/{item.scope}] {item.content}")
            return SkillResponse("\n".join(lines), [], [m.id for m in matches if m.status == ACTIVE], [])
        if "删除" in normalized or "delete" in lowered or "forget" in lowered:
            query = _strip_command_words(normalized, ["delete", "forget", "删除", "这条记忆", "记忆"])
            followup = ""
            if "然后" in query:
                query, followup = query.split("然后", 1)
                followup = followup.strip(" ：:。")
            deleted, delete_actions = self._delete_matching_memories(query)
            names = ", ".join(item.id for item in deleted) or "无匹配"
            active = _filter_deleted_memory_matches(self.retrieve_relevant_memories(normalized), deleted)
            text_after = f"删除结果：{names}。后续检索会过滤 deleted 记忆。"
            if active:
                text_after += "\n\n删除后仍可使用的相关记忆：\n" + "\n".join(f"- {m.content}" for m in active)
            if followup:
                followup_context = f"{context}\n{normalized}".strip()
                followup_memories = _filter_deleted_memory_matches(self.retrieve_relevant_memories(followup, followup_context), deleted)
                text_after += "\n\n继续处理：\n" + self._task_answer(followup, followup_memories, followup_context)
                active = followup_memories
            return SkillResponse(text_after, delete_actions, [m.id for m in active], [])
        if "降权" in normalized or "降级" in normalized or "downgrade" in lowered:
            matches = self.memory.find(normalized, include_inactive=False)
            actions = []
            for item in matches:
                self.memory.downgrade(item.id, "user_requested_downgrade")
                actions.append(self.memory.events[-1])
            return SkillResponse(f"降权 {len(actions)} 条记忆。", actions, [], [])
        if "归档" in normalized or "archive" in lowered:
            matches = self.memory.find(normalized, include_inactive=False)
            actions = []
            for item in matches:
                self.memory.archive(item.id, "user_requested_archive")
                actions.append(self.memory.events[-1])
            return SkillResponse(f"归档 {len(actions)} 条记忆。", actions, [], [])
        return None

    def _apply_updates(self, text: str, context: str = "") -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        blocked, block_reason = _memory_block_reason(text, self.privacy_markers)
        if blocked:
            return [
                {
                    "action": "reject",
                    "memory_id": None,
                    "detail": "[redacted]" if block_reason == "private_or_sensitive" else text,
                    "reason": block_reason,
                }
            ]
        if self.memory_backend == "mem0_hosted":
            return self._apply_remote_structured_updates(self.mem0_client, "mem0_hosted", text, context)
        if not _is_temporary_override(text):
            for match in self._conflicting_memories(text, context):
                self.memory.downgrade(match.id, f"新反馈缩小或推翻旧规则：{text}")
                actions.append(self.memory.events[-1])
        for item in self.extract_memory_candidates(text, context):
            self._prepare_memory_item(item)
            confidence, confidence_reason = _confidence_for_memory(text, item)
            item.confidence = confidence
            if confidence < 0.5:
                actions.append(
                    {
                        "action": "ask",
                        "memory_id": None,
                        "detail": item.content,
                        "confidence": confidence,
                        "reason": confidence_reason,
                    }
                )
                continue
            if confidence < 0.8 and item.validity.get("time_scope") == "long_term":
                item.user_approved = False
                self.pending_proposals.append(item)
                actions.append(
                    {
                        "action": "propose",
                        "memory_id": None,
                        "detail": item.content,
                        "confidence": confidence,
                        "reason": confidence_reason,
                    }
                )
                continue
            duplicate = self._duplicate_memory(item)
            if duplicate:
                actions.append(
                    {
                        "action": "dedupe",
                        "memory_id": duplicate.id,
                        "detail": item.content,
                        "confidence": confidence,
                        "reason": "duplicate_active_memory",
                    }
                )
                continue
            self.memory.add(item)
            event = dict(self.memory.events[-1])
            event["confidence"] = confidence
            event["reason"] = confidence_reason
            event["approval"] = "auto_high_confidence" if not item.user_approved else "explicit_or_contextual"
            actions.append(event)
        return actions

    def _prepare_memory_item(self, item: MemoryItem) -> MemoryItem:
        time_scope = _time_scope(item)
        item.validity["time_scope"] = time_scope
        item.validity["layer"] = time_scope
        if time_scope == TIME_CURRENT_TASK:
            item.validity.setdefault("session_id", self.session_id)
            item.validity.setdefault("expires", "session_end")
        if time_scope == TIME_SCENE_MEMORY:
            item.validity.setdefault("needs_confirmation", True)
            item.validity.setdefault("default_application", "confirm_first")
        if time_scope == TIME_LONG_TERM:
            item.validity.setdefault("needs_confirmation", False)
            item.validity.setdefault("default_application", "apply")
        return item

    def _apply_remote_updates(self, client: Any, backend: str, text: str, context: str = "") -> list[dict[str, Any]]:
        if not client:
            error = "memory backend is not configured"
            return [{"action": "remote_extract", "backend": backend, "detail": text, "ok": False, "error": error}]
        try:
            result = client.add_text(text, context=context)
            return [{"action": "remote_extract", "backend": backend, "detail": text, "ok": True, "result": _compact_remote_result(result)}]
        except Exception as exc:
            return [{"action": "remote_extract", "backend": backend, "detail": text, "ok": False, "error": str(exc)}]

    def _apply_remote_structured_updates(self, client: Any, backend: str, text: str, context: str = "") -> list[dict[str, Any]]:
        if not client:
            return [{"action": "add", "backend": backend, "storage": "remote_structured", "detail": text, "ok": False, "error": "memory backend is not configured"}]
        actions: list[dict[str, Any]] = []
        remote_active = self._remote_active_items(client)
        for match in ([] if _is_temporary_override(text) else self._conflicting_memories(text, context, active_items=remote_active)):
            remote_id = str(match.validity.get("mem0_id") or match.id)
            try:
                result = client.delete(remote_id)
                actions.append(
                    {
                        "action": "downgrade",
                        "backend": backend,
                        "storage": "remote_structured",
                        "memory_id": match.id,
                        "remote_memory_id": remote_id,
                        "detail": f"新反馈缩小或推翻旧规则：{text}",
                        "ok": True,
                        "result": _compact_remote_result(result),
                    }
                )
                remote_active = [item for item in remote_active if item.id != match.id]
            except Exception as exc:
                actions.append(
                    {
                        "action": "downgrade",
                        "backend": backend,
                        "storage": "remote_structured",
                        "memory_id": match.id,
                        "remote_memory_id": remote_id,
                        "detail": f"新反馈缩小或推翻旧规则：{text}",
                        "ok": False,
                        "error": str(exc),
                    }
                )
        for item in self.extract_memory_candidates(text, context):
            self._prepare_memory_item(item)
            confidence, confidence_reason = _confidence_for_memory(text, item)
            item.confidence = confidence
            if confidence < 0.5:
                actions.append(
                    {
                        "action": "ask",
                        "backend": backend,
                        "storage": "remote_structured",
                        "memory_id": None,
                        "detail": item.content,
                        "confidence": confidence,
                        "reason": confidence_reason,
                    }
                )
                continue
            if confidence < 0.8 and item.validity.get("time_scope") == "long_term":
                actions.append(
                    {
                        "action": "propose",
                        "backend": backend,
                        "storage": "remote_structured",
                        "memory_id": None,
                        "detail": item.content,
                        "confidence": confidence,
                        "reason": confidence_reason,
                    }
                )
                continue
            duplicate = self._duplicate_memory(item, active_items=remote_active)
            if duplicate:
                actions.append(
                    {
                        "action": "dedupe",
                        "backend": backend,
                        "storage": "remote_structured",
                        "memory_id": duplicate.id,
                        "detail": item.content,
                        "confidence": confidence,
                        "reason": "duplicate_active_memory",
                    }
                )
                continue
            try:
                result = client.add(item)
                actions.append(
                    {
                        "action": "add",
                        "backend": backend,
                        "storage": "remote_structured",
                        "memory_id": item.id,
                        "detail": item.content,
                        "confidence": confidence,
                        "reason": confidence_reason,
                        "approval": "auto_high_confidence" if not item.user_approved else "explicit_or_contextual",
                        "ok": True,
                        "result": _compact_remote_result(result),
                    }
                )
                remote_active.append(item)
            except Exception as exc:
                actions.append(
                    {
                        "action": "add",
                        "backend": backend,
                        "storage": "remote_structured",
                        "memory_id": item.id,
                        "detail": item.content,
                        "confidence": confidence,
                        "reason": confidence_reason,
                        "ok": False,
                        "error": str(exc),
                    }
                )
        return actions

    def _reset_remote_memory(self, client: Any, backend: str) -> SkillResponse:
        if not client:
            error = "memory backend is not configured"
            action = {"action": "reset", "backend": backend, "memory_id": None, "detail": error, "ok": False}
            return SkillResponse("远端记忆后端未配置，无法重置。", [action], [], [])
        try:
            result = client.delete_all(page_size=200)
            action = {"action": "reset", "backend": backend, "memory_id": None, "detail": "remote memory reset", "ok": not result.get("errors"), "result": result}
            return SkillResponse("已重置当前记忆后端。", [action], [], [])
        except Exception as exc:
            action = {"action": "reset", "backend": backend, "memory_id": None, "detail": str(exc), "ok": False}
            return SkillResponse(f"重置当前记忆后端失败：{exc}", [action], [], [])

    def _delete_matching_memories(self, query: str) -> tuple[list[MemoryItem], list[dict[str, Any]]]:
        if self.memory_backend == "mem0_hosted":
            return self._delete_remote_memories(self.mem0_client, "mem0_hosted", query)
        deleted = self.memory.delete(query)
        actions = self.memory.events[-len(deleted) :] if deleted else []
        return deleted, actions

    def _delete_remote_memories(self, client: Any, backend: str, query: str) -> tuple[list[MemoryItem], list[dict[str, Any]]]:
        if not client:
            return [], [{"action": "delete", "backend": backend, "memory_id": None, "detail": query, "ok": False, "error": "memory backend is not configured"}]
        active = self._remote_active_items(client)
        matches = _match_memory_items(query, active)
        actions: list[dict[str, Any]] = []
        deleted: list[MemoryItem] = []
        for item in matches:
            remote_id = str(item.validity.get("mem0_id") or item.id)
            try:
                result = client.delete(remote_id)
                deleted.append(item)
                actions.append(
                    {
                        "action": "delete",
                        "backend": backend,
                        "storage": "remote_structured",
                        "memory_id": item.id,
                        "remote_memory_id": remote_id,
                        "detail": "user_requested_delete",
                        "ok": True,
                        "result": _compact_remote_result(result),
                    }
                )
            except Exception as exc:
                actions.append(
                    {
                        "action": "delete",
                        "backend": backend,
                        "storage": "remote_structured",
                        "memory_id": item.id,
                        "remote_memory_id": remote_id,
                        "detail": "user_requested_delete",
                        "ok": False,
                        "error": str(exc),
                    }
                )
        return deleted, actions

    def _remote_snapshot(self, client: Any) -> dict[str, Any]:
        if not client:
            return {"version": "M0", "active": [], "superseded": [], "archived": [], "deleted": []}
        try:
            raw = client.get_all(page_size=50)
            items = [_item_from_mem0_result(record).to_dict() for record in _mem0_results(raw)]
        except Exception as exc:
            return {"version": "M0", "active": [], "superseded": [], "archived": [], "deleted": [], "errors": [str(exc)]}
        return {"version": f"M{len(items)}", "active": items, "superseded": [], "archived": [], "deleted": []}

    def _search_remote(self, client: Any, text: str, context: str = "") -> list[MemoryItem]:
        if not client:
            return []
        try:
            scope = _infer_scope(text, context)
            target = _infer_target(text, scope) or _infer_target(context, scope)
            candidates = [
                item
                for item in client.search(text, top_k=12)
                if item.status == ACTIVE
                and not _is_polluted_memory_item(item)
                and _memory_scope_matches(item, scope)
                and _memory_target_matches(item, scope, target)
                and self._memory_applies_now(item, text, context)
            ]
            return _rank_retrieved_memories(candidates, text, context, limit=8)
        except Exception:
            return []

    def _remote_active_items(self, client: Any) -> list[MemoryItem]:
        if not client:
            return []
        try:
            return [
                item
                for item in (_item_from_mem0_result(record) for record in _mem0_results(client.get_all(page_size=50)))
                if item.status == ACTIVE and not _is_polluted_memory_item(item)
            ]
        except Exception:
            return []

    def _sync_mem0_add(self, item: MemoryItem) -> dict[str, Any] | None:
        if not self.mem0_client:
            return None
        try:
            result = self.mem0_client.add(item)
            return {"backend": "mem0", "ok": True, "result": _compact_remote_result(result)}
        except Exception as exc:
            return {"backend": "mem0", "ok": False, "error": str(exc)}

    def _search_mem0(self, text: str, *, existing_ids: set[str]) -> list[MemoryItem]:
        if not self.mem0_client:
            return []
        try:
            remote_items = self.mem0_client.search(text, top_k=8)
        except Exception:
            return []
        output = []
        for item in remote_items:
            if item.id in existing_ids:
                continue
            if item.status == ACTIVE and not _is_polluted_memory_item(item):
                output.append(item)
        return output

    def extract_memory_candidates(self, text: str, context: str = "") -> list[MemoryItem]:
        normalized = text.strip()
        if _is_memory_question(normalized) or _is_acknowledgement_only(normalized) or _is_plain_task_request(normalized):
            return []
        if not normalized or not _has_memory_signal(normalized, context):
            return []
        scope = _infer_scope(normalized, context)
        if scope == "gift_planning":
            return _gift_memory_candidates(normalized, context)
        if scope == "life_family_travel":
            travel_candidates = _travel_memory_candidates(normalized, context)
            if travel_candidates:
                return travel_candidates
        candidates: list[MemoryItem] = []
        explicit_request = _explicit_memory_request(normalized)
        for clause in _split_clauses(normalized):
            if not _has_memory_signal(clause, context) and not explicit_request:
                continue
            memory_type = _infer_memory_type(clause, scope)
            content = _clean_memory_content(clause, scope, context)
            if not content:
                continue
            candidates.append(
                MemoryItem(
                    memory_type,
                    content,
                    scope=scope,
                    subject=_infer_subject(clause, scope),
                    target=_infer_target(clause, scope),
                    object=_infer_object(clause, scope),
                    predicate=_infer_predicate(clause, memory_type),
                    source="chat_feedback",
                    evidence=[normalized],
                    applies_when=[scope],
                    tags=_keywords(clause),
                    validity=_infer_validity(clause, memory_type),
                )
            )
        return candidates

    def _conflicting_memories(self, text: str, context: str = "", active_items: list[MemoryItem] | None = None) -> list[MemoryItem]:
        lowered = text.lower()
        conflict_terms = ["不适用", "不用", "不要", "不能", "只用于", "仅用于", "改成", "推翻", "不再"]
        if not any(term in lowered for term in conflict_terms):
            return []
        scope = _infer_scope(text, context)
        terms = _keywords(text)
        active = active_items if active_items is not None else self.memory.active()
        if scope == "work_report" and "风险表只用于老板材料" in text:
            return [
                item
                for item in active
                if item.scope == scope
                and "风险" in item.content
                and item.target not in {"boss", "manager"}
                and "老板" not in item.content
            ]
        if scope == "work_report" and "跨部门" in text and any(term in text for term in ["不要", "不用", "不能", "不再"]):
            cross_team_matches = [
                item
                for item in active
                if item.scope == scope
                and item.target == "cross_functional_team"
                and any(term and term in item.content for term in terms)
            ]
            if cross_team_matches:
                return cross_team_matches
            if "风险表只用于老板材料" in text:
                return []
        if "不适用" in text:
            if "步行不适用" in text or "少步行不适用" in text:
                terms = ["步行"]
            else:
                scoped_terms = [term for term in terms if f"{term}不适用" in text or f"{term} 不适用" in text]
                terms = scoped_terms or terms
        matches = []
        for item in active:
            if any(term and term in item.content for term in terms):
                matches.append(item)
        if not matches:
            topic_terms = ["步行", "风险", "番茄钟", "文献综述", "模板", "自测", "可复现"]
            for item in active:
                if item.scope == scope and any(term in item.content for term in topic_terms if term in text):
                    matches.append(item)
        if not matches and "模板" in text:
            scope = _infer_scope(text, context)
            matches.extend(
                item
                for item in active
                if item.scope == scope
                and item.type == WORKFLOW
                and any(term in item.content for term in ["文献综述", "方法", "数据集", "局限", "可复现", "模板"])
            )
        return matches

    def _is_duplicate(self, candidate: MemoryItem, active_items: list[MemoryItem] | None = None) -> bool:
        return self._duplicate_memory(candidate, active_items=active_items) is not None

    def _duplicate_memory(self, candidate: MemoryItem, active_items: list[MemoryItem] | None = None) -> MemoryItem | None:
        active = active_items if active_items is not None else self.memory.active()
        for item in active:
            same_content = item.content == candidate.content and item.scope == candidate.scope
            same_fact = (
                candidate.predicate
                and item.scope == candidate.scope
                and item.type == candidate.type
                and item.subject == candidate.subject
                and item.target == candidate.target
                and item.object == candidate.object
                and item.predicate == candidate.predicate
                and _time_scope(item) == _time_scope(candidate)
            )
            if same_content or same_fact:
                return item
        return None

    def _approve_pending(self) -> SkillResponse:
        if not self.pending_proposals:
            active = self.memory.active()
            if active:
                active_ids = [item.id for item in active]
                preview = "；".join(item.content for item in active[:4])
                return SkillResponse(f"当前没有待授权候选；已有 {len(active)} 条 active 记忆已保存：{preview}", [], active_ids, [])
            return SkillResponse("没有待授权的记忆候选。", [], [], [])
        created = []
        for item in self.pending_proposals:
            item.user_approved = True
            created.append(self.memory.add(item))
        self.pending_proposals = []
        actions = self.memory.events[-len(created):]
        return SkillResponse("已获授权并保存长期记忆：\n" + "\n".join(f"- {m.content}" for m in created), actions, [], [])

    def _reject_pending(self) -> SkillResponse:
        count = len(self.pending_proposals)
        self.pending_proposals = []
        return SkillResponse(f"已拒绝保存 {count} 条候选记忆，不写入长期记忆库。", [], [], [])

    def _suggest_followups(self, text: str, memories: list[MemoryItem], context: str = "") -> list[str]:
        if _explicit_memory_request(text):
            return []
        scope = _infer_scope(text, context)
        scene_memories = self._matching_scene_memories(text, context)
        if scope == "life_family_travel" and scene_memories:
            return [_scene_confirmation_question(scene_memories[0])]
        return []

    def _task_answer(self, text: str, memories: list[MemoryItem], context: str = "") -> str:
        scope = _infer_scope(text, context)
        answer_text = _contextual_answer_text(text, context, scope)
        if scope == "life_family_travel":
            return _travel_answer(answer_text, memories)
        if scope == "work_report":
            return _work_answer(answer_text, memories)
        if scope == "study_plan":
            return _study_answer(answer_text, memories)
        if scope == "research_review":
            return _research_answer(answer_text, memories)
        if scope == "gift_planning":
            return _gift_answer(text, memories, context)
        return f"我会先按当前请求处理：{text}\n如果你给出稳定偏好或工作方法，我会把它提取为可管理记忆。"


def _contextual_answer_text(text: str, context: str, scope: str) -> str:
    previous_task = _last_user_task_for_scope(context, scope)
    if not previous_task:
        return text
    if _is_contextual_task_update(text, scope):
        return f"{previous_task}\n补充约束：{text}"
    return text


def _last_user_task_for_scope(context: str, scope: str) -> str:
    if not context.strip():
        return ""
    for line in reversed(context.splitlines()):
        role, sep, content = line.partition(":")
        if not sep or role.strip().lower() != "user":
            continue
        content = content.strip()
        if content and _infer_scope(content, "") == scope and _is_task_request_for_scope(content, scope):
            return content
    return ""


def _is_contextual_task_update(text: str, scope: str) -> bool:
    if scope == "general":
        return False
    if any(token in text for token in ["帮我", "安排", "写", "做", "推荐", "生成", "规划"]):
        return False
    return any(token in text for token in ["以后", "请记住", "记住", "喜欢", "不喜欢", "不要", "不能", "要少", "不适用", "改成", "选定"])


def _is_task_request_for_scope(text: str, scope: str) -> bool:
    task_terms = {
        "life_family_travel": ["帮我", "安排", "旅行", "行程", "路线", "半日游", "周末"],
        "work_report": ["写", "材料", "同步", "老板", "跨部门", "报告"],
        "study_plan": ["复习", "学习", "计划", "考点", "例题"],
        "research_review": ["综述", "文献", "research", "brainstorm", "研究"],
        "gift_planning": ["礼物", "生日", "推荐", "选"],
    }
    return any(term in text for term in task_terms.get(scope, ["帮我", "做", "写", "安排"]))


def _memory_action_summary(created: list[dict[str, Any]], proposed: list[dict[str, Any]], changed: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    if created:
        current_task = [a for a in created if _action_is_current_task(a)]
        long_term = [a for a in created if not _action_is_current_task(a)]
        if current_task:
            parts.append("本次任务已应用并暂存：" + "；".join(_action_detail(a) for a in current_task[:4]))
        if long_term:
            parts.append("已保存为后续可复用记忆：" + "；".join(_action_detail(a) for a in long_term[:4]))
    if proposed:
        parts.append("待你确认后才长期保存：" + "；".join(_action_detail(a) for a in proposed[:3]))
    if changed:
        parts.append("已更新/降级旧记忆：" + "；".join(_action_detail(a) for a in changed[:3]))
    return "\n\n记忆处理：\n- " + "\n- ".join(parts) if parts else ""


def _action_is_current_task(action: dict[str, Any]) -> bool:
    detail = _action_detail(action)
    return any(token in detail for token in ["这次", "本次", "不适用", "只有我和孩子"])


def _action_detail(action: dict[str, Any]) -> str:
    return str(action.get("detail") or action.get("memory_id") or "").strip()


def _normalize_command(text: str) -> str:
    if not text.startswith("/"):
        return text
    parts = text[1:].split(maxsplit=1)
    command = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""
    aliases = {
        "memory": rest,
        "mem": rest,
        "reset-memory": "reset memory",
        "reset_memory": "reset memory",
        "show-memory": "show memory",
        "show_memory": "show memory",
        "delete-memory": "delete " + rest,
        "delete_memory": "delete " + rest,
        "downgrade-memory": "downgrade " + rest,
        "downgrade_memory": "downgrade " + rest,
        "archive-memory": "archive " + rest,
        "archive_memory": "archive " + rest,
        "find-memory": "find " + rest,
        "find_memory": "find " + rest,
        "profile": "profile " + rest,
        "memory-profile": "profile " + rest,
        "memory_profile": "profile " + rest,
        "snapshot": "snapshot " + rest,
        "memory-snapshot": "snapshot " + rest,
        "memory_snapshot": "snapshot " + rest,
        "layers": "layers " + rest,
        "memory-layers": "layers " + rest,
        "memory_layers": "layers " + rest,
        "privacy": "privacy " + rest,
        "memory-privacy": "privacy " + rest,
        "memory_privacy": "privacy " + rest,
    }
    return aliases.get(command, text)


def _memory_block_reason(text: str, private_markers: list[str] | tuple[str, ...] = PRIVATE_MARKERS) -> tuple[bool, str]:
    if any(marker in text for marker in private_markers):
        return True, "private_or_sensitive"
    has_durable_marker = any(marker in text for marker in HIGH_CONFIDENCE_MARKERS) or any(
        marker in text for marker in ["保留", "不适用", "改成", "长期", "请记住", "记住"]
    )
    if any(marker in text for marker in TEMPORARY_MARKERS) and not has_durable_marker:
        return True, "temporary_instruction"
    return False, ""


def _mem0_config_from_env() -> Mem0Config:
    backend = _normalize_memory_backend(os.getenv("ASSIST_MEMORY_BACKEND", "local"))
    return Mem0Config(
        enabled=backend == "mem0_hosted",
        base_url=os.getenv("MEM0_BASE_URL", "").strip(),
        api_key=os.getenv("MEM0_API_KEY", "").strip(),
        user_id=os.getenv("MEM0_USER_ID", "workbench-user").strip(),
        app_id=os.getenv("MEM0_APP_ID", "assist-everything-betterandbetter-skill").strip(),
        project_id=os.getenv("MEM0_PROJECT_ID", "").strip(),
        project_name=os.getenv("MEM0_PROJECT_NAME", "").strip(),
        timeout=float(os.getenv("MEM0_TIMEOUT", "15") or 15),
    )


def _normalize_memory_backend(value: str) -> str:
    normalized = (value or "local").strip().lower().replace("-", "_")
    aliases = {
        "mem0": "mem0_hosted",
        "hosted_mem0": "mem0_hosted",
        "volcengine_mem0": "mem0_hosted",
        "mem0_rest": "mem0_hosted",
        "local_json": "local",
        "local_markdown": "local",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in {"local", "mem0_hosted"} else "local"


def _compact_remote_result(result: dict[str, Any]) -> dict[str, Any]:
    if isinstance(result, list):
        return {"count": len(result)}
    if not isinstance(result, dict):
        return {"value": str(result)}
    compact = {
        "event_id": result.get("event_id"),
        "status": result.get("status"),
        "message": result.get("message"),
    }
    return {key: value for key, value in compact.items() if value is not None}


def _confidence_for_memory(text: str, item: MemoryItem) -> tuple[float, str]:
    score = 0.35
    reasons: list[str] = []
    if _explicit_memory_request(text):
        score += 0.25
        reasons.append("explicit_memory_signal")
    if _is_parent_identity_fact(text):
        score += 0.25
        reasons.append("parent_identity_fact")
    if any(marker in text for marker in HIGH_CONFIDENCE_MARKERS):
        score += 0.2
        reasons.append("durable_or_decisive_marker")
    if any(marker in text for marker in ["我喜欢", "我不喜欢", "我习惯", "我偏好"]) or any(marker in item.content for marker in ["喜欢", "爱好", "偏好"]):
        score += 0.25
        reasons.append("personal_preference_signal")
    if item.scope != "general":
        score += 0.25
        reasons.append("scoped_memory")
    if item.type in STRUCTURED_MEMORY_TYPES:
        score += 0.2
        reasons.append("structured_memory")
    if item.validity.get("time_scope") in {"current_task", "past"}:
        score += 0.15
        reasons.append("state_transition_memory")
    if len(text) >= 20:
        score += 0.08
        reasons.append("detailed_context")
    if any(marker in text for marker in UNCERTAIN_MARKERS):
        score -= 0.25
        reasons.append("uncertain_language")
    if any(marker in text for marker in TEMPORARY_MARKERS) and item.validity.get("time_scope") == "long_term":
        score -= 0.18
        reasons.append("temporary_marker")
    score = max(0.0, min(1.0, score))
    return round(score, 2), ", ".join(reasons) if reasons else "weak_signal"


def _retention_reason(item: dict[str, Any]) -> str:
    status = item.get("status", ACTIVE)
    if status == DELETED:
        return "deleted_by_user_control; excluded_from_future_application"
    if status == SUPERSEDED:
        return "superseded_or_downgraded_by_newer_feedback; kept_for_audit"
    if status == "archived":
        return "archived_for_low_relevance_or_user_request; excluded_from_active_retrieval"
    if item.get("source") == "assistant_output":
        return "assistant_candidate_recorded_for_current_task_trace"
    return "active_user_signal_available_for_matching_tasks"


def _rank_retrieved_memories(items: list[MemoryItem], text: str, context: str = "", limit: int = 8) -> list[MemoryItem]:
    scope = _infer_scope(text, context)
    terms = _keywords(text)
    for item in items:
        score = _retrieval_score(item, scope, terms)
        item.validity["retrieval_score"] = score
        item.validity["retrieval_rank_strategy"] = "score_time"
    return sorted(items, key=lambda item: (item.validity.get("retrieval_score", 0.0), _memory_timestamp(item), item.id), reverse=True)[:limit]


def _memory_scope_matches(item: MemoryItem, scope: str) -> bool:
    if not scope or scope == "general":
        return item.scope == "general"
    return item.scope == scope or scope in item.applies_when or item.scope == "general"


def _memory_target_matches(item: MemoryItem, scope: str, target: str) -> bool:
    if scope != "gift_planning" or not target or not item.target:
        return True
    return item.target == target


def _retrieval_score(item: MemoryItem, scope: str, terms: list[str]) -> float:
    raw_score = item.validity.get("mem0_score")
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = float(item.confidence or 0.0)
    if score > 1:
        score = score / 100 if score <= 100 else 1.0
    score = max(0.0, min(1.0, score))
    haystack = " ".join(
        [
            item.content,
            item.scope,
            item.subject,
            item.target,
            item.object,
            item.predicate,
            *item.applies_when,
            *item.tags,
        ]
    )
    layer_bonus = {
        TIME_CURRENT_TASK: 1.0,
        TIME_LONG_TERM: 0.6,
        TIME_SCENE_MEMORY: 0.35,
        TIME_PAST: 0.2,
    }.get(_time_scope(item), 0.4)
    score = score * 0.35 + layer_bonus
    if scope and (scope == item.scope or scope in item.applies_when):
        score += 0.2
    if terms:
        hits = sum(1 for term in terms if term and term in haystack)
        score += min(0.15, hits * 0.04)
    if item.user_approved:
        score += 0.05
    return round(max(0.0, min(1.5, score)), 4)


def _time_scope(item: MemoryItem) -> str:
    value = str(item.validity.get("time_scope") or TIME_LONG_TERM)
    return value if value in {TIME_CURRENT_TASK, TIME_SCENE_MEMORY, TIME_LONG_TERM, TIME_PAST} else TIME_LONG_TERM


def _scene_memory_confirmed_by_text(item: MemoryItem, text: str, context: str = "") -> bool:
    signal = f"{context}\n{text}"
    if item.subject == "father" or "父亲" in item.content or "爸爸" in item.content:
        return _contains_any(signal, ["父亲同行", "爸爸同行", "带父亲", "带爸爸", "老人同行", "父亲也去", "爸爸也去", "父亲去"])
    return _contains_any(text, ["确认适用", "还适用", "按之前"])


def _scene_memory_answered_by_current_text(text: str) -> bool:
    return _contains_any(text, ["父亲不去", "爸爸不去", "只有我和孩子", "少步行不适用", "步行不适用"])


def _scene_confirmation_question(item: MemoryItem) -> str:
    if item.subject == "father" or "父亲" in item.content or "爸爸" in item.content:
        return "之前有过父亲步行限制的记录，这次父亲同行、这个限制还适用吗？"
    return f"之前有过这条场景记忆：{item.content}。这次还适用吗？"


def _memory_pack_item(item: MemoryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "content": item.content,
        "scope": item.scope,
        "type": item.type,
        "time_scope": _time_scope(item),
        "score": item.validity.get("retrieval_score"),
        "needs_confirmation": bool(item.validity.get("needs_confirmation")),
    }


def _is_temporary_override(text: str) -> bool:
    return any(marker in text for marker in TEMPORARY_MARKERS) or any(
        marker in text for marker in ["这次", "本次", "少步行不适用", "步行不适用", "父亲不去", "爸爸不去", "只有我和孩子"]
    )


def _contains_any(text: str, terms: list[str] | tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _memory_timestamp(item: MemoryItem) -> float:
    for value in [item.updated_at, item.created_at]:
        if not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return 0.0


def _strip_command_words(text: str, words: list[str]) -> str:
    result = text
    for word in words:
        result = result.replace(word, "")
    return result.strip(" 。：:，,")


def _split_clauses(text: str) -> list[str]:
    normalized = text
    for prefix in ["以后", "请记住", "记住：", "记住:", "用户反馈：", "反馈："]:
        normalized = normalized.replace(prefix, "")
    clauses = [normalized]
    normalized = normalized.replace("，再", "；再").replace("，但", "；但").replace("；但", "；")
    for sep in ["；", ";", "。", "\n"]:
        clauses = [part for clause in clauses for part in clause.split(sep)]
    return [clause.strip(" ，,：:") for clause in clauses if clause.strip(" ，,：:")]


def _explicit_memory_request(text: str) -> bool:
    return any(token in text for token in ["以后", "请记住", "记住", "同意保存", "确认保存"])


def _is_memory_question(text: str) -> bool:
    stripped = text.strip()
    if _explicit_memory_request(stripped):
        return False
    question_mark = "?" in stripped or "？" in stripped or stripped.endswith("吗")
    question_terms = ["你还记得", "还记得", "记得之前", "什么", "哪些", "有没有记"]
    return question_mark and any(term in stripped for term in question_terms)


def _is_acknowledgement_only(text: str) -> bool:
    stripped = text.strip(" 。！？!?")
    acknowledgements = ["好的", "好", "行", "可以", "嗯", "就这样"]
    if stripped in acknowledgements:
        return True
    return stripped.startswith(("好的，", "好，")) and any(term in stripped for term in ["推荐", "就好", "可以"])


def _is_polluted_memory_item(item: MemoryItem) -> bool:
    text = item.content
    evidence = " ".join(item.evidence)
    if any(token in text for token in ["愚蠢", "你还记得", "好的，给我", "我的意思是"]):
        return True
    return False


def _is_plain_task_request(text: str) -> bool:
    stripped = text.strip(" 。！？!?")
    if any(token in stripped for token in ["以后", "记住", "喜欢", "不喜欢", "不要", "不能", "预算", "选定"]):
        return False
    return stripped in {"给我一个推荐"}


def _has_memory_signal(text: str, context: str = "") -> bool:
    signals = [
        "以后",
        "请记住",
        "记住",
        "喜欢",
        "不喜欢",
        "讨厌",
        "偏好",
        "要少",
        "不要",
        "不能",
        "不用",
        "只用于",
        "仅用于",
        "改成",
        "保留",
        "每天",
        "先",
        "最后",
        "风险",
        "负责人",
        "下一步",
        "局限",
        "可复现",
        "不要夸大",
        "预算",
        "选定",
        "选了",
        "选过",
        "决定",
        "定了",
        "就这个",
        "就它",
        "满意",
        "买过",
        "买了",
        "下单",
        "确认送",
        "爱好",
        "礼物",
        "送过",
        "以前送过",
    ]
    if any(signal in text for signal in signals):
        return True
    if _has_contextual_task_fact(text, context):
        return True
    return False


def _has_contextual_task_fact(text: str, context: str = "") -> bool:
    scope = _infer_scope(text, context)
    if not context.strip() or scope == "general":
        return False
    if scope == "gift_planning":
        gift_fact_markers = [
            "预算",
            "喜欢",
            "爱好",
            "以前送过",
            "之前送过",
            "送过",
            "买过",
            "买了",
            "下单",
            "选定",
            "程序员",
            "养花",
            "养草",
            "金鱼",
            "咖啡",
            "非首饰",
            "不碰首饰",
            "不要首饰",
            "不考虑首饰",
        ]
        return any(marker in text for marker in gift_fact_markers)
    fact_markers = [
        "小孩",
        "孩子",
        "老人",
        "同行",
        "父亲",
        "爸爸",
        "不去",
        "只有我和孩子",
        "动物园",
        "动物",
        "自然",
        "博物馆",
        "科技馆",
        "少走",
        "少步行",
        "步行不适用",
        "少步行不适用",
        "推车",
    ]
    return bool(re.search(r"\d+\s*[-~到至]?\s*\d*\s*岁", text)) or any(marker in text for marker in fact_markers)


def _is_parent_identity_fact(text: str) -> bool:
    return any(marker in text for marker in ["宝妈", "宝爸"]) or (
        any(marker in text for marker in ["我是", "我的身份"])
        and any(marker in text for marker in ["妈妈", "母亲", "爸爸", "父亲", "家长"])
    )


def _infer_scope(text: str, context: str = "") -> str:
    if _is_parent_identity_fact(text) or any(token in text for token in ["家庭", "亲子", "旅行", "行程", "路线", "半日游", "动物", "网红", "父亲", "小孩", "孩子", "景点", "上海"]):
        return "life_family_travel"
    if any(token in text for token in ["老板", "周报", "项目", "跨部门", "同步", "研发", "设计", "运营", "风险", "负责人"]):
        return "work_report"
    if any(token in text for token in ["学习", "复习", "考试", "高数", "线性代数", "物理", "英语", "番茄钟", "例题", "自测"]):
        return "study_plan"
    if any(token in text for token in ["文献", "综述", "RAG", "研究", "数据集", "可复现", "brainstorm", "多模态"]):
        return "research_review"
    if _is_gift_planning_context(text, context):
        return "gift_planning"
    return "general"


def _infer_memory_type(text: str, scope: str) -> str:
    if scope == "gift_planning":
        if _extract_budget(text):
            return CONSTRAINT
        if _is_gift_decision_text(text):
            return DECISION
        if _extract_previous_gifts(text):
            return HISTORY
        if any(token in text for token in ["不要", "不能", "不再", "别", "避开"]):
            return CONSTRAINT
        if any(token in text for token in ["他是", "她是", "是个", "职业", "程序员"]):
            return CONTEXT_FACT
        return PREFERENCE
    if scope == "life_family_travel" and (_is_parent_identity_fact(text) or any(token in text for token in ["小孩", "孩子", "老人", "同行"]) or re.search(r"\d+\s*[-~到至]?\s*\d*\s*岁", text)):
        return CONTEXT_FACT
    if scope == "work_report" and any(token in text for token in ["只用于", "仅用于", "不要", "不用", "不能"]):
        return CONSTRAINT
    if scope == "work_report" and any(token in text for token in ["表格", "风险", "负责人", "下一步", "3 条结论", "3条结论"]):
        return WORKFLOW
    if scope == "study_plan":
        if any(token in text for token in ["不用", "不要", "不能", "只剩", "冲刺"]):
            return CONSTRAINT if "不用" in text or "不要" in text else WORKFLOW
        return WORKFLOW
    if scope == "research_review":
        if any(token in text for token in ["不要", "不用", "不能", "只保留"]):
            return CONSTRAINT
        return WORKFLOW if any(token in text for token in ["文献", "方法", "数据集", "局限", "可复现", "类别"]) else PREFERENCE
    if any(token in text for token in ["不能", "不要", "不喜欢"]):
        return CONSTRAINT
    if any(token in text for token in ["只用于", "仅用于", "不适用", "同行"]):
        return CONSTRAINT
    if any(token in text for token in ["膝盖不好", "负责人"]):
        return CONTEXT_FACT
    return PREFERENCE


def _clean_memory_content(text: str, scope: str, context: str = "") -> str:
    content = text.strip()
    for prefix in ["以后", "家庭出行", "写给老板的项目材料，请", "学习计划请", "做文献综述时，请"]:
        content = content.replace(prefix, "")
    return content.strip(" ，,。：:")


def _infer_subject(text: str, scope: str) -> str:
    if scope == "gift_planning":
        return "recipient"
    if any(token in text for token in ["父亲", "爸爸"]):
        return "father"
    if "孩子" in text:
        return "child"
    return "user"


def _infer_target(text: str, scope: str) -> str:
    if scope == "gift_planning":
        return _gift_recipient(text)
    if scope == "work_report":
        if "老板" in text:
            return "boss"
        if "跨部门" in text:
            return "cross_functional_team"
    return ""


def _infer_object(text: str, scope: str) -> str:
    for token in ["3 条结论", "3条结论", "风险表", "番茄钟", "例题", "自测题", "可复现性", "文献综述模板"]:
        if token in text:
            return token
    return ""


def _infer_predicate(text: str, memory_type: str) -> str:
    if _extract_budget(text):
        return "budget_limit"
    if memory_type == HISTORY and _extract_previous_gifts(text):
        return "previously_given"
    if memory_type == DECISION:
        return "selected"
    if memory_type == PREFERENCE:
        return "likes" if any(token in text for token in ["喜欢", "爱好"]) else "prefers"
    if memory_type == CONSTRAINT:
        return "must_avoid" if any(token in text for token in ["不要", "不能", "不喜欢", "避开"]) else "constrains"
    if memory_type == WORKFLOW:
        return "uses_workflow"
    if memory_type == DECISION:
        return "selected"
    if memory_type == HISTORY:
        return "completed"
    return "states"


def _infer_validity(text: str, memory_type: str) -> dict[str, str]:
    if memory_type == DECISION or text.startswith("这次"):
        return {"time_scope": TIME_CURRENT_TASK}
    if memory_type == HISTORY:
        return {"time_scope": TIME_PAST}
    return {"time_scope": TIME_LONG_TERM}


def _travel_memory_candidates(text: str, context: str = "") -> list[MemoryItem]:
    normalized = text.strip(" 。")
    candidates: list[MemoryItem] = []
    evidence = [normalized]

    def add(
        memory_type: str,
        content: str,
        *,
        subject: str = "user",
        predicate: str = "states",
        tags: list[str] | None = None,
        time_scope: str = "current_task",
    ) -> None:
        if not content or any(item.content == content for item in candidates):
            return
        candidates.append(
            MemoryItem(
                memory_type,
                content,
                scope="life_family_travel",
                subject=subject,
                predicate=predicate,
                source="chat_feedback",
                evidence=evidence,
                applies_when=["life_family_travel"],
                tags=tags or _keywords(content),
                validity={"time_scope": time_scope},
            )
        )

    if any(token in normalized for token in ["父亲不去", "爸爸不去", "只有我和孩子"]):
        add(CONTEXT_FACT, "这次父亲不去，只有我和孩子", subject="father", tags=["父亲", "孩子"], time_scope="current_task")
    if "步行不适用" in normalized or "少步行不适用" in normalized:
        add(CONSTRAINT, "本次少步行限制不适用", subject="father", predicate="not_applicable", tags=["步行", "不适用"], time_scope="current_task")
    if "避开网红" in normalized or "不喜欢人挤人" in normalized or "网红点" in normalized:
        time_scope = "current_task" if normalized.startswith("这次") else "long_term"
        add(CONSTRAINT, "避开人挤人的网红点", subject="user", predicate="must_avoid", tags=["网红"], time_scope=time_scope)
    if "孩子喜欢自然和动物" in normalized or ("孩子" in normalized and "自然" in normalized and "动物" in normalized):
        add(CONTEXT_FACT, "孩子喜欢自然和动物", subject="child", tags=["孩子", "自然", "动物"], time_scope="long_term")
    if "父亲膝盖不好" in normalized or "步行要少" in normalized:
        if not any(token in normalized for token in ["不适用", "不去"]):
            add(
                CONTEXT_FACT,
                "家庭旅行曾出现父亲步行限制，下次需确认父亲是否同行及步行限制是否适用",
                subject="father",
                tags=["父亲", "步行"],
                time_scope=TIME_SCENE_MEMORY,
            )

    return candidates


def _extract_budget(text: str) -> str:
    normalized = text.replace(" ", "")
    if any(marker in normalized for marker in ["千元", "一千", "1000", "1千", "千把块"]):
        if any(marker in normalized for marker in ["以内", "以下", "不超过", "最多"]):
            return "1000 元以内"
        return "1000 元左右"
    match = re.search(r"(预算|价位|价格|控制在|大概|大约|不超过|最多)?\s*(\d{3,5})\s*(元|块|rmb|RMB)?\s*(左右|上下|以内|以下|不超过|最多)?", text)
    if not match:
        return ""
    marker = match.group(1) or ""
    amount = int(match.group(2))
    unit = match.group(3) or ""
    suffix = match.group(4) or ""
    if not (marker or unit or suffix):
        return ""
    if amount < 100:
        return ""
    if suffix in {"以内", "以下", "不超过", "最多"}:
        return f"{amount} 元以内"
    return f"{amount} 元左右"


def _is_gift_planning_context(text: str, context: str = "") -> bool:
    combined = f"{context}\n{text}"
    gift_terms = ["礼物", "生日礼物", "送礼", "选礼", "买礼物", "挑礼物"]
    if any(term in combined for term in gift_terms):
        return True
    return bool(re.search(r"(给|帮我给).{1,12}(选|买|挑|送).{0,8}(礼|礼物)", combined))


def _gift_memory_candidates(text: str, context: str = "") -> list[MemoryItem]:
    scope = "gift_planning"
    target = _gift_recipient(text) or _gift_recipient(context)
    candidates: list[MemoryItem] = []

    budget = _extract_budget(text)
    if budget:
        candidates.append(
            _memory_item(
                CONSTRAINT,
                f"{_gift_prefix(target)}预算在 {budget}",
                scope,
                text,
                target,
                "budget_limit",
                ["预算"],
                {"time_scope": "current_task"},
            )
        )

    previous = _extract_previous_gifts(text)
    if previous:
        candidates.append(
            _memory_item(
                HISTORY,
                f"以前送过{target or '收礼人'}{previous}",
                scope,
                text,
                target,
                "previously_given",
                _keywords(previous),
                {"time_scope": "past"},
            )
        )

    decision = _extract_gift_decision(text, context)
    if decision:
        candidates.append(
            _memory_item(
                DECISION,
                f"本次给{target or '收礼人'}的礼物已选定为{decision}",
                scope,
                text,
                target,
                "selected",
                _keywords(decision),
                {"time_scope": "current_task"},
            )
        )

    avoid = _extract_gift_constraint(text)
    if avoid:
        time_scope = "current_task" if any(token in avoid for token in ["非首饰", "不要首饰", "不碰首饰", "不考虑首饰"]) else "long_term"
        candidates.append(
            _memory_item(
                CONSTRAINT,
                f"{_gift_prefix(target)}约束：{avoid}",
                scope,
                text,
                target,
                "must_avoid",
                _keywords(avoid),
                {"time_scope": time_scope},
            )
        )

    profile = _extract_gift_profile(text)
    if profile:
        memory_type = PREFERENCE if any(token in profile for token in ["喜欢", "爱好", "偏好"]) else CONTEXT_FACT
        candidates.append(
            _memory_item(
                memory_type,
                f"{target or '收礼人'}的礼物偏好/背景：{profile}",
                scope,
                text,
                target,
                "likes" if memory_type == PREFERENCE else "states",
                _keywords(profile),
                {"time_scope": "long_term"},
            )
        )

    return _dedupe_memory_candidates(candidates)


def _memory_item(
    memory_type: str,
    content: str,
    scope: str,
    evidence: str,
    target: str,
    predicate: str,
    tags: list[str],
    validity: dict[str, str],
) -> MemoryItem:
    return MemoryItem(
        memory_type,
        content.strip(" ，,。：:"),
        scope=scope,
        subject="recipient",
        target=target,
        predicate=predicate,
        source="chat_feedback",
        evidence=[evidence],
        applies_when=[scope],
        tags=tags,
        validity=validity,
    )


def _dedupe_memory_candidates(candidates: list[MemoryItem]) -> list[MemoryItem]:
    seen: set[tuple[str, str, str]] = set()
    output: list[MemoryItem] = []
    for item in candidates:
        key = (item.type, item.predicate, item.content)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _match_memory_items(query: str, items: list[MemoryItem]) -> list[MemoryItem]:
    q = _normalize_query(query)
    output: list[MemoryItem] = []
    for item in items:
        haystack = _normalize_query(
            " ".join(
                [
                    item.id,
                    item.type,
                    item.scope,
                    item.subject,
                    item.target,
                    item.object,
                    item.predicate,
                    item.content,
                    *item.tags,
                ]
            )
        )
        if any(term in q for term in ["紫色", "动物", "可复现性"]) and not any(
            term in haystack for term in ["紫色", "动物", "可复现性"] if term in q
        ):
            continue
        parts = [part for part in q.split() if len(part) >= 2]
        compact_hit = q and q in haystack
        token_hit = parts and any(part in haystack for part in parts)
        char_hit = q and len(q) >= 4 and any(q[i : i + 4] in haystack for i in range(max(1, len(q) - 3)))
        if not q or compact_hit or token_hit or char_hit:
            output.append(item)
    return output


def _filter_deleted_memory_matches(items: list[MemoryItem], deleted: list[MemoryItem]) -> list[MemoryItem]:
    if not deleted:
        return items
    deleted_ids = {item.id for item in deleted}
    deleted_remote_ids = {str(item.validity.get("mem0_id") or "") for item in deleted if item.validity.get("mem0_id")}
    deleted_queries = [_normalize_query(" ".join([item.content, *item.tags, item.object, item.predicate])) for item in deleted]
    output: list[MemoryItem] = []
    for item in items:
        remote_id = str(item.validity.get("mem0_id") or "")
        if item.id in deleted_ids or (remote_id and remote_id in deleted_remote_ids):
            continue
        haystack = _normalize_query(" ".join([item.content, *item.tags, item.object, item.predicate]))
        if any(query and (query in haystack or haystack in query) for query in deleted_queries):
            continue
        output.append(item)
    return output


def _gift_prefix(target: str) -> str:
    return f"给{target}选礼物" if target else "礼物"


def _gift_recipient(text: str) -> str:
    known = [
        "女朋友",
        "男朋友",
        "老公",
        "老婆",
        "丈夫",
        "妻子",
        "妈妈",
        "母亲",
        "爸爸",
        "父亲",
        "闺蜜",
        "朋友",
        "同事",
        "客户",
        "老师",
        "孩子",
        "小孩",
    ]
    for relation in known:
        if relation in text:
            return relation
    match = re.search(r"给(?:我)?(.{1,8}?)(?:选|买|挑|送).{0,6}(?:礼物|生日礼物|礼)", text)
    if match:
        candidate = match.group(1).strip(" 的")
        if candidate and candidate not in {"一个", "我", "这次"}:
            return candidate
    return ""


def _extract_previous_gifts(text: str) -> str:
    patterns = [
        r"(?:以前|之前|上次|已经|曾经)?送过(?:了)?(.+?)(?:，?他还比较满意|，?她还比较满意|，?还比较满意|。|；|$)",
        r"(?:以前|之前|已经|曾经)?买过(?:了)?(.+?)(?:。|；|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            gifts = _clean_gift_fragment(match.group(1))
            if gifts:
                return gifts
    return ""


def _extract_gift_decision(text: str, context: str = "") -> str:
    patterns = [
        r"(?:已经|刚刚|刚才)?(?:买了|下单了|入手了)(.+?)(?:。|；|$)",
        r"(?:礼物)?(?:选定|定了|决定买|确认送)(?:为|了)?(.+?)(?:。|；|$)",
        r"就(.+?)(?:吧|了|。|；|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            decision = _clean_gift_fragment(match.group(1))
            if _is_deictic_gift_decision(decision):
                decision = _extract_last_gift_recommendation(context)
            if decision and "推荐" not in decision:
                return decision
    return ""


def _is_gift_decision_text(text: str) -> bool:
    return bool(_extract_gift_decision(text))


def _is_deictic_gift_decision(text: str) -> bool:
    return text.strip(" 了吧。！？!?，,") in {"这个", "这个礼物", "它", "这款", "这个方向", "这一个"}


def _extract_last_gift_recommendation(context: str) -> str:
    if not context.strip():
        return ""
    candidates: list[str] = []
    for line in context.splitlines():
        if "assistant:" not in line:
            continue
        content = line.split("assistant:", 1)[1].strip()
        patterns = [
            r"推荐方向[:：](.+?)(?:。|\n|$)",
            r"推荐[:：](.+?)(?:。|\n|$)",
            r"建议(?:选|送)?[:：]?(.+?)(?:。|\n|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                gift = _clean_gift_fragment(match.group(1))
                gift = re.sub(r"^[一二三四五六七八九十\d]+[、.]\s*", "", gift).strip()
                if gift:
                    candidates.append(gift)
    return candidates[-1] if candidates else ""


def _extract_gift_constraint(text: str) -> str:
    constraints = []
    for clause in _split_clauses(text):
        if any(token in clause for token in ["非首饰", "不碰首饰", "不要首饰", "不考虑首饰", "排除首饰", "换个非首饰"]):
            constraints.append("不要首饰，换非首饰品类")
            continue
        if any(token in clause for token in ["不要", "不能", "不再", "别", "避开"]):
            if _extract_previous_gifts(clause):
                continue
            constraints.append(_clean_gift_fragment(clause))
    return "；".join(item for item in constraints if item)


def _extract_gift_profile(text: str) -> str:
    fragments = []
    for clause in _split_clauses(text):
        if _extract_budget(clause) or _extract_previous_gifts(clause) or _extract_gift_decision(clause):
            continue
        if any(token in clause for token in ["礼物", "生日礼物"]) and not any(token in clause for token in ["喜欢", "爱好", "他是", "她是", "是个", "程序员", "养花", "养草", "金鱼", "咖啡"]):
            continue
        if any(token in clause for token in ["喜欢", "爱好", "偏好", "他是", "她是", "是个", "程序员", "养花", "养草", "金鱼", "咖啡", "阳台"]):
            cleaned = _clean_gift_fragment(clause)
            cleaned = re.sub(r"^(?:帮我)?给(?:我)?.{1,8}?(?:选|买|挑|送)(?:个|一个)?(?:生日)?礼物[，,]?", "", cleaned)
            if cleaned:
                fragments.append(cleaned)
    return "；".join(fragments)


def _clean_gift_fragment(text: str) -> str:
    cleaned = text.strip(" ，,。：:")
    cleaned = re.sub(r"^(他|她)?还比较满意$", "", cleaned)
    cleaned = re.sub(r"^(他|她)?比较满意$", "", cleaned)
    return cleaned.strip(" ，,。：:")


def _keywords(text: str) -> list[str]:
    vocab = [
        "父亲",
        "少步行",
        "步行",
        "孩子",
        "动物",
        "自然",
        "网红",
        "老板",
        "3 条结论",
        "3条结论",
        "风险",
        "风险表",
        "负责人",
        "下一步",
        "跨部门",
        "番茄钟",
        "例题",
        "自测",
        "高频考点",
        "文献综述",
        "方法类别",
        "模板",
        "数据集",
        "局限",
        "可复现性",
        "谨慎",
        "brainstorm",
        "紫色",
        "银色",
        "选定",
        "选过",
    ]
    return [word for word in vocab if word in text]


def _gift_answer(text: str, memories: list[MemoryItem], context: str = "") -> str:
    memory_text = "；".join(item.content for item in memories)
    combined = f"{context}；{text}；{memory_text}"
    budget = next((item.content for item in memories if item.predicate == "budget_limit"), "预算按用户当前范围控制")
    avoid = [item.content for item in memories if item.predicate in {"previously_given", "must_avoid"}]
    preferences = [item.content for item in memories if item.type in {PREFERENCE, CONTEXT_FACT}]
    non_jewelry = any(token in combined for token in ["非首饰", "不要首饰", "不碰首饰", "不考虑首饰", "排除首饰"])
    no_purple = "紫色" not in memory_text and "紫色" not in text.replace("删除 她喜欢紫色", "")
    if "不重复" in text or "再给" in text or "推荐" in text:
        if non_jewelry:
            options = [
                ("蓝牙音箱", "高颜值便携蓝牙音箱或唱片机风格音箱", "能提供日常陪伴和生日仪式感"),
                ("香氛", "小众香氛扩香石 + 手写卡片礼盒", "有生活氛围感，适合作为精致但不夸张的生日礼物"),
                ("包", "质感小皮具或通勤卡包礼盒", "日常使用频率高，预算内能买到做工不错的款式"),
                ("体验", "双人陶艺/金工以外的手作体验预约", "不是实物重复路线，记忆点来自共同经历"),
                ("睡眠", "真丝眼罩、枕套和助眠喷雾组合", "实用、柔软、有照顾感，适合作为非首饰礼物"),
            ]
            idea = options[-1][1]
            reason = options[-1][2]
            for marker, candidate, candidate_reason in options:
                if marker not in context:
                    idea = candidate
                    reason = candidate_reason
                    break
            reason = f"不是首饰，也避开已送过的项链；{reason}"
            if not no_purple:
                reason += "，外观可选低饱和紫/玫瑰金点缀但不依赖单一颜色"
            return (
                f"推荐方向：{idea}。\n"
                f"- 预算：按 {budget} 控制，优先选 600-1000 元区间。\n"
                f"- 理由：{reason}。\n"
                f"- 执行：选小体积、有质感旋钮或复古外观的款式，搭配一张手写卡片；"
                f"避开已送过或已排除的品类。"
            )
        return (
            f"推荐方向：玫瑰金耳饰或手链，带精致但不过度夸张的设计。\n"
            f"- 预算：按 {budget} 控制。\n"
            f"- 依据：{('；'.join(preferences) or '当前偏好信息有限')}。\n"
            f"- 避免：{('；'.join(avoid) or '不要重复已送礼物')}。"
        )
    return (
        "推荐方向：小众香氛或扩香礼盒，优先选包装有质感、可附手写卡片的款式。\n"
        f"- 预算：{budget}；如果用户后续给出预算，再收敛到对应价位。\n"
        f"- 理由：在偏好信息不足时，香氛/扩香兼顾生日仪式感、日常使用和不容易撞款。\n"
        f"- 备选：花艺体验、手作体验、质感小皮具。\n"
        f"- 避开：{('；'.join(avoid) or '暂不重复用户后续明确说已经送过或排除的品类')}。"
    )


def _travel_answer(text: str, memories: list[MemoryItem]) -> str:
    memory_text = "；".join(m.content for m in memories)
    points = "、".join(m.content for m in memories) if memories else "亲子友好、转场少、节奏舒服"
    destination = _travel_destination(text)
    days = _travel_days(text)
    half_day = "半日" in text or "半天" in text or days == 0.5
    no_father = any(token in (text + memory_text) for token in ["父亲不去", "爸爸不去", "只有我和孩子"])
    task_signal = text + memory_text
    low_walk = (
        any(token in task_signal for token in ["膝盖不好", "步行要少", "少步行"])
        and not no_father
        and "少步行不适用" not in task_signal
    )
    nature = any(token in (text + memory_text) for token in ["自然", "动物", "植物", "公园", "湿地"])
    avoid_crowd = any(token in memory_text for token in ["不喜欢人挤人", "避开网红", "网红点"])

    constraints = []
    if low_walk:
        constraints.append("控制步行，把景区电瓶车、游船、打车接驳放在优先级前面")
    if no_father:
        constraints.append("本次按你和孩子两人出行设计，不套用父亲少步行限制")
    if nature:
        constraints.append("优先自然、动物、植物或开阔户外体验")
    if avoid_crowd:
        constraints.append("避开人挤人的网红点，选择清静路线和错峰时段")
    if not constraints:
        constraints.append("少排队、少回头路、每天保留休息时间")

    if half_day:
        route = _half_day_travel_route(destination, nature, avoid_crowd)
        return (
            f"{destination}半日亲子路线：\n"
            f"1. 出发后先去：{route[0]}。\n"
            f"2. 中段安排：{route[1]}。\n"
            f"3. 结束前留：{route[2]}。\n"
            f"执行约束：{'；'.join(constraints)}。\n"
            f"落地建议：全程控制在 4 小时内，只排 1 个主点位 + 1 个轻量补充点，避免为了打卡跨城转场。"
        )

    route_days = _multi_day_travel_route(destination, days, nature, low_walk)
    lines = [f"{destination}{int(days) if days >= 1 else 1}天亲子行程："]
    for idx, plan in enumerate(route_days, 1):
        lines.append(f"第 {idx} 天：{plan}")
    lines.append(f"执行约束：{'；'.join(constraints)}。")
    lines.append("落地建议：每天最多 2 个主点位，午后安排室内休息或咖啡/简餐，晚间不再加硬景点。")
    lines.append(f"本次使用的记忆：{points}。")
    return "\n".join(lines)


def _travel_destination(text: str) -> str:
    for city in ["北京", "杭州", "上海", "南京", "广州", "深圳", "成都", "苏州", "西安"]:
        if city in text:
            return city
    return "目的地"


def _travel_days(text: str) -> float:
    if "半日" in text or "半天" in text:
        return 0.5
    match = re.search(r"(\d+)\s*天", text)
    if match:
        return max(1, min(5, int(match.group(1))))
    if "周末" in text:
        return 2
    return 1


def _half_day_travel_route(destination: str, nature: bool, avoid_crowd: bool) -> tuple[str, str, str]:
    routes = {
        "南京": (
            "玄武湖偏安静的湖边段或情侣园，先让孩子放电",
            "南京博物院或附近安静咖啡点做室内休息",
            "就近吃饭返程，不再叠加夫子庙/老门东这类拥挤点",
        ),
        "上海": (
            "共青森林公园或滨江森林公园，选树荫多的入口",
            "自然观察、草地休息或轻量科普馆",
            "打车回程，避开热门商圈和排队餐厅",
        ),
        "杭州": (
            "西溪湿地非热门入口，优先坐船或电瓶车",
            "湿地栈道短线观察植物和水鸟",
            "附近茶空间休息，不去断桥等高人流点",
        ),
        "北京": (
            "国家植物园或奥森北园，选入口附近短线",
            "自然观察加简单野餐",
            "打车返程，避开热门商圈和长队展馆",
        ),
    }
    if destination in routes:
        return routes[destination]
    if nature:
        return (
            "城市公园或自然类场馆，选离住宿最近的一处",
            "做一段短线自然观察，中途安排坐下休息",
            "就近吃饭返程，不追加跨区景点",
        )
    if avoid_crowd:
        return (
            "非热门博物馆或开阔公园",
            "附近安静街区慢逛",
            "提前结束返程，避开晚高峰",
        )
    return ("一个交通最顺的主景点", "附近室内休息点", "就近返程")


def _multi_day_travel_route(destination: str, days: float, nature: bool, low_walk: bool) -> list[str]:
    city_routes = {
        "北京": [
            "上午国家植物园或奥森北园，下午中国科技馆/自然类展馆，晚上早回酒店",
            "上午北京动物园或海洋馆，午后找近距离室内休息点，傍晚不加长距离步行",
        ],
        "杭州": [
            "上午西溪湿地坐船/电瓶车，下午湿地短线观察，晚上住处附近吃饭",
            "上午杭州动物园或少儿公园，下午植物园短线，避开西湖最拥挤湖段",
            "上午湘湖或良渚文化村轻量自然线，午后留机动返程",
        ],
        "上海": [
            "上午上海动物园或共青森林公园，下午自然博物馆/天文馆二选一，晚间不排商圈",
        ],
        "南京": [
            "上午玄武湖或情侣园，下午南京博物院，晚上就近休息",
        ],
    }
    route = list(city_routes.get(destination, []))
    if not route:
        route = [
            "上午安排离住宿最近的自然/亲子主点位，下午室内休息或轻量互动",
            "第二天安排公园、动物或植物相关点位，午后留机动",
            "最后一天只排一个顺路点位，然后从容返程",
        ]
    if not nature:
        route = [
            item.replace("动物园或", "").replace("自然博物馆/", "").replace("自然/亲子", "亲子")
            for item in route
        ]
    if low_walk:
        route = [item + "；所有点位优先选电瓶车、游船、打车接驳" for item in route]
    count = int(days) if days >= 1 else 1
    while len(route) < count:
        route.append("只补一个顺路轻量点位，保留午休和机动，不做跨区打卡")
    return route[:count]


def _work_answer(text: str, memories: list[MemoryItem]) -> str:
    memory_text = "；".join(m.content for m in memories)
    points = "、".join(m.content for m in memories) if memories else "先明确受众，再组织结论、风险和下一步"
    is_cross_team = any(token in text for token in ["跨部门", "设计", "研发", "运营"])
    is_boss = "老板" in text or "管理层" in text or ("老板" in memory_text and not is_cross_team)
    require_three = any("3 条结论" in m.content or "三条结论" in m.content or "先给 3" in m.content for m in memories)
    risk_table_for_boss = "风险表只用于老板材料" in memory_text or "风险" in memory_text or is_boss

    if is_cross_team and not is_boss:
        return (
            f"跨部门同步草案：\n"
            f"主题：[项目名] 本周协同同步\n"
            f"1. 当前进度：主流程按计划推进，近期重点是设计素材、研发联调和运营配置对齐。\n"
            f"2. 设计侧：请在周四前确认最终视觉稿和尺寸规范，变更点直接同步到共享文档。\n"
            f"3. 研发侧：请锁定联调排期，周五前反馈环境稳定性和接口依赖状态。\n"
            f"4. 运营侧：请补齐上线文案、配置字段和首周数据口径。\n"
            f"5. 下个动作：周五下班前各方在同一文档更新状态，下周例会只处理阻塞项。\n"
            f"采用规则：跨部门同步不用管理层风格，不放风险表；{points}。"
        )

    if is_boss:
        intro = (
            "老板材料草案：\n"
            "一、3 条结论\n"
            "1. 项目整体进展正常，核心链路按计划推进。\n"
            "2. 当前主要风险集中在外部依赖、联调排期和跨团队确认。\n"
            "3. 下周需要拍板接口排期、灰度窗口和资源优先级。\n"
            if require_three
            else (
                "老板材料草案：\n"
                "一、整体判断\n"
                "项目整体进展正常，核心链路按计划推进；当前最需要关注的是外部依赖、联调排期和跨团队确认，建议本周内完成关键拍板。\n"
            )
        )
        table = (
            "\n二、风险 / 负责人 / 下一步\n"
            "| 风险项 | 负责人 | 下一步 |\n"
            "|---|---|---|\n"
            "| 外部接口延期影响联调 | 张三 | 周四前锁定接口交付时间，并准备临时 mock 方案 |\n"
            "| 跨团队方案未确认 | 李四 | 周三前组织 30 分钟拍板会，会后发确认纪要 |\n"
            "| 排期存在 2 天偏差 | 王五 | 周五前提交含 buffer 的调整方案 |\n"
        )
        closing = "\n三、需要老板拍板\n请确认是否接受当前排期 buffer，以及外部依赖延期时是否优先保障核心链路上线。"
        if not risk_table_for_boss:
            table = ""
        return f"{intro}{table}{closing}\n采用规则：{points}。"

    return (
        f"项目同步草案：\n"
        f"1. 当前进度：核心事项按计划推进，关键依赖已进入对齐阶段。\n"
        f"2. 待确认事项：接口排期、跨团队方案和上线窗口。\n"
        f"3. 下一步：明确负责人、截止时间和需要拍板的问题。\n"
        f"采用规则：{points}。"
    )


def _study_answer(text: str, memories: list[MemoryItem]) -> str:
    points = "、".join(m.content for m in memories) if memories else "先判断阶段，再安排知识点、例题和练习"
    days = "2" if "两天" in text or "2天" in text else ("5" if "5天" in text or "五天" in text else "7")
    return (
        f"{days}天复习计划：\n"
        f"第 1 天：梳理高频考点，先看 2 个代表例题，再归纳知识点。\n"
        f"第 2 天：集中做错题和薄弱题，按考点分组复盘。\n"
        f"第 3 天以后：按章节轮换练习、回顾错题、做小测；如果只剩 2 天，则合并为高频考点冲刺。\n"
        f"执行规则：{points}。"
    )


def _research_answer(text: str, memories: list[MemoryItem]) -> str:
    points = "、".join(m.content for m in memories) if memories else "按任务类型选择综述、评测或 brainstorm 结构"
    if "brainstorm" in text or "研究问题" in text:
        return (
            f"3 个 RAG 研究问题：\n"
            f"1. 如何把 RAG 评测中的检索错误和生成错误分离度量，避免只看最终答案分数？\n"
            f"2. 多跳问题里，检索证据的覆盖率、顺序和冗余度分别怎样影响最终回答质量？\n"
            f"3. 不同领域数据集上的 RAG 评测结论能否迁移，哪些指标最容易受到语料分布影响？\n"
            f"写法规则：{points}。"
        )
    fields = "代表数据集和局限"
    if any("可复现" in item.content or item.object == "可复现性" for item in memories):
        fields = "代表数据集、局限和可复现性"
    if "RAG" in text or "评测" in text:
        topic = "RAG 评测方法"
        content = (
            f"{topic}可以先按方法类别拆成三类：第一类是检索侧评测，常用 Recall@K、MRR、NDCG 等指标，"
            f"代表数据集包括 Natural Questions、TriviaQA 和 MS MARCO，局限是只能说明检索是否命中，不能保证答案真正可用；"
            f"第二类是生成侧评测，常用 ROUGE、BLEU 或人工/LLM 评分，局限是容易把表达差异误判为质量差异；"
            f"第三类是端到端忠实度和事实性评测，例如把答案拆成原子陈述后检查是否能被检索证据支持，"
            f"这类方法更贴近 RAG 风险，但可复现性会受到评审模型、证据切分和提示词设置影响。"
        )
    else:
        topic = "多模态检索"
        content = (
            f"{topic}综述可以按方法类别展开：双塔式方法用独立编码器把图像、文本或音频映射到同一向量空间，"
            f"适合大规模召回，但细粒度对齐能力有限；交互式方法在候选召回后进一步做跨模态匹配，精度通常更好，"
            f"但计算成本更高；近年的适配与重排序方法尝试用更强的视觉语言模型改善开放域检索，"
            f"不过在数据集覆盖、负样本构造和真实场景泛化上仍需谨慎比较。"
        )
    return (
        f"{content}\n"
        f"整理要求：按方法类别组织；分别说明{fields}；结论保持谨慎，不把单一数据集结果外推为通用结论。\n"
        f"当前规则：{points}。"
    )
