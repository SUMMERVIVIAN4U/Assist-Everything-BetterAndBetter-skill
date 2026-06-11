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

    def test_workbench_memory_backend_options_use_clear_chinese_labels(self):
        html = Path("evalharness/static/workbench.html").read_text(encoding="utf-8")
        required = [
            'onchange="previewMemoryBackendSelection()"',
            'id="checkMem0Button"',
            '<option value="local">本地JSON</option>',
            '<option value="mem0_hosted">Mem0 Hosted</option>',
            '<option value="mem0_sdk">Mem0 SDK</option>',
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, html)

    def test_settings_page_has_independent_memory_store_tabs(self):
        html = Path("evalharness/static/workbench.html").read_text(encoding="utf-8")
        js = Path("evalharness/static/workbench.js").read_text(encoding="utf-8")
        required_html = [
            "本地Memory",
            "Mem0 Hosted",
            "Mem0 SDK",
            'id="settingsLocalMemory"',
            'id="settingsMem0HostedMemory"',
            'id="settingsMem0SdkMemory"',
        ]
        for token in required_html:
            with self.subTest(token=token):
                self.assertIn(token, html)
        self.assertIn("/api/memory-store?engine=local", js)
        self.assertIn("/api/memory-store?engine=mem0_hosted", js)
        self.assertIn("/api/memory-store?engine=mem0_sdk", js)

    def test_memory_backend_selection_updates_preview_and_health_check(self):
        js = Path("evalharness/static/workbench.js").read_text(encoding="utf-8")
        required = [
            "function previewMemoryBackendSelection()",
            "document.getElementById('checkMem0Button')",
            "Check Mem0 Hosted",
            "Check Mem0 SDK",
            "无需 Check Mem0",
            "/api/mem0-health?engine=",
        ]
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, js)

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
