from evalharness.agent import HarnessAgent, _rewrite_is_usable


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


def test_rewrite_guard_falls_back_when_llm_drops_travel_plan():
    client = _SequencedClient(
        [
            "这个草案是照顾亲子的节奏来规划的。为了把具体路线调得更准，我需要再确认一下：有没有老人、小孩多大？",
            "为了更准，我还需要知道孩子年龄和老人情况。",
        ]
    )
    agent = HarnessAgent(llm_mode="deepseek_pro", llm_client=client, persist_memory=False)

    turn = agent.reply("帮我安排北京周末 2 天亲子旅行。")

    assert len(client.calls) == 2
    assert "北京2天亲子行程" in turn.assistant.content
    assert "第 1 天" in turn.assistant.content
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


def test_rewrite_guard_rejects_offer_to_replan_without_plan():
    draft = (
        "北京2天亲子行程：\n"
        "第 1 天：上午国家植物园或奥森北园，下午中国科技馆。\n"
        "第 2 天：上午北京海洋馆，午后找近距离室内休息点。\n"
        "执行约束：控制步行，优先电瓶车；避开人挤人的网红点。"
    )
    rewritten = (
        "好，这些都记住了。以后家庭出行会按这个来调方案。"
        "刚才那个北京周末行程我也会按这三个约束帮你重新调整，要不要我现在就改一版？"
    )

    assert not _rewrite_is_usable(
        "以后家庭出行请记住：父亲膝盖不好，步行要少；孩子喜欢自然和动物；我不喜欢人挤人的网红点。",
        draft,
        rewritten,
    )


def test_rewrite_guard_uses_context_for_task_continuation():
    context = (
        "user: 帮我安排北京周末 2 天亲子旅行。\n"
        "assistant: 北京2天亲子行程：第 1 天上午国家植物园，下午中国科技馆；第 2 天上午北京海洋馆。"
    )
    draft = (
        "北京2天亲子行程：\n"
        "第 1 天：上午国家植物园或奥森北园，下午中国科技馆。\n"
        "第 2 天：上午北京海洋馆，午后找近距离室内休息点。\n"
        "执行约束：控制步行，优先电瓶车；避开人挤人的网红点。"
    )
    rewritten = (
        "好，这些都记住了。以后家庭出行会减少步行，优先安排自然和动物相关体验，"
        "也会避开人挤人的网红点；后续你再让我规划家庭出行时，我会直接按这些规则处理。"
    )

    assert not _rewrite_is_usable(
        "以后家庭出行请记住：父亲膝盖不好，步行要少；孩子喜欢自然和动物；我不喜欢人挤人的网红点。",
        draft,
        rewritten,
        context=context,
    )
