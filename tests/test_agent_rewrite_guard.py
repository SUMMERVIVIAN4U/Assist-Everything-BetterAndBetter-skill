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
