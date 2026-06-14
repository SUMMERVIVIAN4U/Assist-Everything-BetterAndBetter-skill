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
        self.assertEqual(payload["engine_label"], "本地JSON")
        self.assertEqual(payload["content"], snapshot)

    def test_current_memory_payload_uses_mem0_memory_when_mem0_is_selected(self):
        remote = {"ok": True, "count": 1, "memories": [{"memory": "动物园，小孩3-4岁"}]}

        with patch("evalharness.server._memory_backend_config", return_value=_backend_config("mem0", True)):
            with patch("evalharness.server._mem0_memory", return_value=remote):
                payload = server._current_memory_payload({"version": "M1", "active": []})

        self.assertTrue(payload["memory_enabled"])
        self.assertEqual(payload["selected_engine"], "mem0")
        self.assertEqual(payload["engine_label"], "Mem0 Hosted")
        self.assertEqual(payload["content"], remote)

    def test_memory_store_payload_can_inspect_local_without_changing_selected_backend(self):
        snapshot = {"version": "M2", "active": [{"content": "本地偏好"}]}

        with patch("evalharness.server._memory_backend_config", return_value=_backend_config("mem0_hosted", True)):
            payload = server._memory_store_payload("local", snapshot)

        self.assertTrue(payload["memory_enabled"])
        self.assertEqual(payload["selected_engine"], "mem0_hosted")
        self.assertEqual(payload["engine"], "local")
        self.assertEqual(payload["engine_label"], "本地Memory")
        self.assertEqual(payload["content"], snapshot)

    def test_memory_store_payload_can_inspect_hosted(self):
        hosted = {"ok": True, "count": 1, "memories": [{"memory": "hosted"}]}

        with patch("evalharness.server._memory_backend_config", return_value=_backend_config("local", True)):
            with patch("evalharness.server._mem0_memory_for_engine", return_value=hosted) as mem0_memory:
                hosted_payload = server._memory_store_payload("mem0_hosted", {"active": []})

        self.assertEqual(hosted_payload["selected_engine"], "local")
        self.assertEqual(hosted_payload["engine"], "mem0_hosted")
        self.assertEqual(hosted_payload["engine_label"], "Mem0 Hosted")
        self.assertEqual(hosted_payload["content"], hosted)
        mem0_memory.assert_called_once_with("mem0_hosted")

    def test_memory_store_payload_rejects_unknown_engine(self):
        with self.assertRaises(ValueError):
            server._memory_store_payload("mem0", {"active": []})

    def test_memory_store_payload_rejects_mem0_sdk(self):
        with self.assertRaises(ValueError):
            server._memory_store_payload("mem0_sdk", {"active": []})

    def test_mem0_health_rejects_sdk_alias_to_local(self):
        with patch("evalharness.server._memory_backend_config", return_value=_backend_config("local", True)):
            with patch("evalharness.server._mem0_client_for_backend") as client_factory:
                payload = server._mem0_health("mem0_sdk")

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["stage"], "config")
        self.assertEqual(payload["backend"]["backend"], "local")
        self.assertEqual(payload["error"], "Mem0 is not selected")
        client_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
