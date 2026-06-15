import json

from evalharness.agent import (
    HarnessAgent,
    _conversation_context_blocked_terms,
    _llm_response_is_usable,
    _response_directives,
    _sanitize_llm_output,
    _suppression_context_from_actions,
    _violates_memory_context_constraints,
    _violates_suppression_context,
)


class _SequencedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, temperature=0.3):
        self.calls.append({"messages": messages, "temperature": temperature})
        if self.responses:
            return self.responses.pop(0)
        return ""


class _SemanticClient:
    def __init__(self):
        self.chat_calls = []
        self.json_calls = []

    def chat(self, messages, temperature=0.3):
        self.chat_calls.append({"messages": messages, "temperature": temperature})
        return "推荐方向：拍立得相机。\n- 理由：有生日仪式感。\n- 预算：按当前范围控制。\n- 避开：不要重复已送礼物。"

    def json_chat(self, messages, temperature=0.0):
        self.json_calls.append({"messages": messages, "temperature": temperature})
        return {
            "memories": [
                {
                    "type": "decision",
                    "content": "本次给女朋友的礼物已选定为拍立得",
                    "scope": "gift_planning",
                    "target": "女朋友",
                    "predicate": "selected",
                    "time_scope": "current_task",
                    "confidence": 0.92,
                    "evidence": ["选拍立得"],
                    "tags": ["拍立得"],
                }
            ]
        }


def test_harness_injects_semantic_extractor_for_short_gift_selection():
    client = _SemanticClient()
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)
    agent.reply("帮我给女朋友选个生日礼物。")

    turn = agent.reply("选拍立得")

    actions = turn.tool_calls[0].output["memory_actions"]
    assert client.json_calls
    assert any(action["action"] == "add" and "拍立得" in action["detail"] for action in actions)


def test_sanitize_llm_output_preserves_literal_double_asterisks():
    text = "Python 里 `2 ** 3` 是 8；glob 可以写 `**/*.py`；Markdown 示例：**加粗**。"

    cleaned = _sanitize_llm_output(text)

    assert "`2 ** 3`" in cleaned
    assert "`**/*.py`" in cleaned
    assert "**加粗**" in cleaned


def test_confirm_first_budget_directive_confirms_and_recommends():
    memory_context = {
        "apply_now": [],
        "confirm_first": [
            {
                "type": "constraint",
                "content": "给女朋友选礼物预算在 1000 元左右",
                "reason": "expired_current_task_confirm_first",
            }
        ],
    }

    directives = _response_directives("帮我给女朋友选个生日礼物。", memory_context)

    text = "\n".join(directives)
    assert "不要再问“有预算吗”" in text
    assert "如果这次还沿用这个预算" in text
    assert "直接按该预算给一个推荐" in text


def test_memory_inspection_command_does_not_require_llm_rewrite():
    class Client:
        def chat(self, messages, temperature=0.3):
            raise AssertionError("memory inspection should not call LLM rewrite")

        def json_chat(self, messages, temperature=0.0):
            raise AssertionError("memory inspection should not call LLM extractor")

    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=Client(), persist_memory=False, require_llm=True)

    turn = agent.reply("展示当前记忆")

    assert "当前没有任何记忆" in turn.assistant.content


def test_rewrite_payload_projects_selected_gifts_as_exclusions():
    client = _SemanticClient()
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)
    agent.reply("帮我给女朋友选个生日礼物。")
    agent.reply("选拍立得")

    agent.reply("给我一个礼物推荐。")

    payload = json.loads(client.chat_calls[-1]["messages"][1]["content"])
    exclusions = payload["memory_context"]["gift_selected_exclusions"]
    assert exclusions
    assert any("拍立得" in item["content"] for item in exclusions)
    assert any(item["category"] == "影像设备" for item in exclusions)


def test_rewrite_retries_when_gift_recommendation_only_summarizes_selected_item():
    class Client(_SemanticClient):
        def __init__(self):
            super().__init__()
            self.responses = [
                "初始推荐：拍立得相机。",
                "礼物已选定：拍立得。",
                "礼物已选定，无需重新推荐。剩下唯一待确认是尺寸。",
                "新的礼物推荐：真丝睡衣礼盒。\n预算：约800-1000元。\n避开：不重复已选拍立得。",
            ]

        def chat(self, messages, temperature=0.3):
            self.chat_calls.append({"messages": messages, "temperature": temperature})
            return self.responses.pop(0) if self.responses else ""

    client = Client()
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)
    agent.reply("帮我给女朋友选个生日礼物。")
    agent.reply("选拍立得")

    turn = agent.reply("给我一个礼物推荐。")

    assert len(client.chat_calls) >= 4
    assert "真丝睡衣礼盒" in turn.assistant.content
    assert "无需重新推荐" not in turn.assistant.content


def test_rewrite_retries_fragrance_fallback_for_new_gift_direction():
    class Client(_SemanticClient):
        def __init__(self):
            super().__init__()
            self.responses = [
                "初始推荐：拍立得相机。",
                "礼物已选定：拍立得。",
                "推荐：小众香氛礼盒，预算900元。",
                "新的礼物推荐：真丝睡衣礼盒。\n预算：约800-1000元。\n避开：不重复已选拍立得。",
            ]

        def chat(self, messages, temperature=0.3):
            self.chat_calls.append({"messages": messages, "temperature": temperature})
            return self.responses.pop(0) if self.responses else ""

    client = Client()
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)
    agent.reply("帮我给女朋友选个生日礼物。")
    agent.reply("选拍立得")

    turn = agent.reply("再给一个不重复的礼物方向。")

    assert len(client.chat_calls) >= 4
    assert "真丝睡衣礼盒" in turn.assistant.content
    assert "香氛" not in turn.assistant.content


def test_rewrite_retries_when_selection_turn_recommends_different_gift():
    class Client(_SemanticClient):
        def __init__(self):
            super().__init__()
            self.responses = [
                "推荐：拍立得相册套装。",
                "小众香氛蜡烛礼盒。",
                "已选定：拍立得相册套装。预算按当前范围控制，后续只推进这款。",
            ]

        def chat(self, messages, temperature=0.3):
            self.chat_calls.append({"messages": messages, "temperature": temperature})
            return self.responses.pop(0) if self.responses else ""

    client = Client()
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)
    agent.reply("再给一个推荐。")

    turn = agent.reply("拍立得相册套装")

    assert len(client.chat_calls) >= 3
    assert "拍立得相册套装" in turn.assistant.content
    assert "香氛" not in turn.assistant.content


def test_rewrite_uses_current_selected_memory_action_when_user_names_scarf():
    class Client(_SemanticClient):
        def __init__(self):
            super().__init__()
            self.responses = [
                "1. **紫色丝巾** — 桑蚕丝方巾，1000元内可选。",
                "1000元内可选方向：紫色电动牙刷、紫色吹风机、紫色折叠键盘。",
                "已选定：紫色丝巾。后续就围绕这款推进，不再发散推荐其他方向。",
            ]

        def chat(self, messages, temperature=0.3):
            self.chat_calls.append({"messages": messages, "temperature": temperature})
            return self.responses.pop(0) if self.responses else ""

        def json_chat(self, messages, temperature=0.0):
            self.json_calls.append({"messages": messages, "temperature": temperature})
            payload = json.loads(messages[-1]["content"])
            if payload["user_message"] == "紫色丝巾":
                return {
                    "memories": [
                        {
                            "type": "decision",
                            "content": "本次给女朋友的礼物已选定为紫色丝巾",
                            "scope": "gift_planning",
                            "target": "女朋友",
                            "predicate": "selected",
                            "time_scope": "current_task",
                            "confidence": 0.95,
                            "evidence": ["紫色丝巾"],
                            "tags": ["紫色", "丝巾"],
                        }
                    ]
                }
            return {"memories": []}

    client = Client()
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)
    agent.reply("给我一个礼物推荐。")

    turn = agent.reply("紫色丝巾")

    assert len(client.chat_calls) >= 3
    assert "紫色丝巾" in turn.assistant.content
    assert "电动牙刷" not in turn.assistant.content
    retry_payload = json.loads(client.chat_calls[-1]["messages"][1]["content"])
    assert any("紫色丝巾" in directive for directive in retry_payload["response_directives"])


def test_rewrite_suppresses_deleted_travel_memory_in_followup_plan():
    class Client(_SequencedClient):
        def __init__(self):
            super().__init__(
                [
                    "已记录：孩子喜欢自然和动物；避开人挤人的网红点。",
                    "好的，记忆已删除。\n\n南京半日游：上午红山森林动物园，亮点是孩子能近距离看动物；下午玄武湖散步。",
                    "好的，已删除这条偏好。南京半日游改成：上午南京博物院民国馆，午餐后去玄武湖环洲短线散步；全程避开拥挤打卡点，不再按动物主题安排。",
                ]
            )

    client = Client()
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)
    agent.reply("以后家庭出行请记住：孩子喜欢自然和动物；我不喜欢人挤人的网红点。")

    turn = agent.reply("删除孩子喜欢动物这条记忆。。然后：安排南京半日游。")

    assert len(client.calls) >= 3
    assert "南京博物院" in turn.assistant.content
    assert "红山森林动物园" not in turn.assistant.content
    retry_payload = json.loads(client.calls[-1]["messages"][1]["content"])
    suppressed = retry_payload["suppression_context"]["items"]
    assert any("孩子喜欢自然和动物" in item["content"] for item in suppressed)
    assert any("动物" in item["do_not_assume"] for item in suppressed)


def test_rewrite_keeps_deleted_travel_memory_suppressed_across_followup_turns():
    class Client(_SequencedClient):
        def __init__(self):
            super().__init__(
                [
                    "已记录：孩子喜欢自然和动物；避开人挤人的网红点。",
                    "好的，已删除这条偏好。南京半日游：上午南京博物院，下午玄武湖短线。",
                    "南京半日游：上午红山森林动物园，孩子能看动物；下午中山陵音乐台。",
                    "南京半日游：上午南京博物院民国馆，下午玄武湖情侣园短线，15点返程。",
                    "南京半日游：上午红山森林动物园，孩子能看动物；下午中山陵音乐台。",
                    "南京半日游：上午南京博物院民国馆，下午玄武湖情侣园短线，15点返程。",
                    "南京半日游：上午南京博物院民国馆，下午玄武湖情侣园短线，15点返程。",
                    "南京半日游：上午南京博物院民国馆，下午玄武湖情侣园短线，15点返程。",
                    "南京半日游：上午南京博物院民国馆，下午玄武湖情侣园短线，15点返程。",
                ]
            )

    client = Client()
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)
    agent.reply("以后家庭出行请记住：孩子喜欢自然和动物；我不喜欢人挤人的网红点。")
    agent.reply("删除孩子喜欢动物这条记忆。。然后：安排南京半日游。")

    turn = agent.reply("这次父亲不去，只有我和孩子；避开网红点这条保留，但少步行不适用。")

    assert len(client.calls) >= 4
    assert "南京博物院" in turn.assistant.content
    assert "红山森林动物园" not in turn.assistant.content
    retry_payload = json.loads(client.calls[-1]["messages"][1]["content"])
    suppressed = retry_payload["suppression_context"]["items"]
    assert any("孩子喜欢自然和动物" in item["content"] for item in suppressed)


def test_rewrite_never_falls_back_to_local_draft_when_llm_drops_travel_plan():
    client = _SequencedClient(
        [
            "这个草案是照顾亲子的节奏来规划的。为了把具体路线调得更准，我需要再确认一下：有没有老人、小孩多大？",
            "为了更准，我还需要知道孩子年龄和老人情况。",
        ]
    )
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)

    turn = agent.reply("帮我安排北京周末 2 天亲子旅行。")

    assert len(client.calls) == 2
    first_payload = json.loads(client.calls[0]["messages"][1]["content"])
    assert "tool_draft" not in first_payload
    assert "为了更准" in turn.assistant.content
    assert "北京2天亲子行程" not in turn.assistant.content
    assert "这个草案" not in turn.assistant.content


def test_rewrite_guard_accepts_retry_with_concrete_plan():
    client = _SequencedClient(
        [
            "这个草案是照顾亲子的节奏来规划的。为了把具体路线调得更准，我需要再确认一下：有没有老人、小孩多大？",
            "北京2天亲子行程：\n第 1 天：上午国家植物园，下午中国科技馆。\n第 2 天：上午北京海洋馆，下午就近休息。\n执行约束：少排队、少回头路，每天保留休息时间。",
        ]
    )
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)

    turn = agent.reply("帮我安排北京周末 2 天亲子旅行。")

    assert len(client.calls) == 2
    assert "北京2天亲子行程" in turn.assistant.content
    assert "国家植物园" in turn.assistant.content


def test_rewrite_strips_thinking_blocks_from_visible_output():
    client = _SequencedClient(
        [
            "<think>先判断亲子旅行约束。</think>\n"
            "北京2天亲子行程：\n"
            "第 1 天：上午国家植物园，下午中国科技馆。\n"
            "第 2 天：上午北京海洋馆，下午就近休息。\n"
            "执行约束：少排队、少回头路，每天保留休息时间。"
        ]
    )
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)

    turn = agent.reply("帮我安排北京周末 2 天亲子旅行。")

    assert len(client.calls) == 1
    assert "<think>" not in turn.assistant.content
    assert "先判断" not in turn.assistant.content
    assert "北京2天亲子行程" in turn.assistant.content


def test_rewrite_guard_rejects_truncated_output_and_accepts_retry():
    client = _SequencedClient(
        [
            "南京半日亲子路线：上午红山森林动物园短线，下午玄武湖边休息，就",
            "南京半日亲子路线：上午红山森林动物园短线，下午玄武湖边休息。\n"
            "执行约束：避开网红点，控制步行量，保留打车接驳。"
        ]
    )
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)

    turn = agent.reply("帮我安排南京半日亲子路线。")

    assert len(client.calls) == 2
    assert not turn.assistant.content.endswith("就")
    assert "执行约束" in turn.assistant.content


def test_show_memory_stage_is_not_rewritten():
    client = _SequencedClient(["这不应该被调用"])
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)

    turn = agent.reply("展示当前记忆。", stage="show_memory")

    assert len(client.calls) == 0
    assert "当前" in turn.assistant.content


def test_rewrite_removes_trailing_generic_question():
    client = _SequencedClient(
        [
            "上海1天亲子自然路线：\n"
            "上午：上海动物园早场。\n"
            "下午：共青森林公园。\n"
            "执行约束：避开网红点，午后保留休息。\n"
            "需要我帮你查具体门票和酒店吗？"
        ]
    )
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)

    turn = agent.reply("帮我安排上海 1 天亲子自然路线。")

    assert "上海1天亲子自然路线" in turn.assistant.content
    assert "需要我帮你查" not in turn.assistant.content


def test_rewrite_guard_rejects_unresolved_choice_plan():
    client = _SequencedClient(
        [
            "上海1天亲子自然路线：\n上午：上海动物园。\n下午二选一：A自然博物馆，B滨江森林公园。\n想选下午A还是B",
            "上海1天亲子自然路线：\n上午：上海动物园。\n下午：共青森林公园。\n执行约束：避开网红点，午后留休息时间。",
        ]
    )
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)

    turn = agent.reply("帮我安排上海 1 天亲子自然路线。")

    assert len(client.calls) == 2
    assert "二选一" not in turn.assistant.content
    assert "共青森林公园" in turn.assistant.content


def test_rewrite_guard_rejects_which_direction_plan():
    client = _SequencedClient(
        [
            "南京半日游：方案A玄武湖，方案B中山陵，方案C栖霞山。\n哪个方向？确认后给具体交通时间。",
            "南京半日游：上午玄武湖环湖步道，10点坐游船，12点湖边午餐，13点南京博物院民国馆，15点返程。",
        ]
    )
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)

    turn = agent.reply("安排南京半日游。")

    assert len(client.calls) == 2
    assert "哪个方向" not in turn.assistant.content
    assert "玄武湖" in turn.assistant.content


def test_rewrite_suppression_rejects_pigeon_after_deleted_animal_preference():
    class Client(_SequencedClient):
        def __init__(self):
            super().__init__(
                [
                    "已记录：孩子喜欢自然和动物；避开人挤人的网红点。",
                    "已删除这条偏好。南京半日游：上午南京博物院，下午玄武湖短线。",
                    "南京半日游：上午中山陵音乐台喂鸽子，下午玄武湖游船。",
                    "南京半日游：上午南京博物院民国馆，下午玄武湖游船，15点返程。",
                    "南京半日游：上午中山陵音乐台喂鸽子，下午玄武湖游船。",
                    "南京半日游：上午南京博物院民国馆，下午玄武湖游船，15点返程。",
                    "南京半日游：上午南京博物院民国馆，下午玄武湖游船，15点返程。",
                    "南京半日游：上午南京博物院民国馆，下午玄武湖游船，15点返程。",
                    "南京半日游：上午南京博物院民国馆，下午玄武湖游船，15点返程。",
                ]
            )

    client = Client()
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)
    agent.reply("以后家庭出行请记住：孩子喜欢自然和动物；我不喜欢人挤人的网红点。")
    agent.reply("删除孩子喜欢动物这条记忆。。然后：安排南京半日游。")

    turn = agent.reply("这次父亲不去，只有我和孩子；避开网红点这条保留，但少步行不适用。")

    assert len(client.calls) >= 4
    assert "喂鸽子" not in turn.assistant.content
    assert "南京博物院" in turn.assistant.content


def test_rewrite_guard_rejects_tourist_street_when_avoiding_influencer_spots():
    class Client(_SequencedClient):
        def __init__(self):
            super().__init__(
                [
                        "已记录：避开人挤人的网红点。",
                        "南京半日游：上午中华门城堡，下午老门东历史街区边逛边吃。",
                        "南京半日游：上午南京博物院民国馆，下午玄武湖游船，15点返程。",
                        "南京半日游：上午南京博物院民国馆，下午玄武湖游船，15点返程。",
                        "南京半日游：上午南京博物院民国馆，下午玄武湖游船，15点返程。",
                        "南京半日游：上午南京博物院民国馆，下午玄武湖游船，15点返程。",
                        "南京半日游：上午南京博物院民国馆，下午玄武湖游船，15点返程。",
                        "南京半日游：上午南京博物院民国馆，下午玄武湖游船，15点返程。",
                        "南京半日游：上午南京博物院民国馆，下午玄武湖游船，15点返程。",
                    ]
                )

    client = Client()
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)
    agent.reply("以后家庭出行请记住：我不喜欢人挤人的网红点。")

    turn = agent.reply("安排南京半日游。")

    assert len(client.calls) >= 3
    assert "老门东" not in turn.assistant.content
    assert "南京博物院" in turn.assistant.content


def test_delete_request_itself_creates_suppression_when_backend_has_no_action():
    context = _suppression_context_from_actions(
        [],
        user_text="删除孩子喜欢动物这条记忆。。然后：安排南京半日游。",
        context="",
    )

    assert _violates_suppression_context(
        "南京半日游：下午去红山森林动物园喂鸽子。",
        context,
        user_text="删除孩子喜欢动物这条记忆。。然后：安排南京半日游。",
    )


def test_influencer_spot_constraint_uses_conversation_context():
    assert _violates_memory_context_constraints(
        "南京半日游：下午去老门东吃饭。",
        {},
        user_text="安排南京半日游。",
        conversation_context="以后家庭出行请记住：我不喜欢人挤人的网红点。",
    )
    blocked = _conversation_context_blocked_terms(
        "安排南京半日游。",
        "以后家庭出行请记住：我不喜欢人挤人的网红点。",
    )
    assert "老门东" in blocked


def test_route_task_rejects_short_continue_previous_plan_answer():
    assert not _llm_response_is_usable(
        "已确认删除。玄武湖方案继续适用。",
        user_text="删除孩子喜欢动物这条记忆。。然后：安排南京半日游。",
    )


def test_rewrite_payload_includes_memory_actions_without_tool_draft():
    client = _SequencedClient(["已按你的纠正更新。"])
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)

    agent.reply("帮我给女朋友选个生日礼物。")
    agent.reply("预算1000元左右", stage="feedback")

    payload = json.loads(client.calls[-1]["messages"][1]["content"])
    assert "tool_draft" not in payload
    assert payload["memory_actions"]
