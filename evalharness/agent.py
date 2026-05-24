from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from assist_everything_betterandbetter_skill.cases import EvalCase

from .llm import MimoClient, mimo_configured
from .schemas import HarnessSession, Message, ToolCall, TurnTrace
from .tools import MemoryToolbox


class HarnessAgent:
    """Conversation harness that lets an agent use the skill through tools."""

    def __init__(
        self,
        name: str = "assist-agent",
        toolbox: MemoryToolbox | None = None,
        llm_mode: str = "auto",
        llm_client: MimoClient | None = None,
    ) -> None:
        self.name = name
        self.toolbox = toolbox or MemoryToolbox()
        self.session = HarnessSession()
        self.llm_mode = llm_mode
        self.llm_client = llm_client

    def reply(self, user_text: str, *, stage: str = "chat", case: EvalCase | None = None) -> TurnTrace:
        tool_calls: list[ToolCall] = []
        notes: list[str] = []

        if stage == "reset":
            response, call = self.toolbox.reset_memory()
            tool_calls.append(call)
        elif stage == "round1_task" and case:
            response, call = self.toolbox.first_task(case)
            tool_calls.append(call)
            notes.append("空白状态下只完成普通任务，不写长期记忆。")
        elif stage == "feedback" and case:
            response, call = self.toolbox.learn_feedback(case)
            tool_calls.append(call)
            notes.append("明确反馈经授权后进入长期记忆。")
        elif stage == "show_memory":
            response, call = self.toolbox.show_memory()
            tool_calls.append(call)
        elif stage == "round2_task" and case:
            response, call = self.toolbox.second_task(case)
            tool_calls.append(call)
            notes.append("相似新任务主动检索 active 记忆。")
        elif stage == "preference_change" and case:
            response, call = self.toolbox.update_preferences(case)
            tool_calls.append(call)
            notes.append("偏好变化触发旧记忆降权/条件化和新规则写入。")
        elif stage == "round3_task" and case:
            response, call = self.toolbox.third_task(case)
            tool_calls.append(call)
        elif stage == "delete_retest" and case:
            response, call = self.toolbox.delete_and_retest(case)
            tool_calls.append(call)
            notes.append("删除后复测必须过滤 deleted 记忆。")
        else:
            response, call = self.toolbox.manage_memory(user_text)
            tool_calls.append(call)
            if "未识别到记忆管理命令" in response.text:
                active = self.toolbox.skill.memory.active()
                applied = [item.id for item in active]
                memory_hint = "；".join(item.content for item in active[:3]) or "暂无可用长期记忆"
                response.text = f"我按普通任务处理：{user_text}\n当前可参考记忆：{memory_hint}。"
                response.applied_memories = applied
        response.text = self._maybe_llm_rewrite(user_text, stage, response.text, response.applied_memories)

        user = Message(role="user", content=user_text)
        assistant = Message(role="assistant", content=response.text)
        self.session.messages.extend([user, assistant])
        turn = TurnTrace(
            id=f"turn_{len(self.session.turns) + 1:03d}",
            stage=stage,
            user=user,
            assistant=assistant,
            tool_calls=tool_calls,
            applied_memories=response.applied_memories,
            memory_snapshot=self.toolbox.snapshot(),
            notes=notes,
        )
        self.session.turns.append(turn)
        return turn

    def _maybe_llm_rewrite(self, user_text: str, stage: str, draft: str, applied_memories: list[str]) -> str:
        if not self._use_mimo():
            return draft
        client = self.llm_client or MimoClient()
        snapshot = self.toolbox.snapshot()
        messages = [
            {
                "role": "system",
                "content": (
                    "你是安装了 assist-everything-betterandbetter-skill 的 agent。"
                    "必须尊重已执行的 memory tool 结果，不要虚构记忆。"
                    "用中文简洁回答，必要时说明已应用/未应用哪些记忆。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "stage": stage,
                        "user_message": user_text,
                        "tool_draft": draft,
                        "applied_memory_ids": applied_memories,
                        "memory_snapshot": snapshot,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        return client.chat(messages, temperature=0.3).strip()

    def _use_mimo(self) -> bool:
        if self.llm_mode == "mimo":
            return True
        if self.llm_mode == "local":
            return False
        return mimo_configured()


class ExternalCommandAgent:
    """Optional adapter: run a real LLM/agent command with trace JSON over stdin."""

    def __init__(self, command: str | None = None) -> None:
        self.command = command or os.getenv("EVALHARNESS_AGENT_CMD", "")
        if not self.command:
            raise ValueError("EVALHARNESS_AGENT_CMD is not configured")

    def reply(self, payload: dict[str, Any]) -> dict[str, Any]:
        completed = subprocess.run(
            self.command,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            shell=True,
            check=True,
            capture_output=True,
        )
        return json.loads(completed.stdout)
