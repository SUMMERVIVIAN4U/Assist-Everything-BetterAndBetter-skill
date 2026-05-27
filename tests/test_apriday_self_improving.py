import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "apriday_self_improving.py"


class ApridaySelfImprovingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = os.environ.copy()
        self.env["APRIDAY_MEMORY_DIR"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=True,
            text=True,
            capture_output=True,
            env=self.env,
        )
        return json.loads(completed.stdout)

    def test_reset_observe_and_view(self):
        self.assertTrue(self.run_cli("reset")["ok"])
        result = self.run_cli("observe", "以后写方案先分析评分标准，再写实现。", "--approve")
        self.assertTrue(result["saved"])
        state = self.run_cli("view")
        self.assertEqual(len(state["memories"]), 1)
        self.assertEqual(state["memories"][0]["type"], "workflow_rule")

    def test_temporary_instruction_is_not_saved(self):
        self.run_cli("reset")
        result = self.run_cli("observe", "这次输出请用表格。")
        self.assertFalse(result["saved"])
        self.assertEqual(result["reason"], "temporary_instruction")

    def test_memory_is_applied_to_later_task(self):
        self.run_cli("reset")
        self.run_cli("observe", "以后做架构方案时先分析评分标准，再写实现。", "--approve")
        result = self.run_cli("apply", "帮我做一个新的架构方案")
        self.assertIn("mem_0001", result["used_memory_ids"])
        self.assertEqual(result["user_effort_reduction"], "medium")

    def test_conflicting_preference_supersedes_old_memory(self):
        self.run_cli("reset")
        self.run_cli("observe", "以后礼物优先银色。", "--approve")
        result = self.run_cli("observe", "以后礼物优先玫瑰金。", "--approve")
        self.assertEqual(result["superseded"], ["mem_0001"])
        state = self.run_cli("view")
        statuses = {memory["id"]: memory["status"] for memory in state["memories"]}
        self.assertEqual(statuses["mem_0001"], "superseded")
        self.assertEqual(statuses["mem_0002"], "active")

    def test_deleted_memory_is_not_applied(self):
        self.run_cli("reset")
        self.run_cli("observe", "以后做架构方案时先分析评分标准，再写实现。", "--approve")
        self.assertTrue(self.run_cli("delete", "mem_0001")["ok"])
        result = self.run_cli("apply", "帮我做一个新的架构方案")
        self.assertNotIn("mem_0001", result["used_memory_ids"])

    def test_evaluate_reaches_high_score(self):
        report = self.run_cli("evaluate")
        self.assertGreaterEqual(report["score"]["total"], 90)
        self.assertEqual(report["score"]["scores"]["reproducibility"], 10)
        self.assertEqual(report["score"]["scores"]["user_control_transparency"], 10)


if __name__ == "__main__":
    unittest.main()
