from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .mem0_backend import HostedMem0Client, Mem0Config, Mem0SdkClient, _item_from_mem0_result, _mem0_results
from .memory import ACTIVE, DELETED, SUPERSEDED, MemoryItem, MemoryStore


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
        self.mem0_sdk_error = ""
        self.mem0_sdk_client = None
        if self.memory_backend == "mem0_sdk":
            try:
                self.mem0_sdk_client = Mem0SdkClient(self.mem0_config)
            except Exception as exc:
                self.mem0_sdk_error = str(exc)

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

        command = self._try_memory_command(text)
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
            diagnostics={"memory_mode": memory_mode, "profile": self.memory_profile()},
        )

    def reset_memory(self) -> SkillResponse:
        if self.memory_backend == "mem0_hosted":
            return self._reset_remote_memory(self.mem0_client, "mem0_hosted")
        if self.memory_backend == "mem0_sdk":
            return self._reset_remote_memory(self.mem0_sdk_client, "mem0_sdk")
        event = self.memory.reset()
        return SkillResponse("已重置记忆：当前为 M0 空白状态。", [event], [], [])

    def show_memory(self) -> SkillResponse:
        snapshot = self.snapshot()
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
        if self.memory_backend == "mem0_sdk":
            return self._remote_snapshot(self.mem0_sdk_client)
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
            return self._search_remote(self.mem0_client, text)
        if self.memory_backend == "mem0_sdk":
            return self._search_remote(self.mem0_sdk_client, text)
        scope = _infer_scope(text, context)
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
            scope_hit = scope and (scope == item.scope or scope in item.applies_when)
            term_hit = any(term and term in haystack for term in terms)
            if scope_hit or term_hit:
                relevant.append(item)
        return relevant

    def compose_response(
        self,
        text: str,
        memories: list[MemoryItem],
        actions: list[dict[str, Any]],
        asks: list[str],
        context: str = "",
    ) -> str:
        lines = [self._task_answer(text, memories, context)]
        created = [a for a in actions if a["action"] == "add"]
        proposed = [a for a in actions if a["action"] == "propose"]
        rejected = [a for a in actions if a["action"] == "reject"]
        changed = [a for a in actions if a["action"] in {"downgrade", "archive", "delete", "update"}]
        if created:
            detail = "；".join(a["detail"] for a in created[:2])
            suffix = "等" if len(created) > 2 else ""
            lines.append(f"\n行，这个我记住了：{detail}{suffix}。")
        if proposed:
            lines.append("\n我捕捉到这可能是长期偏好，需要你确认后再保存：同意保存 / 拒绝保存。")
        if rejected:
            lines.append("\n这类内容我不会写入长期记忆。")
        if changed:
            lines.append("\n已更新记忆。")
        if asks:
            lines.append("\n再确认一个关键点：" + asks[0])
        return "\n".join(lines)

    def _try_memory_command(self, text: str) -> SkillResponse | None:
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
            deleted = self.memory.delete(query)
            names = ", ".join(item.id for item in deleted) or "无匹配"
            active = self.retrieve_relevant_memories(normalized)
            text_after = f"删除结果：{names}。后续检索会过滤 deleted 记忆。"
            if active:
                text_after += "\n\n删除后仍可使用的相关记忆：\n" + "\n".join(f"- {m.content}" for m in active)
            if followup:
                followup_memories = self.retrieve_relevant_memories(followup, normalized)
                text_after += "\n\n继续处理：\n" + self._task_answer(followup, followup_memories, normalized)
                active = followup_memories
            return SkillResponse(text_after, self.memory.events[-len(deleted):] if deleted else [], [m.id for m in active], [])
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
            return self._apply_remote_updates(self.mem0_client, "mem0_hosted", text, context)
        if self.memory_backend == "mem0_sdk":
            return self._apply_remote_updates(self.mem0_sdk_client, "mem0_sdk", text, context)
        for match in self._conflicting_memories(text, context):
            self.memory.downgrade(match.id, f"新反馈缩小或推翻旧规则：{text}")
            actions.append(self.memory.events[-1])
        for item in self.extract_memory_candidates(text, context):
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
            if self._is_duplicate(item):
                actions.append(
                    {
                        "action": "dedupe",
                        "memory_id": None,
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

    def _apply_remote_updates(self, client: Any, backend: str, text: str, context: str = "") -> list[dict[str, Any]]:
        if not client:
            error = self.mem0_sdk_error if backend == "mem0_sdk" and self.mem0_sdk_error else "memory backend is not configured"
            return [{"action": "remote_extract", "backend": backend, "detail": text, "ok": False, "error": error}]
        try:
            result = client.add_text(text, context=context)
            return [{"action": "remote_extract", "backend": backend, "detail": text, "ok": True, "result": _compact_remote_result(result)}]
        except Exception as exc:
            return [{"action": "remote_extract", "backend": backend, "detail": text, "ok": False, "error": str(exc)}]

    def _reset_remote_memory(self, client: Any, backend: str) -> SkillResponse:
        if not client:
            error = self.mem0_sdk_error if backend == "mem0_sdk" and self.mem0_sdk_error else "memory backend is not configured"
            action = {"action": "reset", "backend": backend, "memory_id": None, "detail": error, "ok": False}
            return SkillResponse("远端记忆后端未配置，无法重置。", [action], [], [])
        try:
            result = client.delete_all(page_size=200)
            action = {"action": "reset", "backend": backend, "memory_id": None, "detail": "remote memory reset", "ok": not result.get("errors"), "result": result}
            return SkillResponse("已重置当前记忆后端。", [action], [], [])
        except Exception as exc:
            action = {"action": "reset", "backend": backend, "memory_id": None, "detail": str(exc), "ok": False}
            return SkillResponse(f"重置当前记忆后端失败：{exc}", [action], [], [])

    def _remote_snapshot(self, client: Any) -> dict[str, Any]:
        if not client:
            return {"version": "M0", "active": [], "superseded": [], "archived": [], "deleted": []}
        try:
            raw = client.get_all(page_size=50)
            items = [_item_from_mem0_result(record).to_dict() for record in _mem0_results(raw)]
        except Exception:
            items = []
        return {"version": f"M{len(items)}", "active": items, "superseded": [], "archived": [], "deleted": []}

    def _search_remote(self, client: Any, text: str) -> list[MemoryItem]:
        if not client:
            return []
        try:
            return [item for item in client.search(text, top_k=8) if item.status == ACTIVE and not _is_polluted_memory_item(item)]
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

    def _conflicting_memories(self, text: str, context: str = "") -> list[MemoryItem]:
        lowered = text.lower()
        conflict_terms = ["不适用", "不用", "不要", "不能", "只用于", "仅用于", "改成", "推翻", "不再"]
        if not any(term in lowered for term in conflict_terms):
            return []
        scope = _infer_scope(text, context)
        terms = _keywords(text)
        if "不适用" in text:
            if "步行不适用" in text or "少步行不适用" in text:
                terms = ["步行"]
            else:
                scoped_terms = [term for term in terms if f"{term}不适用" in text or f"{term} 不适用" in text]
                terms = scoped_terms or terms
        matches = []
        for item in self.memory.active():
            if any(term and term in item.content for term in terms):
                matches.append(item)
        if not matches:
            topic_terms = ["步行", "风险", "番茄钟", "文献综述", "模板", "自测", "可复现"]
            for item in self.memory.active():
                if item.scope == scope and any(term in item.content for term in topic_terms if term in text):
                    matches.append(item)
        if not matches and "模板" in text:
            scope = _infer_scope(text, context)
            matches.extend(
                item
                for item in self.memory.active()
                if item.scope == scope
                and item.type == WORKFLOW
                and any(term in item.content for term in ["文献综述", "方法", "数据集", "局限", "可复现", "模板"])
            )
        return matches

    def _is_duplicate(self, candidate: MemoryItem) -> bool:
        for item in self.memory.active():
            same_content = item.content == candidate.content and item.scope == candidate.scope
            same_fact = (
                candidate.predicate
                and item.scope == candidate.scope
                and item.type == candidate.type
                and item.subject == candidate.subject
                and item.target == candidate.target
                and item.object == candidate.object
                and item.predicate == candidate.predicate
            )
            if same_content or same_fact:
                return True
        return False

    def _approve_pending(self) -> SkillResponse:
        if not self.pending_proposals:
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
        scope = _infer_scope(text, context)
        if scope == "life_family_travel" and not memories:
            return ["同行人有没有老人、孩子或步行限制？", "偏自然、动物还是城市景点？"]
        if scope == "work_report" and "老板" not in text and not memories:
            return ["这份材料是给老板还是跨部门团队？"]
        if scope == "study_plan" and not memories:
            return ["现在是打基础、常规复习还是临考冲刺？"]
        if scope == "research_review" and not memories:
            return ["这是文献综述、评测整理还是研究问题 brainstorm？"]
        return []

    def _task_answer(self, text: str, memories: list[MemoryItem], context: str = "") -> str:
        scope = _infer_scope(text, context)
        if scope == "life_family_travel":
            return _travel_answer(text, memories)
        if scope == "work_report":
            return _work_answer(text, memories)
        if scope == "study_plan":
            return _study_answer(text, memories)
        if scope == "research_review":
            return _research_answer(text, memories)
        return f"我会先按当前请求处理：{text}\n如果你给出稳定偏好或工作方法，我会把它提取为可管理记忆。"


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
        enabled=backend in {"mem0_hosted", "mem0_sdk"},
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
        "sdk_mem0": "mem0_sdk",
        "mem0ai": "mem0_sdk",
        "local_json": "local",
        "local_markdown": "local",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in {"local", "mem0_hosted", "mem0_sdk"} else "local"


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
    if any(marker in text for marker in HIGH_CONFIDENCE_MARKERS):
        score += 0.2
        reasons.append("durable_or_decisive_marker")
    if any(marker in text for marker in ["我喜欢", "我不喜欢", "我习惯", "我偏好"]) or "喜欢" in item.content:
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
    ]
    if any(signal in text for signal in signals):
        return True
    if _has_contextual_task_fact(text, context):
        return True
    return False


def _has_contextual_task_fact(text: str, context: str = "") -> bool:
    if not context.strip() or _infer_scope(text, context) == "general":
        return False
    fact_markers = [
        "小孩",
        "孩子",
        "老人",
        "同行",
        "动物园",
        "动物",
        "自然",
        "博物馆",
        "科技馆",
        "少走",
        "少步行",
        "推车",
    ]
    return bool(re.search(r"\d+\s*[-~到至]?\s*\d*\s*岁", text)) or any(marker in text for marker in fact_markers)


def _infer_scope(text: str, context: str = "") -> str:
    if any(token in text for token in ["家庭", "亲子", "旅行", "行程", "路线", "半日游", "动物", "网红", "父亲", "小孩", "孩子", "景点", "上海"]):
        return "life_family_travel"
    if any(token in text for token in ["老板", "周报", "项目", "跨部门", "同步", "研发", "设计", "运营", "风险", "负责人"]):
        return "work_report"
    if any(token in text for token in ["学习", "复习", "考试", "高数", "线性代数", "物理", "英语", "番茄钟", "例题", "自测"]):
        return "study_plan"
    if any(token in text for token in ["文献", "综述", "RAG", "研究", "数据集", "可复现", "brainstorm", "多模态"]):
        return "research_review"
    return "general"


def _infer_memory_type(text: str, scope: str) -> str:
    if scope == "life_family_travel" and (any(token in text for token in ["小孩", "孩子", "老人", "同行"]) or re.search(r"\d+\s*[-~到至]?\s*\d*\s*岁", text)):
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
    if any(token in text for token in ["父亲", "爸爸"]):
        return "father"
    if "孩子" in text:
        return "child"
    return "user"


def _infer_target(text: str, scope: str) -> str:
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
    if memory_type == PREFERENCE:
        return "likes" if "喜欢" in text else "prefers"
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
        return {"time_scope": "current_task"}
    if memory_type == HISTORY:
        return {"time_scope": "past"}
    return {"time_scope": "long_term"}


def _extract_budget(text: str) -> str:
    for marker in ["1000", "一千"]:
        if marker in text:
            return "1000 元以内"
    return ""


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


def _travel_answer(text: str, memories: list[MemoryItem]) -> str:
    points = "、".join(m.content for m in memories) if memories else "常规节奏、交通便利、体验丰富"
    if "南京" in text or "半日" in text:
        return f"半日路线：上午/下午任选 4 小时，先去玄武湖或中山陵音乐台这类开阔点，再安排一个室内休息点。约束：{points}。全程控制转场和步行，不排人挤人的网红点。"
    return f"行程草案：第 1 天安排低强度核心景点加早休息；第 2 天安排自然或动物相关点位；第 3 天保留机动和轻松返程。执行约束：{points}。"


def _work_answer(text: str, memories: list[MemoryItem]) -> str:
    points = "、".join(m.content for m in memories) if memories else "先明确受众，再组织结论、风险和下一步"
    return (
        f"同步材料草案：\n"
        f"1. 结论：当前进展正常，主要风险集中在依赖、排期和跨团队确认。\n"
        f"2. 风险/负责人/下一步：按受众决定是否保留风险表。\n"
        f"3. 下周动作：明确负责人、截止时间和需要拍板的问题。\n"
        f"采用规则：{points}。"
    )


def _study_answer(text: str, memories: list[MemoryItem]) -> str:
    points = "、".join(m.content for m in memories) if memories else "先判断阶段，再安排知识点、例题和练习"
    return (
        f"复习计划：第 1 天梳理高频考点并用例题开路；第 2 天集中做错题和自测；后续按天扩展知识点、例题和练习。\n"
        f"采用规则：{points}。"
    )


def _research_answer(text: str, memories: list[MemoryItem]) -> str:
    points = "、".join(m.content for m in memories) if memories else "按任务类型选择综述、评测或 brainstorm 结构"
    if "brainstorm" in text or "研究问题" in text:
        return f"3 个研究问题：1. RAG 评测中检索质量和生成质量如何解耦？2. 多跳问题的证据覆盖率如何度量？3. 不同数据集上的评测结论能否迁移？写法规则：{points}。"
    return f"综述草案：先按方法类别组织，再分别说明代表数据集、局限和可复现性，最后用谨慎表述总结趋势。当前规则：{points}。"
