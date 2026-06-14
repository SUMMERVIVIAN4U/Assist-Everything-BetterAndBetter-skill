from __future__ import annotations

from evalharness.quality import augment_case_run


def _turn(
    turn_id: str,
    user: str,
    assistant: str | None = None,
    *,
    applied: list[str] | None = None,
    active: list[dict] | None = None,
    actions: list[dict] | None = None,
    asks: list[str] | None = None,
) -> dict:
    return {
        "id": turn_id,
        "stage": "chat",
        "user": {"content": user},
        "assistant": {
            "content": assistant
            or "推荐方向：小型复古蓝牙音箱。理由：预算合适，能避开已送历史，也能贴合对方的审美偏好；如果想更稳，可以选择支持退换的渠道。备选方向是手作花器或体验类课程，方便按对方兴趣继续收窄。"
        },
        "applied_memories": applied or [],
        "memory_snapshot": {"version": "M1", "active": active or []},
        "tool_calls": [{"output": {"memory_actions": actions or [], "asks": asks or []}}],
    }


def _case(turns: list[dict]) -> dict:
    return {
        "id": "metric_case",
        "title": "metric case",
        "domain": "gift_planning",
        "module": "agent_chat",
        "script": {"source": "agent_chat"},
        "turns": turns,
        "checks": {
            "reset": True,
            "snapshot_count": len(turns),
            "created": 0,
            "updated": True,
            "deleted_filtered": True,
            "show_memory": True,
            "delete_reported": True,
            "round2_applied": False,
            "round3_applied": False,
        },
        "memory_events": [],
    }


def test_effort_uses_small_additive_weights():
    result = augment_case_run(_case([_turn("t1", "给我一个推荐")]))

    effort = result["user_effort"]
    assert effort["final_score"] == 2
    assert effort["memory_saving_points"] == 0
    assert effort["turns"][0]["reasons"] == ["用户轮次 +1", "输入长度 +1"]
    assert "turn_effort_delta" not in effort["turns"][0]
    assert "cumulative_effort" not in effort["turns"][0]
    assert "当前 session" in effort["scale"]


def test_short_concrete_gift_answer_is_deliverable():
    assistant = "女朋友礼物推荐：**紫色丝绸方巾**，200-500元区间。\n\n理由：紫色是她喜欢的颜色，方巾实用且有仪式感。"
    result = augment_case_run(_case([_turn("t1", "给我一个礼物推荐。", assistant=assistant)]))

    effort = result["user_effort"]
    assert effort["final_score"] == 2
    assert effort["turns"][0]["signals"]["serious_error"] is False
    assert "严重错误 +5" not in effort["turns"][0]["reasons"]


def test_memory_saving_points_come_from_applied_memory():
    active = [
        {
            "id": "mem_1",
            "type": "preference",
            "content": "女朋友的礼物偏好/背景：她喜欢紫色；如果是首饰，她喜欢玫瑰金",
        }
    ]

    result = augment_case_run(_case([_turn("t1", "给我一个推荐", applied=["mem_1"], active=active)]))

    effort = result["user_effort"]
    assert effort["final_score"] == 2
    assert effort["memory_saving_points"] == 2
    assert effort["turns"][0]["memory_saving_points"] == ["她喜欢紫色", "如果是首饰，她喜欢玫瑰金"]


def test_memory_saving_points_skip_information_user_repeated_this_turn():
    active = [
        {
            "id": "mem_1",
            "type": "preference",
            "content": "女朋友的礼物偏好/背景：她喜欢紫色；如果是首饰，她喜欢玫瑰金",
        }
    ]

    result = augment_case_run(_case([_turn("t1", "她喜欢紫色，给我一个推荐", applied=["mem_1"], active=active)]))

    effort = result["user_effort"]
    assert effort["memory_saving_points"] == 1
    assert effort["turns"][0]["memory_saving_points"] == ["如果是首饰，她喜欢玫瑰金"]


def test_adding_memory_does_not_count_as_memory_saving():
    result = augment_case_run(
        _case(
            [
                _turn(
                    "t1",
                    "她喜欢紫色，预算1000。",
                    actions=[{"action": "add", "detail": "女朋友的礼物偏好/背景：她喜欢紫色"}],
                )
            ]
        )
    )

    effort = result["user_effort"]
    assert effort["memory_saving_points"] == 0
    assert effort["turns"][0]["saved_delta"] == 0


def test_clarification_adds_effort_cost():
    result = augment_case_run(_case([_turn("t1", "给我一个推荐", asks=["预算是多少？"])]))

    effort = result["user_effort"]
    assert effort["final_score"] == 4
    assert "被追问 +2" in effort["turns"][0]["reasons"]
