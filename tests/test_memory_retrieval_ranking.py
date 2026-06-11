import tempfile
import unittest

from assist_everything_betterandbetter_skill.mem0_backend import Mem0Config
from assist_everything_betterandbetter_skill.memory import MemoryItem
from assist_everything_betterandbetter_skill.skill import AssistSkill


class MemoryRetrievalRankingTest(unittest.TestCase):
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
