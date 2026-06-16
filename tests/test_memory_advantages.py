import tempfile
import unittest

from assist_everything_betterandbetter_skill.memory import MemoryItem
from assist_everything_betterandbetter_skill.skill import DECISION, WORKFLOW, AssistSkill
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

    def test_cross_team_scope_update_keeps_boss_material_rule(self):
        self.skill.process_message("以后写给老板的项目材料，请先给 3 条结论，再用表格列风险、负责人和下一步。")

        response = self.skill.process_message("跨部门同步不要那么管理层风格，风险表只用于老板材料。")

        self.assertFalse(any(action["action"] == "downgrade" for action in response.memory_actions))
        active_contents = [item.content for item in self.skill.memory.active()]
        self.assertTrue(any("3 条结论" in content for content in active_contents))
        self.assertTrue(any("风险表只用于老板材料" in content for content in active_contents))

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

    def test_local_backend_does_not_sync_added_memory_to_mem0(self):
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
        self.assertEqual(fake.added, [])
        self.assertNotIn("remote", add_action)

    def test_local_backend_extracts_context_fact_without_remote_sync(self):
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
        self.assertEqual(fake.added, [])
        self.assertNotIn("remote", add_action)

    def test_mem0_hosted_write_failure_is_not_reported_as_saved(self):
        skill = AssistSkill(
            memory_dir=self.tmp.name,
            persist=True,
            mem0_config=Mem0Config(enabled=True, base_url="https://mem0.example", api_key="test-key", user_id="u1"),
            memory_backend="mem0_hosted",
        )

        class FailingMem0:
            def get_all(self, page_size=50):
                return {"results": []}

            def add(self, item):
                raise RuntimeError("remote write failed")

        skill.mem0_client = FailingMem0()
        response = skill.process_message("以后家庭出行请记住：父亲膝盖不好，步行要少。")

        self.assertTrue(any(action["action"] == "add" and action.get("ok") is False for action in response.memory_actions))
        self.assertIn("记忆写入没有成功", response.text)
        self.assertNotIn("已记下，后续会按这次上下文使用", response.text)

    def test_mem0_hosted_snapshot_failure_is_not_reported_as_empty_memory(self):
        skill = AssistSkill(
            memory_dir=self.tmp.name,
            persist=True,
            mem0_config=Mem0Config(enabled=True, base_url="https://mem0.example", api_key="test-key", user_id="u1"),
            memory_backend="mem0_hosted",
        )

        class FailingMem0:
            def get_all(self, page_size=50):
                raise RuntimeError("remote read failed")

        skill.mem0_client = FailingMem0()
        response = skill.show_memory()

        self.assertIn("当前记忆读取失败", response.text)
        self.assertNotIn("当前没有任何记忆", response.text)

    def test_proactively_extracts_implicit_travel_preference_from_feedback(self):
        context = "user: 我想带小孩在上海玩两天\nassistant: 可以安排动物园和户外公园。"

        response = self.skill.process_message("优先室内，少走路，孩子怕热", context=context)

        add_actions = [action for action in response.memory_actions if action["action"] == "add"]
        self.assertEqual(1, len(add_actions))
        self.assertIn("优先室内，少走路，孩子怕热", add_actions[0]["detail"])
        active = self.skill.memory.active()
        self.assertEqual(1, len(active))
        self.assertEqual("life_family_travel", active[0].scope)

    def test_travel_memory_update_replans_previous_task_from_context(self):
        context = (
            "user: 帮我安排北京周末 2 天亲子旅行。\n"
            "assistant: 北京2天亲子行程：\n"
            "第 1 天：上午国家植物园或奥森北园，下午中国科技馆/自然类展馆，晚上早回酒店\n"
            "第 2 天：上午北京海洋馆，午后找近距离室内休息点，傍晚不加长距离步行"
        )

        response = self.skill.process_message(
            "以后家庭出行请记住：父亲膝盖不好，步行要少；孩子喜欢自然和动物；我不喜欢人挤人的网红点。",
            context=context,
        )

        self.assertIn("北京2天亲子行程", response.text)
        self.assertIn("第 2 天", response.text)
        self.assertIn("执行约束", response.text)
        self.assertIn("电瓶车", response.text)
        self.assertNotIn("目的地1天亲子行程", response.text)

        father_memory = next(item for item in self.skill.memory.active() if "步行限制" in item.content)
        self.assertEqual("scene_memory", father_memory.validity.get("time_scope"))
        self.assertTrue(father_memory.validity.get("needs_confirmation"))

    def test_scene_memory_prompts_confirmation_instead_of_direct_application(self):
        self.skill.process_message(
            "以后家庭出行请记住：父亲膝盖不好，步行要少；孩子喜欢自然和动物；我不喜欢人挤人的网红点。"
        )

        response = self.skill.process_message("帮我安排杭州 3 天家庭行程。")

        self.assertIn("之前有过父亲步行限制", response.asks[0])
        applied_contents = [item.content for item in self.skill.retrieve_relevant_memories("帮我安排杭州 3 天家庭行程。")]
        self.assertNotIn("家庭旅行曾出现父亲步行限制，下次需确认父亲是否同行及步行限制是否适用", applied_contents)
        self.assertNotIn("所有点位优先选电瓶车", response.text)

    def test_plain_task_does_not_emit_generic_followup_asks(self):
        response = self.skill.process_message("帮我安排北京周末 2 天亲子旅行。")

        self.assertIn("北京2天亲子行程", response.text)
        self.assertEqual([], response.asks)

    def test_initial_gift_request_delivers_recommendation(self):
        response = self.skill.process_message("帮我给女朋友选个生日礼物。")

        self.assertIn("推荐方向", response.text)
        self.assertIn("预算", response.text)

    def test_semantic_extractor_records_short_gift_selection(self):
        def extractor(text, context, scope, active):
            self.assertEqual("选拍立得", text)
            self.assertEqual("gift_planning", scope)
            return [
                MemoryItem(
                    DECISION,
                    "本次给女朋友的礼物已选定为拍立得",
                    scope="gift_planning",
                    target="女朋友",
                    predicate="selected",
                    source="llm_semantic_extractor",
                    confidence=0.92,
                    evidence=[text],
                    applies_when=["gift_planning"],
                    validity={"time_scope": "current_task"},
                )
            ]

        skill = AssistSkill(memory_dir=self.tmp.name, persist=False, semantic_extractor=extractor)
        response = skill.process_message(
            "选拍立得",
            context="user: 帮我给女朋友选个生日礼物。\nassistant: 推荐方向：富士 Instax mini 12 拍立得相机。",
        )

        self.assertTrue(any(action["action"] == "add" for action in response.memory_actions))
        self.assertIn("本次给女朋友的礼物已选定为拍立得", [item.content for item in skill.memory.active()])
        self.assertEqual("llm_semantic_extractor", response.memory_actions[0].get("extractor"))

    def test_rule_extraction_fast_path_does_not_call_semantic_extractor(self):
        def extractor(text, context, scope, active):
            raise AssertionError("semantic extractor should not run for rule-extractable budget")

        skill = AssistSkill(memory_dir=self.tmp.name, persist=False, semantic_extractor=extractor)
        response = skill.process_message("预算1000元左右", context="user: 帮我给女朋友选个生日礼物。")

        self.assertTrue(any("预算在 1000 元左右" in action.get("detail", "") for action in response.memory_actions))
        self.assertFalse(any(item.type == DECISION and "预算" in item.content for item in skill.memory.active()))

    def test_semantic_budget_candidate_cannot_be_saved_as_selected_decision(self):
        def extractor(text, context, scope, active):
            return [
                MemoryItem(
                    DECISION,
                    "本次给女朋友的礼物已选定为预算 1000 元",
                    scope="gift_planning",
                    target="女朋友",
                    predicate="selected",
                    source="llm_semantic_extractor",
                    confidence=0.95,
                    evidence=[text],
                    applies_when=["gift_planning"],
                    validity={"time_scope": "current_task"},
                )
            ]

        skill = AssistSkill(memory_dir=self.tmp.name, persist=False, semantic_extractor=extractor)
        response = skill.process_message(
            "预算 1000 元",
            context="user: 帮我给女朋友选个生日礼物。\nassistant: 推荐小众香氛礼盒，并询问预算。",
        )

        self.assertFalse(any("已选定为预算" in action.get("detail", "") for action in response.memory_actions))
        self.assertFalse(any(item.type == DECISION and "预算" in item.content for item in skill.memory.active()))

    def test_future_instruction_is_recorded_as_workflow_experience(self):
        response = self.skill.process_message(
            "以后要在用户说已经选中了之后，不要继续推荐其他选项",
            context="user: 帮我给女朋友选个礼物。\nassistant: 推荐了多个礼物方向。",
        )

        active = self.skill.memory.active()
        self.assertTrue(any(item.type == WORKFLOW and "不要继续推荐其他选项" in item.content for item in active))
        self.assertTrue(any(action["action"] == "add" and "不要继续推荐其他选项" in action["detail"] for action in response.memory_actions))

    def test_initial_study_request_delivers_plan(self):
        response = self.skill.process_message("帮我做一个 7 天英语复习计划。")

        self.assertIn("7天复习计划", response.text)
        self.assertIn("第 1 天", response.text)
        self.assertIn("执行规则", response.text)

    def test_approve_memory_reports_existing_auto_saved_memories(self):
        self.skill.process_message("预算1000元左右；她喜欢紫色；以前送过玫瑰金项链，送过的不要再送。", context="user: 帮我给女朋友选个生日礼物。")

        response = self.skill.process_message("同意保存。")

        self.assertIn("active 记忆已保存", response.text)
        self.assertNotIn("没有待授权的记忆候选", response.text)

    def test_travel_current_task_constraints_are_atomized_and_explained(self):
        self.skill.process_message(
            "以后家庭出行请记住：父亲膝盖不好，步行要少；孩子喜欢自然和动物；我不喜欢人挤人的网红点。"
        )
        context = (
            "user: 帮我安排杭州3天亲子旅行。\n"
            "assistant: 杭州3天亲子行程：第 1 天西溪湿地，第 2 天杭州动物园，第 3 天湘湖。"
        )

        response = self.skill.process_message("这次父亲不去，只有我和孩子，少步行不适用，但还是避开网红点。", context=context)

        self.assertIn("杭州3天亲子行程", response.text)
        self.assertIn("本次任务已应用并暂存", response.text)
        self.assertIn("这次父亲不去，只有我和孩子", response.text)
        self.assertIn("本次少步行限制不适用", response.text)
        self.assertNotIn("这可能是长期偏好", response.text)
        self.assertNotIn("已记下，后续会按这次上下文使用", response.text)

        active = self.skill.memory.active()
        current_task_contents = [item.content for item in active if item.validity.get("time_scope") == "current_task"]
        self.assertIn("这次父亲不去，只有我和孩子", current_task_contents)
        self.assertIn("本次少步行限制不适用", current_task_contents)
        self.assertTrue(any(item.validity.get("time_scope") == "scene_memory" for item in active if "步行限制" in item.content))
        self.assertFalse(self.skill.memory.snapshot()["superseded"])

        followup = self.skill.process_message("帮我安排上海 1 天亲子自然路线。")
        self.assertEqual([], followup.asks)
        self.assertFalse(any(action["action"] == "propose" for action in followup.memory_actions))
        self.assertFalse(any("上海 1 天亲子自然路线" in action.get("detail", "") for action in followup.memory_actions))

    def test_current_task_memory_does_not_cross_skill_session(self):
        self.skill.process_message("这次父亲不去，只有我和孩子，少步行不适用。", context="user: 帮我安排杭州3天亲子旅行。")

        same_session = self.skill.retrieve_relevant_memories("继续安排杭州亲子行程。")
        self.assertTrue(any("父亲不去" in item.content for item in same_session))

        new_session = AssistSkill(memory_dir=self.tmp.name, persist=True)
        next_session = new_session.retrieve_relevant_memories("继续安排杭州亲子行程。")
        self.assertFalse(any("父亲不去" in item.content for item in next_session))

    def test_travel_current_task_context_fact_is_saved_and_visible(self):
        self.skill.process_message(
            "以后家庭出行请记住：父亲膝盖不好，步行要少；孩子喜欢自然和动物；我不喜欢人挤人的网红点。"
        )

        response = self.skill.process_message(
            "这次父亲不去，只有我和孩子",
            context="user: 帮我安排杭州3天亲子旅行。\nassistant: 杭州3天亲子行程。",
        )
        shown = self.skill.show_memory()

        self.assertTrue(any(action["action"] == "add" and "这次父亲不去，只有我和孩子" in action["detail"] for action in response.memory_actions))
        self.assertIn("这次父亲不去，只有我和孩子", shown.text)
        current_task = [item for item in self.skill.memory.active() if item.content == "这次父亲不去，只有我和孩子"]
        self.assertEqual(1, len(current_task))
        self.assertEqual("current_task", current_task[0].validity.get("time_scope"))

    def test_expired_gift_budget_current_task_is_confirm_first_not_apply_now(self):
        self.skill.process_message("预算1000元左右", context="user: 帮我给女朋友选个生日礼物。")

        new_session = AssistSkill(memory_dir=self.tmp.name, persist=True)
        applied = new_session.retrieve_relevant_memories("帮我给女朋友选个生日礼物。")
        pack = new_session.relevant_memory_pack("帮我给女朋友选个生日礼物。", applied)

        self.assertFalse(any(item.predicate == "budget_limit" for item in applied))
        self.assertTrue(any("预算在 1000 元左右" in item["content"] for item in pack["confirm_first"]))
        budget_item = next(item for item in pack["confirm_first"] if "预算在 1000 元左右" in item["content"])
        self.assertTrue(budget_item["needs_confirmation"])
        self.assertEqual("expired_current_task_confirm_first", budget_item["reason"])

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

    def test_gift_planning_extracts_budget_profile_and_previous_gifts(self):
        response = self.skill.process_message(
            "帮我给我老公选个生日礼物，他是个程序员，爱好在阳台上养花（蝴蝶兰、鹿角蕨……）养金鱼。"
            "预算在 千元左右。以前送过始祖鸟的双肩背包，始祖鸟的防晒服，他还比较满意"
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertIn("给老公选礼物预算在 1000 元左右", add_details)
        self.assertIn("以前送过老公始祖鸟的双肩背包，始祖鸟的防晒服", add_details)
        self.assertTrue(any("老公的礼物偏好/背景" in detail and "程序员" in detail for detail in add_details))

        active = self.skill.memory.active()
        self.assertTrue(all(item.scope == "gift_planning" for item in active))
        self.assertIn("budget_limit", [item.predicate for item in active])
        self.assertIn("previously_given", [item.predicate for item in active])

    def test_gift_planning_extracts_embedded_budget_with_context(self):
        response = self.skill.process_message(
            "预算1000元左右；她喜欢紫色",
            context="user: 帮我给女朋友选个生日礼物。",
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertIn("给女朋友选礼物预算在 1000 元左右", add_details)
        self.assertTrue(any("女朋友的礼物颜色偏好" in detail and "喜欢紫色" in detail for detail in add_details))

    def test_gift_jewelry_facts_infer_gift_scope_without_literal_gift_word(self):
        response = self.skill.process_message(
            "预算1000元左右；她喜欢紫色；如果是首饰，她喜欢玫瑰金；以前送过玫瑰金项链，送过的不要再送。"
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertIn("礼物预算在 1000 元左右", add_details)
        self.assertTrue(any("礼物颜色偏好" in detail and "喜欢紫色" in detail for detail in add_details))
        self.assertTrue(any("首饰类礼物偏好" in detail and "玫瑰金" in detail for detail in add_details))
        self.assertTrue(any("以前送过收礼人玫瑰金项链" in detail for detail in add_details))
        self.assertTrue(all(item.scope == "gift_planning" for item in self.skill.memory.active()))

    def test_semantic_extractor_records_jewelry_selection_after_inferred_gift_scope(self):
        def extractor(text, context, scope, active):
            self.assertEqual("选玫瑰金耳钉", text)
            self.assertEqual("gift_planning", scope)
            return [
                MemoryItem(
                    DECISION,
                    "本次给收礼人的礼物已选定为玫瑰金耳钉",
                    scope="gift_planning",
                    target="",
                    predicate="selected",
                    source="llm_semantic_extractor",
                    confidence=0.92,
                    evidence=[text],
                    applies_when=["gift_planning"],
                    validity={"time_scope": "current_task"},
                )
            ]

        skill = AssistSkill(memory_dir=self.tmp.name, persist=False, semantic_extractor=extractor)
        context = (
            "user: 预算1000元左右；她喜欢紫色；如果是首饰，她喜欢玫瑰金；以前送过玫瑰金项链，送过的不要再送。\n"
            "assistant: 推荐几个不踩雷的紫色+玫瑰金方案，避开项链。"
        )
        response = skill.process_message("选玫瑰金耳钉", context=context)

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertIn("本次给收礼人的礼物已选定为玫瑰金耳钉", add_details)

    def test_gift_candidate_name_reference_uses_semantic_extractor(self):
        def extractor(text, context, scope, active):
            raise AssertionError("candidate references should be handled by the generic rule fast path")

        skill = AssistSkill(memory_dir=self.tmp.name, persist=False, semantic_extractor=extractor)
        context = (
            "user: 不是，我想换个非首饰品类。\n"
            "assistant: 1. 万事利（Wensli）：淡紫素绉缎方巾，约300-500元。"
        )
        response = skill.process_message("万事利（Wensli）：淡紫素绉缎方巾", context=context)

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertIn("本次给收礼人的礼物已选定为万事利（Wensli）：淡紫素绉缎方巾", add_details)

    def test_gift_new_selection_is_not_deduped_by_previous_selected_decision(self):
        skill = AssistSkill(memory_dir=self.tmp.name, persist=False)
        context = "user: 帮我给女朋友选个生日礼物。\nassistant: 推荐：潘多拉玫瑰金手链。"
        skill.process_message("潘多拉玫瑰金手链", context=context)

        response = skill.process_message(
            "万事利（Wensli）：淡紫素绉缎方巾",
            context=context + "\nassistant: 1. 万事利（Wensli）：淡紫素绉缎方巾，约300-500元。",
        )

        self.assertTrue(any(action["action"] == "add" and "万事利" in action["detail"] for action in response.memory_actions))
        selected = [item.content for item in skill.memory.active() if item.type == DECISION and item.predicate == "selected"]
        self.assertTrue(any("潘多拉" in item for item in selected))
        self.assertTrue(any("万事利" in item for item in selected))

    def test_gift_metatalk_is_not_recorded_as_selected_gift(self):
        response = self.skill.process_message(
            "如果我在你的推荐的多个选项里说了某个选项，就代表我选好了，已经锁定了明白吗",
            context="user: 帮我给女朋友选个生日礼物。\nassistant: 推荐：潘多拉玫瑰金手链。",
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertFalse(any("明白吗" in detail for detail in add_details))
        self.assertTrue(any("复述某个候选名称" in detail for detail in add_details))

    def test_gift_correction_selected_means_stop_recommending_other_options_becomes_workflow(self):
        response = self.skill.process_message(
            "我已经选中爱马仕入门丝巾了，不必再提其他",
            context=(
                "user: 不是，我想换个非首饰品类。\n"
                "assistant: 1. 小众包袋\n2. 真丝丝巾/方巾 — 500-1000元，爱马仕入门丝巾、郁金香或上海故事"
            ),
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertTrue(any("复述某个候选名称" in detail and "不要继续追问或发散推荐" in detail for detail in add_details))
        self.assertIn("本次给收礼人的礼物已选定为爱马仕入门丝巾", add_details)
        active = self.skill.memory.active()
        self.assertTrue(any(item.type == WORKFLOW and "不要继续追问或发散推荐" in item.content for item in active))
        self.assertTrue(any(item.type == DECISION and "爱马仕入门丝巾" in item.content for item in active))

    def test_gift_initial_task_request_is_not_recorded_as_selected_candidate(self):
        response = self.skill.process_message(
            "帮我给女朋友选个生日礼物。",
            context="assistant: 推荐：玫瑰金细手链。",
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertFalse(any("已选定为帮我给女朋友选个生日礼物" in detail for detail in add_details))

    def test_gift_rhetorical_previous_gift_question_does_not_pollute_history(self):
        response = self.skill.process_message(
            "不是送过手链了吗？",
            context="user: 帮我给女朋友选个生日礼物。\nassistant: 推荐方向：玫瑰金手链。",
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertFalse(any("手链了吗" in detail for detail in add_details))
        self.assertFalse(any(action.get("detail", "").startswith("以前送过") for action in response.memory_actions))

    def test_gift_jewelry_preference_is_extracted_as_narrow_rule(self):
        response = self.skill.process_message(
            "预算1000元左右；她喜欢紫色；如果是首饰，她喜欢玫瑰金；以前送过玫瑰金项链，送过的不要再送。"
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertTrue(any("首饰类礼物偏好" in detail and "玫瑰金" in detail for detail in add_details))

    def test_gift_color_preference_is_separate_from_jewelry_preference(self):
        response = self.skill.process_message(
            "预算1000元左右；她喜欢紫色；如果是首饰，她喜欢玫瑰金；以前送过玫瑰金项链，送过的不要再送。"
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertTrue(any("礼物颜色偏好" in detail and "喜欢紫色" in detail for detail in add_details))
        self.assertTrue(any("首饰类礼物偏好" in detail and "玫瑰金" in detail for detail in add_details))
        self.assertFalse(any("礼物偏好/背景：她喜欢紫色；如果是首饰" in detail for detail in add_details))

    def test_gift_planning_does_not_treat_profile_numbers_as_budget(self):
        response = self.skill.process_message("给老公买礼物，他身高180，喜欢跑步")

        self.assertTrue(any(action["action"] == "add" for action in response.memory_actions))
        self.assertFalse(any(action.get("detail", "").endswith("180 元左右") for action in response.memory_actions))
        self.assertNotIn("budget_limit", [item.predicate for item in self.skill.memory.active()])

        self.skill.reset_memory()
        response = self.skill.process_message("给老公买礼物，1024 程序员节快到了，他喜欢机械键盘")

        self.assertTrue(any(action["action"] == "add" for action in response.memory_actions))
        self.assertNotIn("budget_limit", [item.predicate for item in self.skill.memory.active()])

    def test_gift_planning_records_selected_or_purchased_gift_as_decision(self):
        response = self.skill.process_message(
            "我已经买了信乐烧莲花盆",
            context="user: 帮我给老公选生日礼物。",
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertIn("本次给老公的礼物已选定为信乐烧莲花盆", add_details)
        active = self.skill.memory.active()
        self.assertEqual("decision", active[0].type)
        self.assertEqual("selected", active[0].predicate)

    def test_gift_explicit_current_selected_gift_is_not_blocked_as_temporary_instruction(self):
        response = self.skill.process_message(
            "本次给女朋友的礼物已选定为 Jo Malone London 祖玛珑英国梨与小苍兰 30ml 古龙水礼盒",
            context="user: 帮我给女朋友选个生日礼物。",
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertIn("本次给女朋友的礼物已选定为Jo Malone London 祖玛珑英国梨与小苍兰 30ml 古龙水礼盒", add_details)
        self.assertFalse(any(action["action"] == "reject" and action.get("reason") == "temporary_instruction" for action in response.memory_actions))

    def test_gift_oral_choice_records_selected_fragrance(self):
        response = self.skill.process_message(
            "好的我选择祖马龙这个香水",
            context=(
                "user: 删除女朋友喜欢紫色后再推荐一个不是首饰类的\n"
                "assistant: 推荐：Jo Malone London 祖玛珑 英国梨与小苍兰 30ml 古龙水礼盒。"
            ),
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertTrue(any("祖马龙这个香水" in detail or "祖玛珑" in detail for detail in add_details))
        self.assertFalse(any("好的我选择" in detail for detail in add_details))
        self.assertTrue(any(item.type == DECISION and item.predicate == "selected" for item in self.skill.memory.active()))

    def test_gift_i_choose_candidate_records_selected_gift(self):
        response = self.skill.process_message(
            "我选索尼 WH-CH720N 无线降噪耳机",
            context=(
                "user: 删除 她喜欢紫色。然后：再给一个不重复的礼物方向。\n"
                "assistant: 1. 索尼 WH-CH720N 无线降噪耳机 —— 续航长、佩戴轻，通勤或者在家听歌追剧都用得上，参考价 700-900 元。"
            ),
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertIn("本次给收礼人的礼物已选定为索尼 WH-CH720N 无线降噪耳机", add_details)
        self.assertTrue(any("索尼 WH-CH720N 无线降噪耳机" in item.content for item in self.skill.memory.active()))

    def test_gift_numbered_candidate_selection_records_selected_gift(self):
        context = (
            "user: 删除 她喜欢紫色。然后：再给一个不重复的礼物方向。\n"
            "assistant: 1. 索尼 WH-CH720N 无线降噪耳机 —— 续航长、佩戴轻，参考价 700-900 元。\n"
            "2. 乐高花卉系列花束套装（10280）—— 拼完当桌面装饰。\n"
            "3. Fujifilm instax mini Link 2 手机照片打印机 + 两包相纸 —— 随拍随打。"
        )

        response = self.skill.process_message("第一款", context=context)

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertIn("本次给收礼人的礼物已选定为索尼 WH-CH720N 无线降噪耳机", add_details)

    def test_gift_partial_candidate_selection_records_selected_gift(self):
        context = (
            "user: 删除 她喜欢紫色。然后：再给一个不重复的礼物方向。\n"
            "assistant: 1. 索尼 WH-CH720N 无线降噪耳机 —— 续航长、佩戴轻，参考价 700-900 元。"
        )

        response = self.skill.process_message("就那个 WH-CH720N", context=context)

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertIn("本次给收礼人的礼物已选定为索尼 WH-CH720N 无线降噪耳机", add_details)

    def test_gift_numbered_selection_uses_latest_assistant_candidates(self):
        context = (
            "user: 帮我给女朋友选个生日礼物。\n"
            "assistant: 1. 首饰\n2. 包包\n"
            "user: 删除 她喜欢紫色。然后：再给一个不重复的礼物方向。\n"
            "assistant: 1. 轻奢包包 —— 1000元可以选小众质感款。\n"
            "2. 功效型护肤礼盒 —— 选她用惯的品牌。\n"
            "3. 体验类 —— 餐厅约会或 spa 套餐。"
        )

        response = self.skill.process_message("第一款", context=context)

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertIn("本次给女朋友的礼物已选定为轻奢包包", add_details)
        self.assertFalse(any(detail.endswith("首饰") for detail in add_details))

    def test_generic_gift_category_is_not_saved_as_selected_gift(self):
        response = self.skill.process_message(
            "第一款",
            context="user: 帮我给女朋友选个生日礼物。\nassistant: 1. 首饰\n2. 包包\n3. 体验类",
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertFalse(any("已选定为首饰" in detail for detail in add_details))

    def test_contextual_confirmed_gift_updates_specific_selected_item(self):
        self.skill.process_message(
            "紫色丝巾/披肩 — 质感好的桑蚕丝款",
            context="user: 帮我给女朋友选个生日礼物。\nassistant: 推荐几个非首饰方向：紫色丝巾/披肩 — 质感好的桑蚕丝款。",
        )
        context = "\n".join(
            [
                "user: 紫色丝巾/披肩 — 质感好的桑蚕丝款",
                "assistant: 已选礼物：紫色桑蚕丝丝巾/披肩，1000预算能拿不错的品牌。",
                "user: 野兽派：设计感强，包装精致，直接送礼省心",
                "assistant: 好，锁定野兽派。野兽派丝巾礼盒，材质选100%桑蚕丝，紫色选偏深或灰调。",
                "user: 下次要记住我具体选择的是哪个礼物，具体到礼物本身，不是方向",
                "assistant: 明白，以后记录到具体商品名称，不只记品类。这次已确认的礼物是野兽派丝巾礼盒，材质选100%桑蚕丝。",
            ]
        )

        response = self.skill.process_message("把本次确认的礼物也加入记忆", context=context)

        add_details = [action.get("detail", "") for action in response.memory_actions if action.get("action") == "add"]
        self.assertIn("本次给女朋友的礼物已选定为野兽派丝巾礼盒", add_details)
        active_decisions = [
            item.content
            for item in self.skill.memory.active()
            if item.type == DECISION and item.predicate == "selected"
        ]
        self.assertIn("本次给女朋友的礼物已选定为野兽派丝巾礼盒", active_decisions)
        self.assertFalse(any("紫色丝巾/披肩" in content for content in active_decisions))

    def test_gift_deictic_selection_records_actual_recommended_gift(self):
        response = self.skill.process_message(
            "就这个吧",
            context="user: 帮我给女朋友选个生日礼物。\nassistant: 推荐方向：小型复古蓝牙音箱。",
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertIn("本次给女朋友的礼物已选定为小型复古蓝牙音箱", add_details)
        self.assertNotIn("本次给女朋友的礼物已选定为这个", add_details)

    def test_gift_lookup_instruction_is_not_recorded_as_selected_gift(self):
        response = self.skill.process_message(
            "找时间最近的一次，直接回答我",
            context="user: 她喜欢周杰伦，帮我找最近一次的演唱会。\nassistant: 推荐演唱会 / 音乐会门票。",
        )

        add_details = [action.get("detail", "") for action in response.memory_actions if action.get("action") == "add"]
        self.assertFalse(any("已选定为找时间最近的一次" in detail for detail in add_details))
        self.assertFalse(any(item.type == DECISION and "直接回答我" in item.content for item in self.skill.memory.active()))

    def test_gift_concert_lookup_instruction_becomes_workflow_memory(self):
        response = self.skill.process_message(
            "她喜欢周杰伦，帮我找最近一次的演唱会。以后当我定好歌手和演唱会，你就找时间最近的一次，直接回答我",
            context="user: 给我找周杰伦的演唱会，我和她一起去看",
        )

        workflows = [item for item in self.skill.memory.active() if item.type == WORKFLOW]
        self.assertTrue(workflows)
        self.assertEqual("gift_planning", workflows[0].scope)
        self.assertIn("找时间最近的一次，直接回答我", workflows[0].content)
        self.assertFalse(any(item.type == DECISION and "直接回答我" in item.content for item in self.skill.memory.active()))

    def test_gift_purchase_channel_teaching_becomes_workflow_memory(self):
        response = self.skill.process_message(
            "以后我选定礼物后，如果我直接问销售渠道，你就按已选礼物直接给购买渠道、下单注意点和包装建议，不要重新推荐礼物。",
            context="user: 帮我给女朋友选礼物。\nassistant: 推荐：拍立得相机配相册。\nuser: 我选拍立得相机配相册",
        )

        add_details = [action.get("detail", "") for action in response.memory_actions if action.get("action") == "add"]
        self.assertTrue(any("销售渠道" in detail and "不要重新推荐礼物" in detail for detail in add_details))
        self.assertTrue(any(item.type == WORKFLOW and "销售渠道" in item.content for item in self.skill.memory.active()))
        self.assertFalse(any(item.type == DECISION and "选定礼物后" in item.content for item in self.skill.memory.active()))

    def test_retrieval_filters_cross_scope_memories(self):
        self.skill.process_message("以后学习计划请先看例题再讲知识点。")
        self.skill.process_message(
            "预算1000元左右；她喜欢紫色。",
            context="user: 帮我给女朋友选个生日礼物。",
        )

        gift_memories = self.skill.retrieve_relevant_memories(
            "给我一个礼物推荐。",
            context="user: 帮我给女朋友选个生日礼物。",
        )
        self.assertTrue(gift_memories)
        self.assertTrue(all(item.scope == "gift_planning" for item in gift_memories))

        study_memories = self.skill.retrieve_relevant_memories("帮我做一个英语复习计划。")
        self.assertTrue(study_memories)
        self.assertTrue(all(item.scope == "study_plan" for item in study_memories))

    def test_gift_compound_delete_continues_with_recommendation(self):
        self.skill.process_message(
            "预算1000元左右；她喜欢紫色；如果是首饰，她喜欢玫瑰金；以前送过玫瑰金项链，送过的不要再送。",
            context="user: 帮我给女朋友选个生日礼物。",
        )

        response = self.skill.process_message("删除 她喜欢紫色。然后：再给一个不重复的礼物方向。")

        self.assertTrue(any(action["action"] == "delete" for action in response.memory_actions))
        self.assertIn("推荐方向", response.text)
        self.assertNotIn("紫色", " ".join(item.content for item in self.skill.memory.active()))

    def test_gift_non_jewelry_feedback_becomes_current_task_constraint(self):
        response = self.skill.process_message(
            "不是，我想换个非首饰品类。",
            context="user: 帮我给女朋友选个生日礼物。\nassistant: 推荐玫瑰金手链。",
        )

        add_details = [action["detail"] for action in response.memory_actions if action["action"] == "add"]
        self.assertTrue(any("不要首饰" in detail for detail in add_details))
        active = self.skill.memory.active()
        self.assertEqual("constraint", active[0].type)
        self.assertEqual("current_task", active[0].validity.get("time_scope"))


if __name__ == "__main__":
    unittest.main()
