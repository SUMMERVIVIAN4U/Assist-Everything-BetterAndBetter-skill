import unittest
from unittest.mock import patch

from evalharness import server


def _backend_config(backend="local", memory_enabled=True):
    return {
        "backend": backend,
        "memory_enabled": memory_enabled,
        "mem0": {
            "base_url": "https://mem0.example",
            "api_key": "test-key",
            "user_id": "workbench-user",
            "app_id": "test-app",
            "project_id": "project-1",
            "project_name": "test-project",
            "timeout": 15.0,
        },
    }


class WorkbenchCurrentMemoryTest(unittest.TestCase):
    def test_current_memory_payload_uses_local_snapshot_when_local_is_selected(self):
        snapshot = {"version": "M1", "active": [{"content": "用户喜欢先看结论"}]}

        with patch("evalharness.server._memory_backend_config", return_value=_backend_config("local", False)):
            payload = server._current_memory_payload(snapshot)

        self.assertFalse(payload["memory_enabled"])
        self.assertEqual(payload["selected_engine"], "local")
        self.assertEqual(payload["engine_label"], "本地 Markdown / JSON")
        self.assertEqual(payload["content"], snapshot)

    def test_current_memory_payload_uses_mem0_memory_when_mem0_is_selected(self):
        remote = {"ok": True, "count": 1, "memories": [{"memory": "动物园，小孩3-4岁"}]}

        with patch("evalharness.server._memory_backend_config", return_value=_backend_config("mem0", True)):
            with patch("evalharness.server._mem0_memory", return_value=remote):
                payload = server._current_memory_payload({"version": "M1", "active": []})

        self.assertTrue(payload["memory_enabled"])
        self.assertEqual(payload["selected_engine"], "mem0")
        self.assertEqual(payload["engine_label"], "Mem0")
        self.assertEqual(payload["content"], remote)


if __name__ == "__main__":
    unittest.main()
