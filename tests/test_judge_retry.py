import json

from evalharness.judge import LLMJudge


class _FlakyJudgeClient:
    def __init__(self):
        self.calls = 0

    def json_chat(self, messages, temperature=0.0):
        self.calls += 1
        if self.calls == 1:
            raise json.JSONDecodeError("bad json", "{", 0)
        return {
            "scores": {
                "reproducibility": 10,
                "memory_extraction": 20,
                "memory_application": 25,
                "update_and_decay": 20,
                "transparency": 10,
                "result_quality": 15,
            },
            "reasons": {},
        }


def test_llm_judge_retries_json_parse_errors(monkeypatch):
    monkeypatch.setenv("EVALHARNESS_JUDGE_RETRIES", "2")
    client = _FlakyJudgeClient()
    judge = LLMJudge("mimo", client=client)
    case_run = {
        "id": "case",
        "title": "case",
        "module": "module",
        "script": {},
        "rounds": [],
        "checks": {},
        "user_effort": {},
        "quality": {},
        "memory_events": [],
        "turns": [],
    }

    result = judge.score(case_run)

    assert client.calls == 2
    assert result["scores"]["total"] == 100
