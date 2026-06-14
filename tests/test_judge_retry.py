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


class _CapturingJudgeClient:
    def __init__(self):
        self.messages = None

    def json_chat(self, messages, temperature=0.0):
        self.messages = messages
        return {
            "scores": {
                "reproducibility": 10,
                "memory_extraction": 20,
                "memory_application": 25,
                "update_and_decay": 20,
                "transparency": 10,
                "result_quality": 15,
            },
            "reasons": {"result_quality": "用户主动换品类，不是 agent 纠错成本。"},
            "effort_review": {
                "session_effort_judgement": "final_score 主要来自用户主动选择和细化。",
                "agent_induced_corrections": [],
                "user_driven_refinements": [
                    {"turn": "turn_002", "reason": "用户换非首饰品类属于主动细化。"}
                ],
                "certified_memory_savings": [
                    {"memory": "预算1000元左右", "turn": "turn_001", "reason": "本 session 未重复说明且被使用。"}
                ],
                "rejected_memory_savings": [
                    {"memory": "本轮刚选定拍立得", "reason": "本 session 刚创建，不能算节省。"}
                ],
            },
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


def test_llm_judge_includes_session_effort_memory_rubric():
    client = _CapturingJudgeClient()
    judge = LLMJudge("mimo", client=client)
    case_run = {
        "id": "case",
        "title": "case",
        "module": "module",
        "script": {"source": "agent_chat"},
        "rounds": [],
        "checks": {},
        "user_effort": {"final_score": 12, "memory_saving_points": 4},
        "quality": {},
        "memory_events": [],
        "turns": [
            {
                "stage": "turn_002",
                "user": {"content": "不是，我想换个非首饰品类。"},
                "assistant": {"content": "可以，换成万事利丝巾。"},
                "tool_calls": [],
                "applied_memories": [],
                "memory_snapshot": {"active": []},
            }
        ],
    }

    result = judge.score(case_run)

    assert result["scores"]["total"] == 100
    assert result["effort_review"]["agent_induced_corrections"] == []
    assert "用户自然缩小范围" in client.messages[0]["content"]
    assert "effort_review" in client.messages[0]["content"]
    payload = json.loads(client.messages[1]["content"])
    assert payload["session_eval_policy"]["eval_unit"].startswith("一次 Agent Chat Eval")
    assert payload["turns"][0]["stage"] == "turn_002"
