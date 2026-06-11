import tempfile
import unittest
from unittest.mock import patch

from assist_everything_betterandbetter_skill.mem0_backend import HostedMem0Client, Mem0Config, Mem0SdkClient
from assist_everything_betterandbetter_skill.skill import AssistSkill


class MemoryAdapterTest(unittest.TestCase):
    def test_hosted_mem0_add_text_uses_remote_extraction_payload(self):
        captured = {}

        class FakeHosted(HostedMem0Client):
            def _request_first(self, method, paths, payload):
                captured["method"] = method
                captured["paths"] = paths
                captured["payload"] = payload
                return {"event_id": "evt_1"}

        client = FakeHosted(Mem0Config(enabled=True, base_url="https://mem0.example", api_key="k", user_id="u1"))

        client.add_text("优先室内，少走路，孩子怕热", context="上海亲子游")

        self.assertEqual("POST", captured["method"])
        self.assertEqual(["/v1/memories/", "/v3/memories/add/"], captured["paths"])
        self.assertTrue(captured["payload"]["infer"])
        self.assertEqual("u1", captured["payload"]["user_id"])
        self.assertEqual("优先室内，少走路，孩子怕热", captured["payload"]["messages"][0]["content"])

    def test_mem0_sdk_client_uses_python_sdk_and_user_scoped_reset(self):
        class FakeMemory:
            def __init__(self):
                self.add_calls = []
                self.delete_all_calls = []

            def add(self, messages, user_id=None, **kwargs):
                self.add_calls.append({"messages": messages, "user_id": user_id, "kwargs": kwargs})
                return {"results": [{"id": "sdk_1", "memory": "用户优先室内"}]}

            def delete_all(self, user_id=None, **kwargs):
                self.delete_all_calls.append({"user_id": user_id, "kwargs": kwargs})
                return {"deleted": True}

        fake = FakeMemory()
        client = Mem0SdkClient(Mem0Config(enabled=True, user_id="u1"), memory=fake)

        client.add_text("优先室内")
        result = client.delete_all()

        self.assertEqual("u1", fake.add_calls[0]["user_id"])
        self.assertEqual("优先室内", fake.add_calls[0]["messages"][0]["content"])
        self.assertEqual([{"user_id": "u1", "kwargs": {}}], fake.delete_all_calls)
        self.assertEqual({"deleted": True}, result["result"])

    def test_mem0_sdk_client_defaults_to_minimax_and_fastembed_when_mimo_is_configured(self):
        captured = {}

        class FakeMemoryFactory:
            @classmethod
            def from_config(cls, config):
                captured["config"] = config
                return "fake-memory"

        env = {
            "MIMO_API_KEY": "mimo-key",
            "MIMO_BASE_URL": "https://api.minimaxi.com/v1",
            "MIMO_MODEL": "MiniMax-M2.7",
        }
        with patch.dict("os.environ", env, clear=False):
            with patch.dict("sys.modules", {"mem0": type("FakeMem0Module", (), {"Memory": FakeMemoryFactory})}):
                client = Mem0SdkClient(Mem0Config(enabled=True, user_id="u1"))

        self.assertEqual("fake-memory", client.memory)
        self.assertEqual("minimax", captured["config"]["llm"]["provider"])
        self.assertEqual("mimo-key", captured["config"]["llm"]["config"]["api_key"])
        self.assertEqual("https://api.minimaxi.com/v1", captured["config"]["llm"]["config"]["minimax_base_url"])
        self.assertEqual("fastembed", captured["config"]["embedder"]["provider"])
        self.assertEqual(1024, captured["config"]["vector_store"]["config"]["embedding_model_dims"])
        self.assertIn("mem0_sdk_qdrant", captured["config"]["vector_store"]["config"]["path"])

    def test_hosted_mem0_backend_is_mutually_exclusive_with_local_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = AssistSkill(
                memory_dir=tmp,
                persist=True,
                mem0_config=Mem0Config(enabled=True, base_url="https://mem0.example", api_key="k", user_id="u1"),
                memory_backend="mem0_hosted",
            )

            class FakeHosted:
                def __init__(self):
                    self.added = []

                def add_text(self, text, context=""):
                    self.added.append({"text": text, "context": context})
                    return {"event_id": "evt_hosted"}

                def search(self, query, top_k=8):
                    return []

                def get_all(self, page_size=50):
                    return {"results": [{"id": "remote_1", "memory": "优先室内，少走路，孩子怕热"}]}

                def delete_all(self, page_size=200):
                    return {"mode": "individual", "found_count": 1, "deleted_count": 1, "errors": []}

            fake = FakeHosted()
            skill.mem0_client = fake

            response = skill.process_message("优先室内，少走路，孩子怕热", context="user: 上海亲子游")

            self.assertEqual([{"text": "优先室内，少走路，孩子怕热", "context": "user: 上海亲子游"}], fake.added)
            self.assertEqual([], skill.memory.active())
            self.assertEqual("mem0_hosted", response.memory_actions[0]["backend"])
            self.assertEqual("remote_extract", response.memory_actions[0]["action"])


if __name__ == "__main__":
    unittest.main()
