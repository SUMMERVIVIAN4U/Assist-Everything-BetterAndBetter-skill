import tempfile
import unittest

from assist_everything_betterandbetter_skill.skill import AssistSkill
from assist_everything_betterandbetter_skill.mem0_backend import Mem0Config


class MemoryAdvantagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.skill = AssistSkill(memory_dir=self.tmp.name, persist=True)
        self.skill.reset_memory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_high_confidence_memory_builds_profile_and_layers(self):
        response = self.skill.process_message("我特别喜欢以后先看结论，再看评分标准。")
        self.assertTrue(any(action["action"] == "add" for action in response.memory_actions))

        profile = self.skill.memory_profile()
        self.assertIn("conclusion_first", profile["interaction_style"])
        self.assertIn("rubric_or_risk_first", profile["interaction_style"])

        compact = self.skill.compact_snapshot()
        self.assertEqual(compact["active_count"], 1)
        self.assertIn("compression", compact)

        layers = self.skill.memory_layers()
        self.assertEqual([layer["id"] for layer in layers["layers"]], ["L0", "L1", "L2"])
        self.assertIn("retention_reason", layers["layers"][2]["items"][0])

    def test_instant_mode_skips_memory_retrieval(self):
        self.skill.process_message("以后写老板材料先给 3 条结论。")
        response = self.skill.process_message("[q] 你好")
        self.assertEqual(response.applied_memories, [])
        self.assertEqual(response.diagnostics["memory_mode"]["mode"], "instant")

    def test_medium_confidence_becomes_pending_proposal(self):
        response = self.skill.process_message("可能以后报告短一点？")
        self.assertTrue(any(action["action"] == "propose" for action in response.memory_actions))
        self.assertEqual(self.skill.memory.active(), [])

        approved = self.skill.process_message("同意保存")
        self.assertTrue(any(action["action"] == "add" for action in approved.memory_actions))
        self.assertEqual(len(self.skill.memory.active()), 1)

    def test_private_memory_is_rejected_and_redacted(self):
        response = self.skill.process_message("我的密码是 123456，请记住。")
        self.assertTrue(any(action["action"] == "reject" for action in response.memory_actions))
        self.assertEqual(self.skill.memory.active(), [])

        privacy = self.skill.privacy_report()
        self.assertIn("delete", privacy["controls"])
        self.assertEqual(privacy["sensitive_storage"], "private_or_sensitive observations are redacted and not saved as memory")

    def test_mem0_backend_syncs_added_memory_without_external_call(self):
        skill = AssistSkill(
            memory_dir=self.tmp.name,
            persist=True,
            mem0_config=Mem0Config(enabled=True, base_url="https://mem0.example", api_key="test-key", user_id="u1"),
        )

        class FakeMem0:
            def __init__(self):
                self.added = []

            def add(self, item):
                self.added.append(item.content)
                return {"event_id": "evt_1", "status": "queued"}

            def search(self, query, top_k=8):
                return []

        fake = FakeMem0()
        skill.mem0_client = fake
        response = skill.process_message("我特别喜欢以后先看结论，再看评分标准。")

        add_action = next(action for action in response.memory_actions if action["action"] == "add")
        self.assertEqual(fake.added, ["我特别喜欢先看结论，再看评分标准"])
        self.assertEqual(add_action["remote"]["backend"], "mem0")
        self.assertTrue(add_action["remote"]["ok"])

    def test_mem0_backend_syncs_context_fact_from_ongoing_chat(self):
        skill = AssistSkill(
            memory_dir=self.tmp.name,
            persist=True,
            mem0_config=Mem0Config(enabled=True, base_url="https://mem0.example", api_key="test-key", user_id="u1"),
        )

        class FakeMem0:
            def __init__(self):
                self.added = []

            def add(self, item):
                self.added.append(item.content)
                return {"event_id": "evt_travel", "status": "queued"}

            def search(self, query, top_k=8):
                return []

        fake = FakeMem0()
        skill.mem0_client = fake
        context = "user: 我想要带小孩去上海玩，帮我推荐一些景点"

        response = skill.process_message("动物园，小孩3-4岁", context=context)

        add_actions = [action for action in response.memory_actions if action["action"] == "add"]
        self.assertEqual(1, len(add_actions))
        add_action = add_actions[0]
        self.assertIn("小孩3-4岁", add_action["detail"])
        self.assertEqual(fake.added, [add_action["detail"]])
        self.assertEqual(add_action["remote"]["backend"], "mem0")
        self.assertTrue(add_action["remote"]["ok"])

    def test_proactively_extracts_implicit_travel_preference_from_feedback(self):
        context = "user: 我想带小孩在上海玩两天\nassistant: 可以安排动物园和户外公园。"

        response = self.skill.process_message("优先室内，少走路，孩子怕热", context=context)

        add_actions = [action for action in response.memory_actions if action["action"] == "add"]
        self.assertEqual(1, len(add_actions))
        self.assertIn("优先室内，少走路，孩子怕热", add_actions[0]["detail"])
        active = self.skill.memory.active()
        self.assertEqual(1, len(active))
        self.assertEqual("life_family_travel", active[0].scope)

    def test_proactively_corrects_prior_memory_from_negative_feedback(self):
        self.skill.process_message(
            "动物园，小孩3-4岁",
            context="user: 我想要带小孩去上海玩，帮我推荐一些景点",
        )

        response = self.skill.process_message("不是动物园，改成室内科技馆，孩子怕热")

        self.assertTrue(any(action["action"] == "downgrade" for action in response.memory_actions))
        add_actions = [action for action in response.memory_actions if action["action"] == "add"]
        self.assertEqual(1, len(add_actions))
        self.assertIn("室内科技馆", add_actions[0]["detail"])
        active_contents = [item.content for item in self.skill.memory.active()]
        self.assertEqual(["不是动物园，改成室内科技馆，孩子怕热"], active_contents)

    def test_memory_disabled_skips_local_and_remote_memory(self):
        skill = AssistSkill(
            memory_dir=self.tmp.name,
            persist=True,
            mem0_config=Mem0Config(enabled=True, base_url="https://mem0.example", api_key="test-key", user_id="u1"),
            memory_enabled=False,
        )

        class FakeMem0:
            def add(self, item):
                raise AssertionError("memory disabled should not sync additions")

            def search(self, query, top_k=8):
                raise AssertionError("memory disabled should not search remote memory")

        skill.mem0_client = FakeMem0()
        response = skill.process_message("以后请记住：我喜欢先看结论。")

        self.assertEqual(response.memory_actions, [])
        self.assertEqual(response.applied_memories, [])
        self.assertEqual(skill.memory.active(), [])
        self.assertEqual(response.diagnostics["memory_mode"]["mode"], "disabled")

    def test_ad_hoc_chat_does_not_use_hardcoded_gift_fixture(self):
        response = self.skill.process_message("帮我给女朋友选个礼物。")

        self.assertNotIn("小众香氛礼盒", response.text)
        self.assertFalse(any("候选方案" in action.get("detail", "") for action in response.memory_actions))
        self.assertEqual(self.skill.memory.active(), [])


if __name__ == "__main__":
    unittest.main()
