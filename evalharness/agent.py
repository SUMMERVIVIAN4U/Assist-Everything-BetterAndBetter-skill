from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from .llm import MimoClient, mimo_configured
from .schemas import HarnessSession, Message, TurnTrace
from .tools import MemoryToolbox


class HarnessAgent:
    """Conversation harness that lets an agent use the generic skill runtime."""

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

    def reply(self, user_text: str, *, stage: str = "chat") -> TurnTrace:
        response, call = self.toolbox.process_message(user_text)
        response.text = self._maybe_llm_rewrite(user_text, stage, response.text, response.applied_memories)

        user = Message(role="user", content=user_text)
        assistant = Message(role="assistant", content=response.text)
        self.session.messages.extend([user, assistant])
        turn = TurnTrace(
            id=f"turn_{len(self.session.turns) + 1:03d}",
            stage=stage,
            user=user,
            assistant=assistant,
            tool_calls=[call],
            applied_memories=response.applied_memories,
            memory_snapshot=self.toolbox.snapshot(),
            notes=_notes_from_response(response.to_dict()),
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
                    "记忆工具已经完成提取、更新、删除和检索。"
                    "必须尊重 tool_draft 和 memory_snapshot，不要虚构或使用 deleted/superseded 记忆。"
                    "用中文回复，段落清楚，必要时说明已应用/更新的记忆。"
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


def _notes_from_response(response: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    actions = response.get("memory_actions", [])
    if any(action.get("action") == "add" for action in actions):
        notes.append("本轮从自然语言中提取并保存了通用记忆。")
    if any(action.get("action") in {"downgrade", "archive", "delete"} for action in actions):
        notes.append("本轮处理了记忆更新、淘汰或删除。")
    if response.get("applied_memories"):
        notes.append("本轮检索并应用了 active 记忆。")
    return notes
