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

    def test_hosted_mem0_backend_uses_local_strategy_and_keeps_local_store_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = AssistSkill(
                memory_dir=tmp,
                persist=True,
                mem0_config=Mem0Config(enabled=True, base_url="https://mem0.example", api_key="k", user_id="u1"),
                memory_backend="mem0_hosted",
            )

            class FakeHosted:
                def __init__(self):
                    self.added_items = []
                    self.added_text = []

                def add_text(self, text, context=""):
                    self.added_text.append({"text": text, "context": context})
                    return {"event_id": "evt_text"}

                def add(self, item):
                    self.added_items.append(item)
                    return {"event_id": "evt_structured"}

                def search(self, query, top_k=8):
                    return []

                def get_all(self, page_size=50):
                    return {"results": []}

                def delete_all(self, page_size=200):
                    return {"mode": "individual", "found_count": 1, "deleted_count": 1, "errors": []}

            fake = FakeHosted()
            skill.mem0_client = fake

            response = skill.process_message(
                "有小孩，玩3天",
                context="user: 帮我规划一个上海旅游行程\nassistant: 你们同行人有老人、小孩或行动不便的情况吗？",
            )

            self.assertEqual([], fake.added_text)
            self.assertEqual(1, len(fake.added_items))
            self.assertEqual("有小孩，玩3天", fake.added_items[0].content)
            self.assertEqual("life_family_travel", fake.added_items[0].scope)
            self.assertEqual("context_fact", fake.added_items[0].type)
            self.assertEqual([], skill.memory.active())
            self.assertEqual("mem0_hosted", response.memory_actions[0]["backend"])
            self.assertEqual("add", response.memory_actions[0]["action"])
            self.assertEqual("remote_structured", response.memory_actions[0]["storage"])

    def test_hosted_mem0_backend_saves_explicit_parent_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = AssistSkill(
                memory_dir=tmp,
                persist=True,
                mem0_config=Mem0Config(enabled=True, base_url="https://mem0.example", api_key="k", user_id="u1"),
                memory_backend="mem0_hosted",
            )

            class FakeHosted:
                def __init__(self):
                    self.added_items = []

                def add(self, item):
                    self.added_items.append(item)
                    return {"event_id": "evt_identity"}

                def search(self, query, top_k=8):
                    return []

                def get_all(self, page_size=50):
                    return {"results": []}

            fake = FakeHosted()
            skill.mem0_client = fake

            response = skill.process_message(
                "我是一个宝妈，记住我的身份",
                context="user: 帮我规划一个端午节去上海旅游的行程\nassistant: 有老人、小孩或需要省力一点吗？",
            )

            self.assertEqual(1, len(fake.added_items))
            self.assertEqual("我是一个宝妈，记住我的身份", fake.added_items[0].content)
            self.assertEqual("life_family_travel", fake.added_items[0].scope)
            self.assertEqual("context_fact", fake.added_items[0].type)
            self.assertEqual("add", response.memory_actions[0]["action"])
            self.assertEqual("mem0_hosted", response.memory_actions[0]["backend"])
            self.assertTrue(response.memory_actions[0]["ok"])

    def test_hosted_mem0_backend_applies_update_strategy_to_remote_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = AssistSkill(
                memory_dir=tmp,
                persist=True,
                mem0_config=Mem0Config(enabled=True, base_url="https://mem0.example", api_key="k", user_id="u1"),
                memory_backend="mem0_hosted",
            )

            class FakeHosted:
                def __init__(self):
                    self.added_items = []
                    self.deleted_ids = []

                def add(self, item):
                    self.added_items.append(item)
                    return {"event_id": "evt_new"}

                def search(self, query, top_k=8):
                    return []

                def get_all(self, page_size=50):
                    return {
                        "results": [
                            {
                                "id": "remote_old",
                                "memory": "动物园，小孩3-4岁",
                                "metadata": {
                                    "assist_memory": {
                                        "id": "mem_old",
                                        "type": "context_fact",
                                        "scope": "life_family_travel",
                                        "content": "动物园，小孩3-4岁",
                                        "status": "active",
                                        "confidence": 0.8,
                                    }
                                },
                            }
                        ]
                    }

                def delete(self, memory_id):
                    self.deleted_ids.append(memory_id)
                    return {"deleted": memory_id}

            fake = FakeHosted()
            skill.mem0_client = fake

            response = skill.process_message("不是动物园，改成室内科技馆，孩子怕热", context="user: 上海亲子游")

            self.assertEqual(["remote_old"], fake.deleted_ids)
            self.assertEqual(1, len(fake.added_items))
            self.assertEqual("不是动物园，改成室内科技馆，孩子怕热", fake.added_items[0].content)
            self.assertEqual([], skill.memory.active())
            actions = response.memory_actions
            self.assertTrue(any(action["action"] == "downgrade" and action["backend"] == "mem0_hosted" for action in actions))
            self.assertTrue(any(action["action"] == "add" and action["backend"] == "mem0_hosted" for action in actions))


if __name__ == "__main__":
    unittest.main()
