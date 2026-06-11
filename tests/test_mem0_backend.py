import json
import unittest
import urllib.error
from io import BytesIO
from unittest.mock import patch

from assist_everything_betterandbetter_skill.mem0_backend import Mem0Client, Mem0Config
from assist_everything_betterandbetter_skill.memory import MemoryItem


class _FakeResponse:
    def __init__(self, body: bytes = b'{"results":[{"id":"remote_1","event":"ADD"}]}'):
        self.body = body

    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


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

    def test_add_text_can_enable_async_mode_for_hosted_bulk_runs(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse()

        client = Mem0Client(
            Mem0Config(
                enabled=True,
                base_url="https://mem0.example",
                api_key="test-key",
                user_id="u1",
            )
        )

        with patch("assist_everything_betterandbetter_skill.mem0_backend.urllib.request.urlopen", fake_urlopen):
            client.add_text("bulk demo memory", context="performance_demo", async_mode=True)

        self.assertTrue(captured["payload"]["infer"])
        self.assertTrue(captured["payload"]["async_mode"])
        self.assertEqual("performance_demo", captured["payload"]["metadata"]["context"])

    def test_request_retries_transient_url_errors(self):
        calls = []

        def fake_urlopen(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise urllib.error.URLError("transient eof")
            return _FakeResponse()

        client = Mem0Client(
            Mem0Config(
                enabled=True,
                base_url="https://mem0.example",
                api_key="test-key",
                user_id="u1",
            )
        )

        with (
            patch("assist_everything_betterandbetter_skill.mem0_backend.urllib.request.urlopen", fake_urlopen),
            patch("assist_everything_betterandbetter_skill.mem0_backend.time.sleep") as sleep,
        ):
            result = client.add_text("bulk demo memory", async_mode=True)

        self.assertEqual({"results": [{"id": "remote_1", "event": "ADD"}]}, result)
        self.assertEqual(2, len(calls))
        sleep.assert_called_once()

    def test_delete_all_falls_back_to_individual_deletes_when_bulk_is_unavailable(self):
        calls = []

        def fake_urlopen(request, timeout):
            calls.append((request.get_method(), request.full_url))
            if request.get_method() == "GET":
                return _FakeResponse(b'{"results":[{"id":"remote_1"},{"id":"remote_2"}]}')
            if "remote_" not in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=BytesIO(b'{"detail":"Not Found"}'))
            return _FakeResponse(b'{}')

        client = Mem0Client(
            Mem0Config(
                enabled=True,
                base_url="https://mem0.example",
                api_key="test-key",
                user_id="u1",
            )
        )

        with patch("assist_everything_betterandbetter_skill.mem0_backend.urllib.request.urlopen", fake_urlopen):
            result = client.delete_all()

        self.assertEqual(result["found_count"], 2)
        self.assertEqual(result["deleted_count"], 2)
        self.assertEqual(result["errors"], [])
        self.assertNotIn("bulk_error", result)
        self.assertEqual(1, len(result["warnings"]))
        self.assertEqual(calls[0][0], "GET")
        self.assertIn("/v1/memories/?user_id=u1", calls[0][1])
        self.assertEqual(result["mode"], "individual")
        individual_deletes = [call for call in calls if "remote_" in call[1]]
        self.assertEqual([call[0] for call in individual_deletes], ["DELETE", "DELETE"])
        self.assertTrue(individual_deletes[0][1].endswith("/v1/memories/remote_1/"))
        self.assertTrue(individual_deletes[1][1].endswith("/v1/memories/remote_2/"))

    def test_delete_all_prefers_user_scoped_bulk_delete(self):
        calls = []

        def fake_urlopen(request, timeout):
            calls.append((request.get_method(), request.full_url))
            if request.get_method() == "GET":
                return _FakeResponse(b'{"results":[{"id":"remote_1"},{"id":"remote_2"}]}')
            return _FakeResponse(b'{}')

        client = Mem0Client(
            Mem0Config(
                enabled=True,
                base_url="https://mem0.example",
                api_key="test-key",
                user_id="u1",
            )
        )

        with patch("assist_everything_betterandbetter_skill.mem0_backend.urllib.request.urlopen", fake_urlopen):
            result = client.delete_all()

        self.assertEqual(result["found_count"], 2)
        self.assertEqual(result["deleted_count"], 2)
        self.assertEqual(result["errors"], [])
        self.assertEqual([call[0] for call in calls], ["GET", "DELETE"])
        self.assertIn("/v1/memories/?user_id=u1", calls[1][1])


if __name__ == "__main__":
    unittest.main()
