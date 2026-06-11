import unittest
from pathlib import Path


class SkillMarkdownContractTest(unittest.TestCase):
    def setUp(self):
        self.text = Path("skill/SKILL.md").read_text(encoding="utf-8")

    def test_workbench_features_are_documented_in_skill(self):
        required = [
            "Agent Chat",
            "History Evals",
            "Workbench Memory",
            "Mem0 Memory",
            "当前 Memory",
            "记忆功能",
            "隐私设置",
            "/api/current-memory",
            "LocalMemoryStore",
            "HostedMem0Client",
            "Mem0SdkClient",
            "mutually exclusive",
            "mem0_sdk",
            "user_id",
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, self.text)

    def test_self_improvement_loop_is_documented_in_skill(self):
        required = [
            ".learnings/",
            "LEARNINGS.md",
            "ERRORS.md",
            "FEATURE_REQUESTS.md",
            "从错误中学习",
            "在经验中成长",
            "Recurring Pattern",
            "Promotion",
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, self.text)


if __name__ == "__main__":
    unittest.main()
