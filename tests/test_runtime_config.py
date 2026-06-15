import os
import tempfile
import unittest
from unittest.mock import patch

import evalharness.server as workbench_server
from evalharness.agent import HarnessAgent
from evalharness.llm import llm_config_from_env
from evalharness.schemas import Message

from assist_everything_betterandbetter_skill.direct_agent import (
    direct_agent_config,
    memory_manage,
    memory_pack,
    memory_write,
    restore_agent_session,
    save_agent_session,
    session_path,
)
from assist_everything_betterandbetter_skill.runtime_config import load_runtime_config, normalize_provider, update_runtime_config


class RuntimeConfigTest(unittest.TestCase):
    def test_minimax_alias_is_canonical_provider_key(self):
        self.assertEqual("minimax", normalize_provider("minimax"))
        self.assertEqual("minimax", normalize_provider("MiniMax"))
        self.assertEqual("minimax", normalize_provider("mimo"))

    def test_minimax_env_takes_precedence_over_legacy_mimo_env(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with patch.dict(
                    os.environ,
                    {
                        "MINIMAX_API_KEY": "minimax-key",
                        "MINIMAX_BASE_URL": "https://api.minimax.example/v1",
                        "MINIMAX_MODEL": "MiniMax-New",
                        "MIMO_API_KEY": "mimo-key",
                        "MIMO_BASE_URL": "https://api.mimo.example/v1",
                        "MIMO_MODEL": "mimo-old",
                    },
                    clear=True,
                ):
                    config = llm_config_from_env("minimax")

                self.assertEqual("minimax-key", config.api_key)
                self.assertEqual("https://api.minimax.example/v1", config.base_url)
                self.assertEqual("MiniMax-New", config.model)
                self.assertEqual("MiniMax", config.label)
            finally:
                os.chdir(cwd)

    def test_shared_runtime_config_is_used_by_direct_agent(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with patch.dict(os.environ, {}, clear=True):
                    update_runtime_config(
                        {
                            "agent": {"provider": "minimax"},
                            "memory": {"backend": "mem0_hosted", "enabled": False, "dir": "memories/shared"},
                            "mem0": {"user_id": "shared-user"},
                            "privacy": {"items": ["secret-marker"]},
                        }
                    )

                    config = direct_agent_config(require_llm=False)

                self.assertEqual("minimax", config.provider)
                self.assertEqual("mem0_hosted", config.memory_backend)
                self.assertFalse(config.memory_enabled)
                self.assertEqual("memories/shared", config.memory_dir)
            finally:
                os.chdir(cwd)

    def test_cli_like_overrides_do_not_rewrite_shared_runtime_config(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with patch.dict(os.environ, {}, clear=True):
                    update_runtime_config({"agent": {"provider": "minimax"}, "memory": {"backend": "local"}})

                    config = direct_agent_config(provider="mimo", memory_backend="mem0_hosted", require_llm=False)
                    saved = load_runtime_config()

                self.assertEqual("minimax", config.provider)
                self.assertEqual("mem0_hosted", config.memory_backend)
                self.assertEqual("minimax", saved["agent"]["provider"])
                self.assertEqual("local", saved["memory"]["backend"])
            finally:
                os.chdir(cwd)

    def test_direct_session_store_restores_context_for_next_process(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                config = direct_agent_config(provider="minimax", memory_dir="memories/test", require_llm=False)
                agent = HarnessAgent(llm_mode="local", persist_memory=False)
                agent.session.messages = [
                    Message(role="user", content="帮我给女朋友选生日礼物"),
                    Message(role="assistant", content="先看预算和偏好。"),
                    Message(role="user", content="预算1000元"),
                ]
                agent._context_start_index = 0

                path = session_path("unit")
                save_agent_session(agent, path, session_id="unit", config=config)

                restored = HarnessAgent(llm_mode="local", persist_memory=False)
                restore_agent_session(restored, path)

                self.assertEqual("unit", restored.session.id)
                self.assertEqual(
                    ["帮我给女朋友选生日礼物", "先看预算和偏好。", "预算1000元"],
                    [message.content for message in restored.session.messages],
                )
                self.assertIn("预算1000元", restored._recent_context())
            finally:
                os.chdir(cwd)

    def test_direct_session_truncation_preserves_reset_boundary(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                config = direct_agent_config(provider="minimax", memory_dir="memories/test", require_llm=False)
                agent = HarnessAgent(llm_mode="local", persist_memory=False)
                agent.session.messages = [Message(role="user" if idx % 2 == 0 else "assistant", content=f"msg-{idx}") for idx in range(30)]
                agent._context_start_index = 20

                path = session_path("boundary")
                save_agent_session(agent, path, session_id="boundary", config=config)

                restored = HarnessAgent(llm_mode="local", persist_memory=False)
                restore_agent_session(restored, path)

                self.assertEqual(24, len(restored.session.messages))
                self.assertEqual(14, restored._context_start_index)
                self.assertEqual("msg-20", restored.session.messages[restored._context_start_index].content)
            finally:
                os.chdir(cwd)

    def test_installed_skill_memory_tools_do_not_require_business_llm(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with patch.dict(os.environ, {}, clear=True):
                    write = memory_write(
                        "预算1000元左右；她喜欢紫色；以前送过玫瑰金项链，送过的不要再送。",
                        context="user: 帮我给女朋友选个生日礼物。",
                        memory_dir="memories/test",
                        memory_backend="local",
                        memory_enabled=True,
                        session_id="host-test",
                    )
                    pack = memory_pack(
                        "再推荐一个生日礼物",
                        context="user: 帮我给女朋友选个生日礼物。",
                        memory_dir="memories/test",
                        memory_backend="local",
                        memory_enabled=True,
                        session_id="host-test",
                    )
                    managed = memory_manage(
                        "展示当前记忆",
                        memory_dir="memories/test",
                        memory_backend="local",
                        memory_enabled=True,
                    )

                self.assertTrue(write["ok"])
                self.assertTrue(write["response"]["memory_actions"])
                self.assertTrue(pack["memory_pack"]["apply_now"])
                self.assertTrue(any("预算" in item["content"] for item in pack["memory_pack"]["apply_now"]))
                self.assertIn("当前 active 记忆", managed["response"]["text"])
            finally:
                os.chdir(cwd)

    def test_workbench_privacy_items_allows_explicit_empty_list(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with patch.dict(os.environ, {}, clear=True):
                    update_runtime_config({"privacy": {"items": []}})
                    self.assertEqual([], workbench_server._privacy_items())
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
