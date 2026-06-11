import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evalharness import mem0_performance
from evalharness.mem0_performance import (
    DEMO_USER_ID,
    generate_demo_memories,
    generate_demo_queries,
    run_performance_demo,
)


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
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "mem0_performance_demo.json"
            with patch.object(mem0_performance, "LATEST_PERFORMANCE_REPORT", report_path):
                report = run_performance_demo(engine="mem0_hosted", mode="dry_run", scale=1000, query_count=5)

        self.assertTrue(report["ok"])
        self.assertEqual("dry_run", report["mode"])
        self.assertEqual("mem0_hosted", report["engine"])
        self.assertEqual(1000, report["scale"])
        self.assertEqual(DEMO_USER_ID, report["demo_user_id"])
        self.assertGreater(report["metrics"]["write_qps"], 0)
        self.assertEqual(0.0, report["metrics"]["error_rate"])
        self.assertGreaterEqual(report["metrics"]["search_p95_ms"], report["metrics"]["search_p50_ms"])
        self.assertIn("elapsed_ms", report["phases"][0])
        self.assertIn("ok", report["phases"][0])
        self.assertNotIn("duration_ms", report["phases"][0])
        self.assertNotIn("status", report["phases"][0])
        self.assertEqual(5, len(report["examples"]))
        self.assertEqual("score_time", report["examples"][0]["top_k"][0]["retrieval_rank_strategy"])
        self.assertEqual(report["examples"][0]["top_k"][0]["score"], report["examples"][0]["top_k"][0]["retrieval_score"])
        self.assertEqual(1000, report["reset"]["found_count"])
        self.assertEqual(1000, report["reset"]["deleted_count"])
        self.assertEqual([], report["reset"]["errors"])

    def test_generate_demo_queries_is_deterministic(self):
        first = generate_demo_queries(query_count=4, seed=11)
        second = generate_demo_queries(query_count=4, seed=11)

        self.assertEqual(first, second)
        self.assertEqual(4, len(first))

    def test_dry_run_does_not_use_provided_client(self):
        class ExplodingClient:
            def __getattr__(self, name):
                raise AssertionError(f"dry_run unexpectedly used client attribute {name}")

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "mem0_performance_demo.json"
            with patch.object(mem0_performance, "LATEST_PERFORMANCE_REPORT", report_path):
                report = run_performance_demo(
                    engine="mem0_hosted",
                    mode="dry_run",
                    scale=1000,
                    query_count=2,
                    client=ExplodingClient(),
                )

        self.assertTrue(report["ok"])

    def test_real_run_is_not_implemented_in_task_1(self):
        with self.assertRaisesRegex(ValueError, "real_run mode is not implemented"):
            run_performance_demo(engine="mem0_hosted", mode="real_run", scale=1000, query_count=1, client=object())
