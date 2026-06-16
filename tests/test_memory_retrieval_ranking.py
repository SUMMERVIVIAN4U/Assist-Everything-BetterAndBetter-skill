import tempfile
import unittest

from assist_everything_betterandbetter_skill.mem0_backend import Mem0Config
from assist_everything_betterandbetter_skill.memory import MemoryItem
from assist_everything_betterandbetter_skill.skill import AssistSkill


class MemoryRetrievalRankingTest(unittest.TestCase):
    def test_delete_exact_memory_id_does_not_delete_related_memories(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = AssistSkill(memory_dir=tmp, persist=True)
            budget = skill.memory.add(MemoryItem("constraint", "给女朋友选礼物预算在 1000 元左右", scope="gift_planning"))
            decision = skill.memory.add(MemoryItem("decision", "本次给女朋友的礼物已选定为手作体验", scope="gift_planning"))

            deleted = skill.memory.delete(budget.id)

            self.assertEqual([budget.id], [item.id for item in deleted])
            self.assertEqual("deleted", skill.memory.get(budget.id, include_inactive=True).status)
            self.assertEqual("active", skill.memory.get(decision.id).status)

    def test_delete_color_preference_does_not_delete_selected_gift_or_constraints(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = AssistSkill(memory_dir=tmp, persist=True)
            purple = skill.memory.add(
                MemoryItem(
                    "preference",
                    "女朋友的礼物颜色偏好：喜欢紫色",
                    scope="gift_planning",
                    target="女朋友",
                    predicate="likes",
                    tags=["紫色"],
                )
            )
            selected = skill.memory.add(
                MemoryItem(
                    "decision",
                    "本次给女朋友的礼物已选定为潘多拉玫瑰金紫水晶耳钉/手链",
                    scope="gift_planning",
                    target="女朋友",
                    predicate="selected",
                    validity={"time_scope": "current_task", "session_id": "old_session"},
                )
            )
            budget = skill.memory.add(
                MemoryItem(
                    "constraint",
                    "给女朋友选礼物预算在 1000 元左右",
                    scope="gift_planning",
                    target="女朋友",
                    predicate="budget_limit",
                    validity={"time_scope": "current_task", "session_id": "old_session"},
                )
            )

            response = skill.process_message("删除 女朋友喜欢紫色")

            self.assertEqual([purple.id], [action["memory_id"] for action in response.memory_actions if action["action"] == "delete"])
            self.assertEqual("deleted", skill.memory.get(purple.id, include_inactive=True).status)
            self.assertEqual("active", skill.memory.get(selected.id).status)
            self.assertEqual("active", skill.memory.get(budget.id).status)

    def test_local_retrieval_ranks_by_score_then_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = AssistSkill(memory_dir=tmp, persist=True)
            low_score_recent = MemoryItem(
                "preference",
                "上海亲子游可以安排户外公园",
                scope="life_family_travel",
                confidence=0.4,
                updated_at="2026-06-11T08:00:00+00:00",
                created_at="2026-06-11T08:00:00+00:00",
                tags=["上海", "亲子游"],
            )
            high_score_old = MemoryItem(
                "preference",
                "上海亲子游优先室内，少走路，孩子怕热",
                scope="life_family_travel",
                confidence=0.95,
                updated_at="2026-06-10T08:00:00+00:00",
                created_at="2026-06-10T08:00:00+00:00",
                tags=["上海", "亲子游", "室内"],
            )
            same_score_newer = MemoryItem(
                "preference",
                "上海亲子游优先动物园上午入园",
                scope="life_family_travel",
                confidence=0.95,
                updated_at="2026-06-11T09:00:00+00:00",
                created_at="2026-06-11T09:00:00+00:00",
                tags=["上海", "亲子游", "动物园"],
            )
            skill.memory.add(low_score_recent)
            skill.memory.add(high_score_old)
            skill.memory.add(same_score_newer)

            results = skill.retrieve_relevant_memories("上海亲子游推荐")

            self.assertEqual([same_score_newer.id, high_score_old.id, low_score_recent.id], [item.id for item in results])
            self.assertGreaterEqual(results[0].validity["retrieval_score"], results[1].validity["retrieval_score"])

    def test_generic_followup_uses_single_active_scene_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = AssistSkill(memory_dir=tmp, persist=True)
            first.process_message(
                "预算1000元左右；她喜欢紫色；如果是首饰，她喜欢玫瑰金；以前送过玫瑰金项链，送过的不要再送。",
                context="user: 帮我给女朋友选个生日礼物。",
            )
            first.process_message(
                "不是，我想换个非首饰品类。",
                context="user: 给我一个礼物推荐。\nassistant: 推荐玫瑰金手链。",
            )

            next_session = AssistSkill(memory_dir=tmp, persist=True)
            results = next_session.retrieve_relevant_memories("那再给一个推荐。")
            pack = next_session.relevant_memory_pack("那再给一个推荐。", results)

            contents = [item.content for item in results]
            self.assertTrue(any("女朋友" in content or "礼物" in content or "首饰" in content for content in contents))
            self.assertTrue(all(item.scope == "gift_planning" for item in results))
            self.assertTrue(any("预算在 1000 元左右" in item["content"] for item in pack["confirm_first"]))
            self.assertTrue(any("不要首饰" in item["content"] for item in pack["confirm_first"]))

    def test_generic_followup_does_not_guess_scope_when_multiple_scenes_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = AssistSkill(memory_dir=tmp, persist=True)
            skill.memory.add(MemoryItem("constraint", "给女朋友选礼物预算在 1000 元左右", scope="gift_planning"))
            skill.memory.add(MemoryItem("constraint", "避开人挤人的网红点", scope="life_family_travel"))

            results = skill.retrieve_relevant_memories("那再给一个推荐。")

            self.assertEqual([], results)

    def test_gift_history_lookup_includes_selected_decisions_from_previous_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = AssistSkill(memory_dir=tmp, persist=True)
            first.memory.add(
                MemoryItem(
                    "history",
                    "以前送过女朋友玫瑰金项链，送过的不要再送",
                    scope="gift_planning",
                    target="女朋友",
                    predicate="previously_given",
                    validity={"time_scope": "past"},
                )
            )
            for content in [
                "本次给女朋友的礼物已选定为找时间最近的一次，直接回答我",
                "本次给女朋友的礼物已选定为玫瑰金手链 APM Monaco",
                "本次给女朋友的礼物已选定为演唱会 / 音乐会门票",
                "本次给女朋友的礼物已选定为Diptyque香氛蜡烛礼盒",
            ]:
                first.memory.add(
                    MemoryItem(
                        "decision",
                        content,
                        scope="gift_planning",
                        target="女朋友",
                        predicate="selected",
                        validity={"time_scope": "current_task", "session_id": "old_session"},
                    )
                )

            next_session = AssistSkill(memory_dir=tmp, persist=True)
            results = next_session.retrieve_relevant_memories("最近我已经送过女朋友什么礼物")

            contents = [item.content for item in results]
            self.assertTrue(any("玫瑰金项链" in content for content in contents))
            self.assertTrue(any("玫瑰金手链 APM Monaco" in content for content in contents))
            self.assertTrue(any("演唱会 / 音乐会门票" in content for content in contents))
            self.assertTrue(any("Diptyque香氛蜡烛礼盒" in content for content in contents))
            self.assertFalse(any("直接回答我" in content for content in contents))

    def test_short_previous_gift_lookup_includes_selected_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = AssistSkill(memory_dir=tmp, persist=True)
            first.memory.add(
                MemoryItem(
                    "decision",
                    "本次给女朋友的礼物已选定为Jo Malone London 祖玛珑英国梨与小苍兰 30ml 古龙水礼盒",
                    scope="gift_planning",
                    target="女朋友",
                    predicate="selected",
                    validity={"time_scope": "current_task", "session_id": "old_session"},
                )
            )

            next_session = AssistSkill(memory_dir=tmp, persist=True)
            results = next_session.retrieve_relevant_memories("以前送过什么?")

            contents = [item.content for item in results]
            self.assertTrue(any("祖玛珑" in content for content in contents))

    def test_previous_selected_gift_lookup_with_pronoun_includes_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = AssistSkill(memory_dir=tmp, persist=True)
            first.memory.add(
                MemoryItem(
                    "decision",
                    "本次给女朋友的礼物已选定为索尼 WH-CH720N 无线降噪耳机",
                    scope="gift_planning",
                    target="女朋友",
                    predicate="selected",
                    validity={"time_scope": "current_task", "session_id": "old_session"},
                )
            )

            next_session = AssistSkill(memory_dir=tmp, persist=True)
            results = next_session.retrieve_relevant_memories("我之前给她选过什么？")

            contents = [item.content for item in results]
            self.assertTrue(any("索尼 WH-CH720N 无线降噪耳机" in content for content in contents))

    def test_llm_retrieval_intent_can_expand_natural_gift_history_lookup(self):
        calls = []

        def classifier(text, context, active_items):
            calls.append({"text": text, "context": context, "count": len(active_items)})
            return {
                "intent": "gift_history_lookup",
                "scope": "gift_planning",
                "target": "女朋友",
                "include_types": ["history", "decision"],
                "include_expired_current_task": True,
            }

        with tempfile.TemporaryDirectory() as tmp:
            first = AssistSkill(memory_dir=tmp, persist=True)
            first.memory.add(
                MemoryItem(
                    "history",
                    "以前送过女朋友玫瑰金项链，送过的不要再送",
                    scope="gift_planning",
                    target="女朋友",
                    predicate="previously_given",
                    validity={"time_scope": "past"},
                )
            )
            first.memory.add(
                MemoryItem(
                    "decision",
                    "本次给女朋友的礼物已选定为演唱会 / 音乐会门票",
                    scope="gift_planning",
                    target="女朋友",
                    predicate="selected",
                    validity={"time_scope": "current_task", "session_id": "old_session"},
                )
            )

            next_session = AssistSkill(memory_dir=tmp, persist=True, retrieval_intent_classifier=classifier)
            results = next_session.retrieve_relevant_memories(
                "我给她置办过的生日东西都有哪些",
                context="user: 帮我给女朋友选生日礼物。",
            )

            contents = [item.content for item in results]
            self.assertTrue(calls)
            self.assertTrue(any("玫瑰金项链" in content for content in contents))
            self.assertTrue(any("演唱会 / 音乐会门票" in content for content in contents))

    def test_hosted_mem0_retrieval_uses_same_score_time_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = AssistSkill(
                memory_dir=tmp,
                persist=True,
                mem0_config=Mem0Config(enabled=True, base_url="https://mem0.example", api_key="k", user_id="u1"),
                memory_backend="mem0_hosted",
            )

            old_low = MemoryItem(
                "preference",
                "上海亲子游可以安排户外公园",
                scope="life_family_travel",
                confidence=0.4,
                updated_at="2026-06-11T08:00:00+00:00",
                created_at="2026-06-11T08:00:00+00:00",
            )
            old_low.validity["mem0_score"] = 0.4
            old_high = MemoryItem(
                "preference",
                "上海亲子游优先室内，少走路，孩子怕热",
                scope="life_family_travel",
                confidence=0.9,
                updated_at="2026-06-10T08:00:00+00:00",
                created_at="2026-06-10T08:00:00+00:00",
            )
            old_high.validity["mem0_score"] = 0.9
            newer_high = MemoryItem(
                "preference",
                "上海亲子游优先动物园上午入园",
                scope="life_family_travel",
                confidence=0.9,
                updated_at="2026-06-11T09:00:00+00:00",
                created_at="2026-06-11T09:00:00+00:00",
            )
            newer_high.validity["mem0_score"] = 0.9

            class FakeHosted:
                def search(self, query, top_k=8):
                    return [old_low, old_high, newer_high]

            skill.mem0_client = FakeHosted()

            results = skill.retrieve_relevant_memories("上海亲子游推荐")

            self.assertEqual([newer_high.id, old_high.id, old_low.id], [item.id for item in results])
            self.assertEqual("score_time", results[0].validity["retrieval_rank_strategy"])


if __name__ == "__main__":
    unittest.main()
