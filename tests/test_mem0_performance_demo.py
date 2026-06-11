import unittest

from evalharness.mem0_performance import DEMO_USER_ID, generate_demo_memories, run_performance_demo


class Mem0PerformanceDemoTest(unittest.TestCase):
    def test_generate_demo_memories_is_deterministic(self):
        first = generate_demo_memories(scale=5, seed=7)
        second = generate_demo_memories(scale=5, seed=7)

        self.assertEqual(first, second)
        self.assertEqual(5, len(first))
        self.assertEqual("demo_mem_000001", first[0]["id"])
        self.assertIn("content", first[0])
        self.assertIn("updated_at", first[0])

    def test_dry_run_report_has_metrics_examples_and_demo_user(self):
        report = run_performance_demo(engine="mem0_hosted", mode="dry_run", scale=1000, query_count=5)

        self.assertTrue(report["ok"])
        self.assertEqual("dry_run", report["mode"])
        self.assertEqual("mem0_hosted", report["engine"])
        self.assertEqual(1000, report["scale"])
        self.assertEqual(DEMO_USER_ID, report["demo_user_id"])
        self.assertGreater(report["metrics"]["write_qps"], 0)
        self.assertGreaterEqual(report["metrics"]["search_p95_ms"], report["metrics"]["search_p50_ms"])
        self.assertEqual(5, len(report["examples"]))
        self.assertEqual("score_time", report["examples"][0]["top_k"][0]["retrieval_rank_strategy"])
        self.assertEqual([], report["reset"]["errors"])
