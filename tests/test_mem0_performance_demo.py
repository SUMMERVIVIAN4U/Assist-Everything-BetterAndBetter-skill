import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assist_everything_betterandbetter_skill.mem0_backend import Mem0Config
from evalharness import mem0_performance, server
from evalharness.mem0_performance import (
    DEMO_USER_ID,
    config_for_demo_user,
    generate_demo_memories,
    generate_demo_queries,
    reset_demo_memory,
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


class Mem0PerformanceIsolationTest(unittest.TestCase):
    def test_config_for_demo_user_never_reuses_chat_user_id(self):
        original = Mem0Config(enabled=True, base_url="https://mem0.example", api_key="k", user_id="workbench-user")

        demo = config_for_demo_user(original)

        self.assertEqual(DEMO_USER_ID, demo.user_id)
        self.assertNotEqual(original.user_id, demo.user_id)
        self.assertEqual(original.base_url, demo.base_url)
        self.assertEqual(original.api_key, demo.api_key)

    def test_reset_demo_memory_uses_demo_scoped_client(self):
        class FakeClient:
            def __init__(self):
                self.config = Mem0Config(enabled=True, base_url="https://mem0.example", api_key="k", user_id=DEMO_USER_ID)
                self.deleted = False
                self.page_size = None

            def delete_all(self, page_size=200):
                self.page_size = page_size
                self.deleted = True
                return {"mode": "user_scoped", "found_count": 3, "deleted_count": 3, "errors": []}

        client = FakeClient()

        result = reset_demo_memory(client)

        self.assertTrue(client.deleted)
        self.assertEqual(200, client.page_size)
        self.assertTrue(result["ok"])
        self.assertEqual(DEMO_USER_ID, result["demo_user_id"])
        self.assertEqual(3, result["deleted_count"])

    def test_reset_demo_memory_rejects_non_demo_scoped_client(self):
        class FakeClient:
            def __init__(self):
                self.config = Mem0Config(
                    enabled=True,
                    base_url="https://mem0.example",
                    api_key="k",
                    user_id="workbench-user",
                )
                self.deleted = False

            def delete_all(self, page_size=200):
                self.deleted = True
                return {"mode": "user_scoped", "found_count": 3, "deleted_count": 3, "errors": []}

        client = FakeClient()

        result = reset_demo_memory(client)

        self.assertFalse(client.deleted)
        self.assertFalse(result["ok"])
        self.assertEqual("scope", result["stage"])
        self.assertEqual(DEMO_USER_ID, result["demo_user_id"])
        self.assertEqual(0, result["found_count"])
        self.assertEqual(0, result["deleted_count"])
        self.assertIn("workbench-user", result["errors"][0])

    def test_reset_demo_memory_rejects_client_without_config(self):
        class FakeClient:
            def __init__(self):
                self.deleted = False

            def delete_all(self, page_size=200):
                self.deleted = True
                return {"mode": "user_scoped", "found_count": 3, "deleted_count": 3, "errors": []}

        client = FakeClient()

        result = reset_demo_memory(client)

        self.assertFalse(client.deleted)
        self.assertFalse(result["ok"])
        self.assertEqual("scope", result["stage"])
        self.assertEqual(DEMO_USER_ID, result["demo_user_id"])
        self.assertEqual(0, result["found_count"])
        self.assertEqual(0, result["deleted_count"])
        self.assertIn("requires a demo-scoped Mem0 client", result["errors"][0])

    def test_reset_demo_memory_rejects_client_without_user_id(self):
        class FakeClient:
            def __init__(self):
                self.config = Mem0Config(enabled=True, base_url="https://mem0.example", api_key="k", user_id=None)
                self.deleted = False

            def delete_all(self, page_size=200):
                self.deleted = True
                return {"mode": "user_scoped", "found_count": 3, "deleted_count": 3, "errors": []}

        client = FakeClient()

        result = reset_demo_memory(client)

        self.assertFalse(client.deleted)
        self.assertFalse(result["ok"])
        self.assertEqual("scope", result["stage"])
        self.assertEqual(0, result["deleted_count"])
        self.assertIn("requires a demo-scoped Mem0 client", result["errors"][0])

    def test_reset_demo_memory_rejects_non_dict_delete_result(self):
        class FakeClient:
            def __init__(self):
                self.config = Mem0Config(enabled=True, base_url="https://mem0.example", api_key="k", user_id=DEMO_USER_ID)

            def delete_all(self, page_size=200):
                return "deleted"

        result = reset_demo_memory(FakeClient())

        self.assertFalse(result["ok"])
        self.assertEqual("delete_all", result["stage"])
        self.assertEqual(0, result["found_count"])
        self.assertEqual(0, result["deleted_count"])
        self.assertEqual(["Mem0 delete_all returned an invalid result"], result["errors"])

    def test_reset_demo_memory_normalizes_malformed_counts_and_errors(self):
        class FakeClient:
            def __init__(self):
                self.config = Mem0Config(enabled=True, base_url="https://mem0.example", api_key="k", user_id=DEMO_USER_ID)

            def delete_all(self, page_size=200):
                return {"found_count": None, "deleted_count": "many", "errors": "partial failure"}

        result = reset_demo_memory(FakeClient())

        self.assertFalse(result["ok"])
        self.assertEqual(0, result["found_count"])
        self.assertEqual(0, result["deleted_count"])
        self.assertEqual(["partial failure"], result["errors"])


class Mem0PerformanceApiTest(unittest.TestCase):
    def test_run_mem0_performance_demo_defaults_to_dry_run(self):
        with patch("evalharness.server._mem0_client_for_backend") as client_factory:
            result = server._run_mem0_performance_demo({"engine": "mem0_hosted", "scale": 1000, "query_count": 3})

        client_factory.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual("dry_run", result["mode"])
        self.assertEqual(1000, result["scale"])

    def test_reset_mem0_performance_demo_uses_demo_config(self):
        class FakeClient:
            def __init__(self, config):
                self.config = config

            def delete_all(self, page_size=200):
                return {"mode": "user_scoped", "found_count": 1, "deleted_count": 1, "errors": []}

        with patch("evalharness.server._mem0_client_for_backend", side_effect=lambda backend, config: FakeClient(config)):
            result = server._reset_mem0_performance_demo({"engine": "mem0_hosted"})

        self.assertTrue(result["ok"])
        self.assertEqual(DEMO_USER_ID, result["demo_user_id"])
