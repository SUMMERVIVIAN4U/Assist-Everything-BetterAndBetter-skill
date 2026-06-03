import json
import unittest
from unittest.mock import patch

from assist_everything_betterandbetter_skill.mem0_backend import Mem0Client, Mem0Config
from assist_everything_betterandbetter_skill.memory import MemoryItem


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b'{"results":[{"id":"remote_1","event":"ADD"}]}'


class Mem0BackendTest(unittest.TestCase):
    def test_add_disables_async_mode_when_infer_is_false(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeResponse()

        client = Mem0Client(
            Mem0Config(
                enabled=True,
                base_url="https://mem0.example",
                api_key="test-key",
                user_id="u1",
                app_id="app1",
                project_id="project1",
                timeout=3,
            )
        )
        item = MemoryItem("preference", "用户喜欢先看结论", "general")

        with patch("assist_everything_betterandbetter_skill.mem0_backend.urllib.request.urlopen", fake_urlopen):
            client.add(item)

        self.assertEqual(captured["timeout"], 3)
        self.assertFalse(captured["payload"]["infer"])
        self.assertFalse(captured["payload"]["async_mode"])
        self.assertEqual(captured["payload"]["user_id"], "u1")
        self.assertEqual(captured["payload"]["metadata"]["project_id"], "project1")


if __name__ == "__main__":
    unittest.main()
