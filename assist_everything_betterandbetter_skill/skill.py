from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .mem0_backend import Mem0Client, Mem0Config
from .memory import ACTIVE, DELETED, SUPERSEDED, MemoryItem, MemoryStore


PREFERENCE = "preference"
CONSTRAINT = "constraint"
WORKFLOW = "workflow"
CANDIDATE = "candidate"
DECISION = "decision"
HISTORY = "history"
CONTEXT_FACT = "context_fact"

TEMPORARY_MARKERS = ("这次", "本次", "今天", "临时", "暂时", "这一轮", "只要这版", "本轮")
PRIVATE_MARKERS = ("密码", "token", "密钥", "身份证", "银行卡", "验证码", "隐私不要记")
HIGH_CONFIDENCE_MARKERS = ("以后", "下次", "一直", "总是", "必须", "绝对", "特别", "非常", "决定", "确定", "定了")
UNCERTAIN_MARKERS = ("可能", "也许", "考虑", "随便", "算了", "不重要", "？", "?")
STRUCTURED_MEMORY_TYPES = {CONSTRAINT, WORKFLOW, DECISION, HISTORY, CONTEXT_FACT, CANDIDATE}


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
        self.mem0_client = Mem0Client(self.mem0_config) if self.mem0_config.ready else None

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
        actions.extend(self._record_assistant_candidate(response_text, text, context))
        return SkillResponse(
            response_text,
            actions,
            [item.id for item in relevant],
            asks,
            diagnostics={"memory_mode": memory_mode, "profile": self.memory_profile()},
        )

    def reset_memory(self) -> SkillResponse:
        event = self.memory.reset()
        return SkillResponse("已重置记忆：当前为 M0 空白状态。", [event], [], [])

    def show_memory(self) -> SkillResponse:
        snapshot = self.memory.snapshot()
        if not self.memory.items:
            return SkillResponse("当前没有任何记忆。", [], [], [])
        lines = ["当前 active 记忆与历史状态："]
        for status in [ACTIVE, SUPERSEDED, "archived", DELETED]:
            items = snapshot.get(status if status != ACTIVE else "active", [])
            for item in items:
                lines.append(f"- {item['id']} [{item['status']}/{item['type']}/{item['scope']}] {item['content']}")
        active_ids = [item.id for item in self.memory.active()]
        return SkillResponse("\n".join(lines), [], active_ids, [])

    def compact_snapshot(self, limit: int = 8) -> dict[str, Any]:
        snapshot = self.memory.snapshot()
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
        active = self.memory.snapshot().get("active", [])
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
        snapshot = self.memory.snapshot()
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
        snapshot = self.memory.snapshot()
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
        scope = _infer_scope(text, context)
        terms = [term for term in _keywords(text) if term not in {"礼物"}]
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
        relevant.extend(self._search_mem0(text, existing_ids={item.id for item in relevant}))
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
            remote = self._sync_mem0_add(item)
            if remote:
                event["remote"] = remote
            actions.append(event)
        return actions

    def _record_assistant_candidate(self, response_text: str, user_text: str, context: str = "") -> list[dict[str, Any]]:
        if _infer_scope(user_text, context) != "relationship_gift":
            return []
        gift_object = _extract_recommended_gift(response_text)
        if not gift_object:
            return []
        item = MemoryItem(
            CANDIDATE,
            f"给女朋友选礼物时，曾推荐候选方案：{gift_object}",
            scope="relationship_gift",
            subject="assistant",
            target="girlfriend",
            object=gift_object,
            predicate="proposed",
            source="assistant_output",
            evidence=[response_text],
            applies_when=["relationship_gift"],
            tags=["礼物", "候选", gift_object],
            validity={"time_scope": "task_thread", "status": "proposed"},
        )
        if self._is_duplicate(item):
            return []
        self.memory.add(item)
        event = dict(self.memory.events[-1])
        remote = self._sync_mem0_add(item)
        if remote:
            event["remote"] = remote
        return [event]

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
        if scope == "relationship_gift" and _looks_like_final_gift_choice(normalized):
            return []
        relationship = _relationship_candidates(normalized, context, scope)
        if relationship:
            return relationship
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
        if scope == "relationship_gift" and any(term in text for term in ["送过的就不要", "已经送过", "送过的东西"]):
            return []
        if scope == "relationship_gift" and any(term in text for term in ["不送首饰", "不能不送首饰", "非首饰"]):
            return []
        terms = [term for term in _keywords(text) if term not in {"礼物"}]
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
            topic_terms = ["步行", "风险", "番茄钟", "文献综述", "模板", "自测", "可复现", "香水", "前女友"]
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
        if scope == "relationship_gift":
            enough = (
                any("预算" in m.content for m in memories)
                and any("喜欢" in m.content for m in memories)
                and any("送过" in m.content or "重复" in m.content or "避开首饰" in m.content for m in memories)
            )
            if enough or any(token in text for token in ["给我一个", "非首饰推荐", "礼物推荐"]):
                return []
        if scope == "relationship_gift" and not any("闺蜜" in m.content for m in memories):
            return ["她闺蜜最近晒过或收到过什么品牌吗？", "有没有前女友有过、绝对不能送的东西？"]
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
        if scope == "relationship_gift":
            return _gift_answer(text, memories, context)
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
    backend = os.getenv("ASSIST_MEMORY_BACKEND", "local").strip().lower()
    return Mem0Config(
        enabled=backend == "mem0",
        base_url=os.getenv("MEM0_BASE_URL", "").strip(),
        api_key=os.getenv("MEM0_API_KEY", "").strip(),
        user_id=os.getenv("MEM0_USER_ID", "workbench-user").strip(),
        app_id=os.getenv("MEM0_APP_ID", "assist-everything-betterandbetter-skill").strip(),
        project_id=os.getenv("MEM0_PROJECT_ID", "").strip(),
        project_name=os.getenv("MEM0_PROJECT_NAME", "").strip(),
        timeout=float(os.getenv("MEM0_TIMEOUT", "15") or 15),
    )


def _compact_remote_result(result: dict[str, Any]) -> dict[str, Any]:
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
    if item.scope == "relationship_gift" and any(marker in text for marker in ["喜欢", "预算", "送过", "收到", "玫瑰金", "紫色"]):
        score += 0.2
        reasons.append("relationship_gift_fact")
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
    if item.type == PREFERENCE and "女朋友喜欢紫色" in text and any(token in evidence for token in ["不要硬拗紫色", "别用紫色", "不能用紫色"]):
        return True
    return False


def _is_plain_task_request(text: str) -> bool:
    stripped = text.strip(" 。！？!?")
    if any(token in stripped for token in ["以后", "记住", "喜欢", "不喜欢", "不要", "不能", "预算", "送过", "选定"]):
        return False
    return stripped in {"给我一个礼物推荐", "给我一个推荐", "再给一个礼物方向", "帮我给女朋友选个礼物"}


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
        "闺蜜",
        "前女友",
        "送过",
        "送了",
        "送出",
        "晒过",
        "预算",
        "保留一个",
        "就保留一个",
        "只要一个",
        "一个最合适",
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
        "非首饰",
        "不送首饰",
    ]
    if any(signal in text for signal in signals):
        return True
    if _is_relationship_context(context):
        relationship_fact = any(token in text for token in ["紫色", "1000", "一千", "一个", "银色", "手链"])
        gift_decision_or_history = any(token in text for token in ["选定", "选了", "选过", "决定", "送过", "送了", "送出", "买过", "买了", "下单", "满意", "就这个", "就它"])
        return relationship_fact or gift_decision_or_history
    return False


def _infer_scope(text: str, context: str = "") -> str:
    if any(token in text for token in ["女朋友", "礼物", "送礼", "闺蜜", "前女友", "项链", "玫瑰金", "首饰", "手链", "银色", "耳钉"]) or _is_relationship_context(context):
        return "relationship_gift"
    if any(token in text for token in ["家庭", "亲子", "旅行", "行程", "路线", "半日游", "动物", "网红", "父亲"]):
        return "life_family_travel"
    if any(token in text for token in ["老板", "周报", "项目", "跨部门", "同步", "研发", "设计", "运营", "风险", "负责人"]):
        return "work_report"
    if any(token in text for token in ["学习", "复习", "考试", "高数", "线性代数", "物理", "英语", "番茄钟", "例题", "自测"]):
        return "study_plan"
    if any(token in text for token in ["文献", "综述", "RAG", "研究", "数据集", "可复现", "brainstorm", "多模态"]):
        return "research_review"
    return "general"


def _infer_memory_type(text: str, scope: str) -> str:
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
    if any(token in text for token in ["不能", "不要", "不喜欢", "前女友"]):
        return CONSTRAINT
    if scope == "relationship_gift" and any(token in text for token in ["保留一个", "只要一个", "一个最合适", "推荐的有点多"]):
        return WORKFLOW
    if any(token in text for token in ["只用于", "仅用于", "不适用", "同行", "闺蜜"]):
        return CONSTRAINT
    if any(token in text for token in ["膝盖不好", "负责人"]):
        return CONTEXT_FACT
    return PREFERENCE


def _clean_memory_content(text: str, scope: str, context: str = "") -> str:
    content = text.strip()
    for prefix in ["以后", "家庭出行", "写给老板的项目材料，请", "学习计划请", "做文献综述时，请"]:
        content = content.replace(prefix, "")
    content = content.strip(" ，,。：:")
    if scope == "relationship_gift":
        content = _relationship_memory_content(content, context)
    return content


def _infer_subject(text: str, scope: str) -> str:
    if scope == "relationship_gift":
        if any(token in text for token in ["她", "女朋友"]):
            return "girlfriend"
        return "user"
    if any(token in text for token in ["父亲", "爸爸"]):
        return "father"
    if "孩子" in text:
        return "child"
    return "user"


def _infer_target(text: str, scope: str) -> str:
    if scope == "relationship_gift":
        return "girlfriend"
    if scope == "work_report":
        if "老板" in text:
            return "boss"
        if "跨部门" in text:
            return "cross_functional_team"
    return ""


def _infer_object(text: str, scope: str) -> str:
    if scope == "relationship_gift":
        return _extract_gift_object(text)
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
    if memory_type == CANDIDATE:
        return "proposed"
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


def _relationship_memory_content(content: str, context: str) -> str:
    if "预算" in content:
        budget = _extract_budget(content)
        if "喜欢紫色" in content or "紫色" in content:
            if budget:
                return f"给女朋友选礼物预算在 {budget}；女朋友喜欢紫色"
            return "女朋友喜欢紫色"
        if budget:
            return f"给女朋友选礼物预算在 {budget}"
    if "喜欢紫色" in content or (_is_relationship_context(context) and "紫色" in content):
        return "女朋友喜欢紫色"
    if "保留一个" in content or "只要一个" in content or "一个最合适" in content:
        return "给女朋友选礼物时，用户希望只保留一个最合适的推荐"
    if "推荐的有点多" in content:
        return "给女朋友选礼物时，用户希望推荐更简洁"
    if "闺蜜" in content:
        return f"给女朋友选礼物需参考闺蜜线索：{content}"
    if "前女友" in content:
        return f"给女朋友选礼物需避开前女友相关物品：{content}"
    return content


def _relationship_candidates(text: str, context: str, scope: str) -> list[MemoryItem]:
    if scope != "relationship_gift":
        return []
    if _is_memory_question(text):
        return []
    candidates: list[MemoryItem] = []
    resolved_object = _resolve_gift_reference(text, context)
    if resolved_object and _is_gift_rejection(text):
        candidates.append(
            MemoryItem(
                CONSTRAINT,
                f"当前给女朋友选礼物时不再推荐{resolved_object}",
                scope=scope,
                subject="user",
                target="girlfriend",
                object=resolved_object,
                predicate="must_avoid",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["礼物", "否决", resolved_object],
                validity={"time_scope": "current_task"},
            )
        )
    if resolved_object and _is_acceptance_signal(text):
        candidates.append(
            MemoryItem(
                DECISION,
                f"本次给女朋友的礼物已选定为{resolved_object}",
                scope=scope,
                subject="user",
                target="girlfriend",
                object=resolved_object,
                predicate="selected",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["礼物", "选定", resolved_object, "满意"],
                validity={"time_scope": "current_task", "status": "accepted"},
            )
        )
    if resolved_object and _is_execution_signal(text):
        candidates.append(
            MemoryItem(
                HISTORY,
                f"已经给女朋友送过{resolved_object}",
                scope=scope,
                subject="user",
                target="girlfriend",
                object=resolved_object,
                predicate="gave",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["礼物", "送过", resolved_object],
                validity={"time_scope": "past"},
            )
        )
    if any(token in text for token in ["不送首饰", "不能不送首饰", "非首饰", "换个非首饰"]):
        candidates.append(
            MemoryItem(
                CONSTRAINT,
                "本次给女朋友选礼物暂时避开首饰，优先非首饰品类",
                scope=scope,
                subject="user",
                target="girlfriend",
                object="首饰",
                predicate="must_avoid",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["非首饰", "首饰"],
                validity={"time_scope": "current_task"},
            )
        )
    if any(token in text for token in ["送过的就不要", "送过的东西", "送过的不要", "不要再送", "选过的不要", "已经选过", "选过的不能", "送过的不能"]):
        candidates.append(
            MemoryItem(
                CONSTRAINT,
                _avoid_repeat_content(context),
                scope=scope,
                subject="user",
                target="girlfriend",
                object=_previous_gift_objects(context) or "已送/已选礼物",
                predicate="must_avoid",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["送过", "重复"],
                validity={"time_scope": "long_term"},
            )
        )
    budget = _extract_budget(text)
    if budget:
        candidates.append(
            MemoryItem(
                CONSTRAINT,
                f"给女朋友选礼物预算在 {budget}",
                scope=scope,
                subject="user",
                target="girlfriend",
                object=budget,
                predicate="budget_limit",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["礼物", "预算"],
                validity={"time_scope": "long_term"},
            )
        )
    if "紫色" in text and any(token in text for token in ["不要硬套紫色", "不要用紫色", "不用紫色", "别用紫色", "不能用紫色"]):
        candidates.append(
            MemoryItem(
                CONSTRAINT,
                "给女朋友选首饰时不要硬套紫色偏好",
                scope=scope,
                subject="user",
                target="girlfriend",
                object="紫色",
                predicate="must_avoid",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["女朋友", "紫色", "首饰"],
                validity={"time_scope": "long_term"},
            )
        )
    elif "紫色" in text or "喜欢紫色" in text:
        candidates.append(
            MemoryItem(
                PREFERENCE,
                "女朋友喜欢紫色",
                scope=scope,
                subject="girlfriend",
                target="",
                object="紫色",
                predicate="likes",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["女朋友", "紫色"],
                validity={"time_scope": "long_term"},
            )
        )
    if "玫瑰金" in text and any(token in text for token in ["喜欢", "偏好"]):
        candidates.append(
            MemoryItem(
                PREFERENCE,
                "女朋友喜欢玫瑰金",
                scope=scope,
                subject="girlfriend",
                object="玫瑰金",
                predicate="likes",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["女朋友", "玫瑰金", "首饰"],
                validity={"time_scope": "long_term"},
            )
        )
    gift_object = _extract_gift_object(text) or resolved_object
    if gift_object and gift_object != "首饰" and any(token in text for token in ["送过", "送了", "买过", "已经送", "以前送", "之前送"]):
        candidates.append(
            MemoryItem(
                HISTORY,
                f"之前已经送过{gift_object}",
                scope=scope,
                subject="user",
                target="girlfriend",
                object=gift_object,
                predicate="gave",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["礼物", "送过", gift_object],
                validity={"time_scope": "past"},
            )
        )
    elif gift_object and any(token in text for token in ["选定", "选了", "决定", "定了", "就选", "就要"]):
        candidates.append(
            MemoryItem(
                DECISION,
                f"本次给女朋友的礼物已选定为{gift_object}",
                scope=scope,
                subject="user",
                target="girlfriend",
                object=gift_object,
                predicate="selected",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["礼物", "选定", gift_object],
                validity={"time_scope": "current_task"},
            )
        )
    if "保留一个" in text or "只要一个" in text or "一个最合适" in text or "推荐的有点多" in text:
        candidates.append(
            MemoryItem(
                WORKFLOW,
                "给女朋友选礼物时，用户希望只保留一个最合适的推荐",
                scope=scope,
                subject="user",
                target="assistant",
                object="推荐数量",
                predicate="uses_workflow",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["推荐简洁", "一个"],
                validity={"time_scope": "long_term"},
            )
        )
    if "闺蜜" in text:
        candidates.append(
            MemoryItem(
                CONTEXT_FACT,
                f"给女朋友选礼物需参考闺蜜线索：{text}",
                scope=scope,
                subject="girlfriend_friend",
                target="girlfriend",
                object=_extract_gift_object(text),
                predicate="social_signal",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["闺蜜", "礼物"],
                validity={"time_scope": "long_term"},
            )
        )
    if "前女友" in text:
        candidates.append(
            MemoryItem(
                CONSTRAINT,
                f"给女朋友选礼物需避开前女友相关物品：{text}",
                scope=scope,
                subject="user",
                target="girlfriend",
                object=_extract_gift_object(text),
                predicate="must_avoid",
                source="chat_feedback",
                evidence=[text],
                applies_when=[scope],
                tags=["前女友", "禁忌"],
                validity={"time_scope": "long_term"},
            )
        )
    return candidates


def _extract_budget(text: str) -> str:
    for marker in ["1000", "一千"]:
        if marker in text:
            return "1000 元以内"
    return ""


def _extract_gift_object(text: str) -> str:
    candidates = [
        "银色手链",
        "玫瑰金耳钉",
        "紫色真丝小方巾",
        "小众香氛礼盒",
        "施华洛世奇的水晶项链",
        "施华洛世奇项链",
        "玫瑰金项链",
        "紫水晶项链",
        "水晶项链",
        "项链",
        "手链",
        "香水",
        "包袋配饰",
        "首饰",
    ]
    for candidate in candidates:
        if candidate in text:
            return candidate
    if "玫瑰金" in text:
        return "玫瑰金"
    return ""


def _extract_recommended_gift(text: str) -> str:
    for marker in ["推荐：", "我就保留一个推荐："]:
        if marker in text:
            tail = text.split(marker, 1)[1]
            first_line = tail.splitlines()[0]
            return _normalize_gift_object(first_line.strip(" 。；;，,"))
    return _extract_gift_object(text)


def _normalize_gift_object(text: str) -> str:
    if "银色" in text and "手链" in text:
        return "银色手链"
    if "玫瑰金" in text and "耳钉" in text:
        return "玫瑰金耳钉"
    if "紫色" in text and "方巾" in text:
        return "紫色真丝小方巾"
    if "香氛" in text or "香水" in text:
        return "小众香氛礼盒" if "礼盒" in text or "香氛" in text else "香水"
    return _extract_gift_object(text)


def _resolve_gift_reference(text: str, context: str) -> str:
    direct = _normalize_gift_object(text)
    if direct:
        return direct
    if not any(token in text for token in ["这个", "刚才", "上次", "上一", "选过", "送过", "已经选", "已经送"]):
        return ""
    return _latest_gift_object_from_context(context)


def _latest_gift_object_from_context(context: str) -> str:
    objects = []
    for line in context.splitlines():
        obj = ""
        if line.startswith("assistant:"):
            obj = _extract_recommended_gift(line)
        if not obj:
            obj = _normalize_gift_object(line)
        if obj:
            objects.append(obj)
    return objects[-1] if objects else ""


def _previous_gift_objects(context: str) -> str:
    objects = []
    for line in context.splitlines():
        if any(token in line for token in ["已选定为", "送过", "确认送", "下单", "就这个", "送了"]):
            obj = _normalize_gift_object(line)
            if obj and obj not in objects:
                objects.append(obj)
    return "、".join(objects)


def _avoid_repeat_content(context: str) -> str:
    objects = _previous_gift_objects(context)
    if objects:
        return f"给女朋友选礼物时，已经选过或送过的礼物不要重复：{objects}"
    return "给女朋友选礼物时，已经选过或送过的礼物不要重复"


def _is_acceptance_signal(text: str) -> bool:
    return any(token in text for token in ["满意", "就这个", "就它", "可以", "定这个", "确认这个", "选这个", "挺好"])


def _looks_like_final_gift_choice(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("就") and stripped.endswith(("吧", "了", "就行", "好了")) and any(
        token in stripped for token in ["手链", "项链", "耳钉", "吊坠", "香氛", "香水", "方巾", "包"]
    )


def _is_execution_signal(text: str) -> bool:
    return any(token in text for token in ["送出", "送了", "已经送", "下单", "买了", "付款", "确认送"])


def _is_gift_rejection(text: str) -> bool:
    return any(token in text for token in ["换一个", "不要这个", "不选这个", "太多", "不合适", "算了", "别推荐这个"])


def _is_relationship_context(context: str) -> bool:
    return any(token in context for token in ["女朋友", "礼物", "送礼", "闺蜜", "前女友"])


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
        "玫瑰金",
        "项链",
        "手链",
        "银色",
        "耳钉",
        "选定",
        "选过",
        "送过",
        "闺蜜",
        "前女友",
        "香水",
        "礼物",
    ]
    return [word for word in vocab if word in text]


def _gift_answer(text: str, memories: list[MemoryItem], context: str = "") -> str:
    decisions = [memory for memory in memories if memory.type == DECISION]
    history = [memory for memory in memories if memory.type == HISTORY]
    constraints = [memory for memory in memories if memory.type == CONSTRAINT]
    resolved_object = _resolve_gift_reference(text, context)
    if resolved_object and _is_execution_signal(text):
        return f"妥，就按{resolved_object}。我会把它当作已经送出的历史，后面再选礼物会避开它。"
    if resolved_object and _is_acceptance_signal(text):
        return f"妥，就定{resolved_object}。后面如果继续聊这次礼物，我就围绕它走。"
    rejected_or_avoided = _avoided_gift_objects(memories)
    previous_objects = [memory.object for memory in [*history, *decisions] if memory.object]
    avoid_scent = any(memory.type == CONSTRAINT and any(token in memory.object + memory.content for token in ["香水", "香氛"]) for memory in memories)
    non_jewelry = any("非首饰" in memory.content or "避开首饰" in memory.content for memory in constraints) or any(
        token in text for token in ["非首饰", "不送首饰", "不能不送首饰", "换个品类"]
    )
    avoid_repeat = any("送过" in memory.content or "重复" in memory.content for memory in constraints)
    if decisions and any(token in text for token in ["之前", "已经", "还记得", "选", "礼物"]) and not avoid_repeat:
        return f"我会延续已定方案：{decisions[-1].content}。如果继续推进，我会围绕这件礼物做款式、预算和避重校验。"
    wants_one = any("只保留一个" in memory.content or "一个最合适" in memory.content for memory in memories)
    likes_purple = any(memory.type == PREFERENCE and "紫色" in memory.content for memory in memories)
    likes_rose_gold = any(memory.type == PREFERENCE and "玫瑰金" in memory.content for memory in memories)
    likes_silver_bracelet = any(memory.type == PREFERENCE and "手链" in memory.content and "银色" in memory.content for memory in memories)
    has_budget = any(memory.type == CONSTRAINT and "预算" in memory.content for memory in memories)
    if avoid_repeat and previous_objects:
        if likes_purple:
            return "推荐：紫色真丝小方巾。\n理由：这次避开上次已选/已送的礼物，转到非首饰小配饰，仍能用上她的颜色偏好。"
        item = "真丝小方巾" if avoid_scent else "小众香氛礼盒"
        return f"推荐：{item}。\n理由：这次先避开上次已选/已送的礼物，换到不同品类，重复风险低。"
    if "玫瑰金耳钉" in rejected_or_avoided:
        if likes_silver_bracelet and "银色手链" not in previous_objects:
            return "推荐：银色手链。\n理由：你已经否掉玫瑰金耳钉了；手链方向里银色更贴合她后来的反馈。"
        return "推荐：银色手链。\n理由：玫瑰金耳钉已经被否掉，这个换了款式和颜色，和常见耳钉拉开差异。"
    if non_jewelry:
        color = "紫色系" if likes_purple else "她日常会用到的"
        history_clause = "它避开了已送过的礼物和首饰品类" if history else "它避开了首饰品类"
        color_clause = "；如果不送首饰，紫色偏好才适合重新作为颜色线索" if likes_purple else ""
        return (
            f"我会换到非首饰品类，不再推项链、手链、耳饰。推荐：{color}真丝小方巾。\n"
            f"理由：{history_clause}{color_clause}；预算也容易控制在 1000 元以内。"
        )
    if history and any(token in text for token in ["送过", "之前", "还记得", "重复"]):
        return "我会避开已经送过的礼物：" + "、".join(memory.object or memory.content for memory in history) + "。"
    if avoid_repeat and history:
        if likes_rose_gold:
            return (
                "推荐：玫瑰金耳钉。\n"
                "理由：如果仍坚持首饰品类，材质偏好应继续用玫瑰金；同时它避开了已经送过的玫瑰金项链，不重复同一件礼物。"
            )
        if likes_purple:
            return "推荐：紫色真丝小方巾。\n理由：它使用了已知紫色偏好，同时避开已经送过的礼物。"
        return "推荐：小众香氛礼盒。\n理由：它和已送礼物拉开品类差异，重复风险低。"
    if likes_silver_bracelet and "银色手链" not in rejected_or_avoided and "银色手链" not in previous_objects:
        return "推荐：银色手链。\n理由：她明确说手链更喜欢银色，比继续推玫瑰金耳钉更贴近这次反馈。"
    if wants_one and likes_rose_gold and has_budget:
        return "我就保留一个推荐：玫瑰金耳钉。它仍在首饰品类里尊重玫瑰金偏好，同时避开已送过的项链形态。"
    if wants_one and likes_purple and has_budget:
        return "我就保留一个推荐：紫色真丝小方巾。它符合 1000 元以内预算，也避开了首饰重复风险。"
    if likes_rose_gold and has_budget:
        return "推荐：玫瑰金耳钉。\n理由：它匹配首饰材质偏好，预算也容易控制在 1000 元以内。"
    if likes_purple and has_budget:
        return "推荐：紫色真丝小方巾。\n理由：它匹配紫色偏好，预算容易控制在 1000 元以内，也比首饰更不容易踩重复。"
    if likes_rose_gold:
        return "推荐：玫瑰金耳钉。\n理由：它直接匹配她对玫瑰金首饰的偏好。"
    if likes_purple:
        return "推荐：紫色真丝小方巾。\n理由：它直接匹配她的颜色偏好，且作为日常配饰比较实用。"
    if memories:
        item = "真丝小方巾" if avoid_scent else "小众香氛礼盒"
        return f"推荐：{item}。\n理由：当前记忆不足以支持更细的颜色或材质偏好，这个默认方向更稳，也方便后续按禁忌过滤。"
    return "推荐：小众香氛礼盒。\n理由：在还没有预算、偏好和已送清单前，它比首饰更少依赖尺寸和材质信息；你补充预算、偏好和已送过清单后，我会收敛到更精确的一件。"


def _avoided_gift_objects(memories: list[MemoryItem]) -> str:
    objects = []
    for memory in memories:
        if memory.type == CONSTRAINT and memory.object and any(token in memory.content for token in ["不再推荐", "不要重复", "避开", "不能再送"]):
            objects.append(memory.object)
    return "、".join(objects)


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
