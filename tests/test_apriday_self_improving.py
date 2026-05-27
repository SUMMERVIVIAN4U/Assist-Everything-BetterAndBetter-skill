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

    def test_high_confidence_preference_auto_records_without_approve(self):
        self.run_cli("reset")
        result = self.run_cli("observe", "我特别喜欢先看结论再看细节。")
        self.assertTrue(result["saved"])
        self.assertEqual(result["action"], "auto_record")
        self.assertEqual(result["memory"]["approval"], "auto_high_confidence")

    def test_medium_confidence_candidate_asks_for_confirmation(self):
        self.run_cli("reset")
        result = self.run_cli("observe", "可能以后报告短一点？")
        self.assertFalse(result["saved"])
        self.assertIn(result["action"], {"ask", "confirm"})

    def test_duplicate_memory_is_deduped(self):
        self.run_cli("reset")
        self.run_cli("observe", "我特别喜欢先看结论再看细节。")
        result = self.run_cli("observe", "我特别喜欢先看结论再看细节。")
        self.assertFalse(result["saved"])
        self.assertEqual(result["action"], "dedupe")

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

    def test_instant_mode_skips_memory_loading(self):
        self.run_cli("reset")
        self.run_cli("observe", "以后做架构方案时先分析评分标准，再写实现。", "--approve")
        result = self.run_cli("apply", "[q] 你好")
        self.assertEqual(result["memory_mode"]["mode"], "instant")
        self.assertEqual(result["used_memory_ids"], [])

    def test_snapshot_shows_recent_active_memory(self):
        self.run_cli("reset")
        self.run_cli("observe", "以后做架构方案时先分析评分标准，再写实现。", "--approve")
        result = self.run_cli("snapshot")
        self.assertEqual(result["active_count"], 1)
        self.assertEqual(result["recent_active_memories"][0]["id"], "mem_0001")
        self.assertIn("compression", result)

    def test_profile_aggregates_interaction_style(self):
        self.run_cli("reset")
        self.run_cli("observe", "我特别喜欢以后先看结论，再看评分标准。")
        result = self.run_cli("profile")
        self.assertIn("conclusion_first", result["interaction_style"])
        self.assertIn("rubric_first", result["interaction_style"])

    def test_feedback_learning_adjusts_confidence(self):
        self.run_cli("reset")
        self.run_cli("observe", "我特别喜欢以后先看结论，再看评分标准。")
        result = self.run_cli("feedback", "mem_0001", "这个偏好应用准确，继续保持。", "--rating", "1")
        self.assertTrue(result["ok"])
        self.assertGreater(result["after_confidence"], result["before_confidence"])

    def test_privacy_report_and_redaction(self):
        self.run_cli("reset")
        rejected = self.run_cli("observe", "我的密码是 123456，请记住。")
        self.assertEqual(rejected["reason"], "private_or_sensitive")
        self.assertEqual(rejected["text"], "[redacted]")
        report = self.run_cli("privacy")
        self.assertIn("delete", report["controls"])
        self.assertEqual(report["sensitive_storage"], "private_or_sensitive observations are redacted and not saved as memory")

    def test_layers_show_source_and_retention_reason(self):
        self.run_cli("reset")
        self.run_cli("observe", "我特别喜欢以后先看结论，再看评分标准。")
        layers = self.run_cli("layers")
        self.assertEqual([layer["id"] for layer in layers["layers"]], ["L0", "L1", "L2"])
        self.assertEqual(layers["layers"][0]["status"], "ephemeral")
        self.assertGreaterEqual(layers["layers"][1]["compression"]["estimated_savings_percent"], 0)
        ledger_items = layers["layers"][2]["items"]
        self.assertEqual(ledger_items[0]["id"], "mem_0001")
        self.assertIn("retention_reason", ledger_items[0])
        self.assertIn("evidence", ledger_items[0])

    def test_evaluate_reaches_high_score(self):
        report = self.run_cli("evaluate")
        self.assertEqual(report["score"]["total"], 100)
        self.assertEqual(report["trace"]["auto_feedback"]["action"], "auto_record")
        self.assertEqual(report["trace"]["medium_candidate"]["action"], "confirm")
        self.assertEqual(report["trace"]["duplicate"]["action"], "dedupe")
        self.assertEqual(report["trace"]["instant_apply"]["memory_mode"]["mode"], "instant")
        self.assertEqual(report["trace"]["deep_apply"]["memory_mode"]["mode"], "deep")
        self.assertTrue(all(report["score"]["direction_coverage"].values()))
        self.assertEqual(report["score"]["scores"]["reproducibility"], 10)
        self.assertEqual(report["score"]["scores"]["user_control_transparency"], 10)


if __name__ == "__main__":
    unittest.main()
