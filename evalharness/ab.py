from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

from .agent import HarnessAgent
from .schemas import HarnessSession, Message, ToolCall, TurnTrace
from .tools import MemoryToolbox


GIFT_SCRIPT_PATH = "eval/scripts/relationship_gift_two_session.md"


@dataclass(frozen=True)
class ScriptStep:
    session: str
    user: str
    stage: str = "relationship_gift"


GIFT_SCRIPT: list[ScriptStep] = [
    ScriptStep("session1", "reset memory", "reset"),
    ScriptStep("session1", "帮我给女朋友选个礼物。"),
    ScriptStep("session1", "1000 元左右的。"),
    ScriptStep("session1", "可是她知道我前女友之前也收过这个香水，跟我大吵了一架。"),
    ScriptStep("session1", "首饰的话她喜欢玫瑰金色的。"),
    ScriptStep("session1", "玫瑰金耳钉太多了，换一个。"),
    ScriptStep("session1", "后来又问了下她，说手链的话颜色更喜欢银色。"),
    ScriptStep("session1", "这个可以，我挺满意的，就这个，确认送出。"),
    ScriptStep("session2", "第二次送礼物，已经选过的不要再选，给我一个推荐。"),
]


BASELINE_EXTRA_EXPLANATION = "上次已经选了银色手链，别再选这个，也别推荐玫瑰金耳钉。"


def run_gift_ab_script(agent_mode: str = "local") -> dict[str, Any]:
    memory_result = _run_memory_path(agent_mode)
    baseline_result = _run_baseline_path()
    memory_effort = _effort_for_path(memory_result["turns"])
    baseline_effort = _effort_for_path(baseline_result["turns"])
    return {
        "script": {
            "name": "relationship_gift_two_session",
            "path": GIFT_SCRIPT_PATH,
            "steps": [step.__dict__ for step in GIFT_SCRIPT],
        },
        "summary": {
            "memory_user_effort": memory_effort["score"],
            "baseline_user_effort": baseline_effort["score"],
            "effort_saved": baseline_effort["score"] - memory_effort["score"],
            "memory_second_session_effort": memory_effort["second_session_score"],
            "baseline_second_session_effort": baseline_effort["second_session_score"],
            "second_session_effort_saved": baseline_effort["second_session_score"] - memory_effort["second_session_score"],
            "memory_user_turns": memory_effort["user_turns"],
            "baseline_user_turns": baseline_effort["user_turns"],
            "turns_saved": baseline_effort["user_turns"] - memory_effort["user_turns"],
            "memory_second_session_extra_explanations": memory_effort["second_session_repeated_explanations"],
            "baseline_second_session_extra_explanations": baseline_effort["second_session_repeated_explanations"],
            "memory_violations": memory_effort["violations"],
            "baseline_violations": baseline_effort["violations"],
            "winner": "memory"
            if memory_effort["second_session_score"] < baseline_effort["second_session_score"]
            else "baseline",
        },
        "rules": USER_EFFORT_COMPARISON_RULES,
        "memory": {
            **memory_result,
            "effort": memory_effort,
        },
        "baseline": {
            **baseline_result,
            "effort": baseline_effort,
        },
    }


def _run_memory_path(agent_mode: str) -> dict[str, Any]:
    toolbox = MemoryToolbox(persist=False)
    session1 = HarnessAgent(name="gift-memory-session1", toolbox=toolbox, llm_mode=agent_mode)
    session2 = HarnessAgent(name="gift-memory-session2", toolbox=toolbox, llm_mode=agent_mode)
    turns: list[dict[str, Any]] = []
    for step in GIFT_SCRIPT:
        agent = session1 if step.session == "session1" else session2
        turn = agent.reply(step.user, stage=step.stage).to_dict()
        turn["script_session"] = step.session
        turns.append(turn)
    return {
        "label": "Memory Agent",
        "description": "使用 skill memory；第二轮新 session 只继承持久记忆，不继承第一轮 transcript。",
        "turns": turns,
        "final_memory": toolbox.snapshot(),
    }


def _run_baseline_path() -> dict[str, Any]:
    session1 = NoSkillGiftBaseline("baseline-session1")
    session2 = NoSkillGiftBaseline("baseline-session2")
    turns: list[dict[str, Any]] = []
    for step in GIFT_SCRIPT:
        agent = session1 if step.session == "session1" else session2
        turn = agent.reply(step.user, stage=step.stage).to_dict()
        turn["script_session"] = step.session
        turns.append(turn)

    first_second_reply = turns[-1]["assistant"]["content"]
    if _needs_repeated_explanation(first_second_reply):
        extra = session2.reply(BASELINE_EXTRA_EXPLANATION, stage="relationship_gift").to_dict()
        extra["script_session"] = "session2"
        extra["injected_for_comparison"] = True
        turns.append(extra)

    return {
        "label": "No-skill Baseline",
        "description": "不使用 skill memory；第二轮新 session 不知道第一轮最终选定了什么。",
        "turns": turns,
        "final_memory": {"active": [], "superseded": [], "deleted": [], "version": "NO_MEMORY"},
    }


class NoSkillGiftBaseline:
    def __init__(self, name: str) -> None:
        self.name = name
        self.session = HarnessSession()

    def reply(self, user_text: str, *, stage: str) -> TurnTrace:
        response = self._compose(user_text)
        user = Message(role="user", content=user_text)
        assistant = Message(role="assistant", content=response)
        self.session.messages.extend([user, assistant])
        turn = TurnTrace(
            id=f"turn_{len(self.session.turns) + 1:03d}",
            stage=stage,
            user=user,
            assistant=assistant,
            tool_calls=[
                ToolCall(
                    name="no_skill_baseline",
                    input={"message": user_text},
                    output={
                        "memory_actions": [],
                        "applied_memories": [],
                        "asks": ["上次选过什么？"] if "已经选过的不要再选" in user_text else [],
                    },
                )
            ],
            applied_memories=[],
            memory_snapshot={"active": [], "superseded": [], "deleted": [], "version": "NO_MEMORY"},
            notes=["baseline 不使用 skill memory"],
        )
        self.session.turns.append(turn)
        return turn

    def _compose(self, user_text: str) -> str:
        text = user_text.strip()
        context = "\n".join(message.content for message in self.session.messages[-8:])
        if "reset memory" in text.lower():
            return "我这边不使用持久化记忆，只重置当前对话。"
        if "已经选过的不要再选" in text:
            return "可以，但我不知道上次具体选了什么。你告诉我上次选定的礼物后，我再避开它推荐一个新的。"
        if "上次已经选了银色手链" in text:
            return "那这次推荐：小众珍珠发夹。预算 1000 元内，避开银色手链和玫瑰金耳钉，也不是香水方向。"
        if "确认送出" in text or "就这个" in text:
            return "好，就按银色手链定。"
        if "手链" in text and "银色" in text:
            return "推荐：银色手链。理由：贴合她对手链颜色的偏好，预算也更好控制。"
        if "玫瑰金耳钉太多" in text or "换一个" in text:
            return "那换成银色手链。它和耳钉不是同一个具体款式，也更日常。"
        if "玫瑰金" in text and "首饰" in text:
            return "推荐：玫瑰金耳钉。预算 1000 元左右能买到质感不错的款。"
        if "前女友" in text or "香水" in text or "香氛" in text:
            return "那避开香水，推荐：真丝小方巾。预算友好，也不会踩前女友相关的雷。"
        if "1000" in text:
            return "1000 元左右的话，先推荐小众香氛礼盒，预算合适，也比较有仪式感。"
        if "礼物" in text and "女朋友" in text:
            return "推荐：小众香氛礼盒。它比较容易显得用心，价格也能按预算调整。"
        if "银色手链" in context:
            return "推荐：银色手链。"
        return "我会直接给一个具体推荐：真丝小方巾。"


USER_EFFORT_COMPARISON_RULES = [
    {
        "name": "用户轮数",
        "weight": "每轮 +10",
        "description": "从开始选礼物到满意完成，用户必须多说一轮，就多一段成本。",
    },
    {
        "name": "输入字数",
        "weight": "每 30 字 +1",
        "description": "用户解释越长，费力度越高；这是补充说明成本。",
    },
    {
        "name": "纠错/换方向",
        "weight": "每次 +15",
        "description": "用户需要指出不满意、换一个、不要再送，说明 agent 没有一次命中。",
    },
    {
        "name": "重复解释",
        "weight": "每次 +20",
        "description": "第二个 session 还要用户重复上次已选/已送的事实，是 memory 应该节省的核心成本。",
    },
    {
        "name": "需要追问",
        "weight": "每次 +12",
        "description": "agent 因缺少记忆而反问上次选了什么，会增加用户操作成本。",
    },
    {
        "name": "违反已知约束",
        "weight": "每次 +25",
        "description": "重复推荐已送、已否定或被污染的品类，是最高成本信号。",
    },
]


def _effort_for_path(turns: list[dict[str, Any]]) -> dict[str, Any]:
    score = 0
    second_session_score = 0
    trace: list[dict[str, Any]] = []
    user_turns = 0
    corrections = 0
    repeated = 0
    second_session_repeated = 0
    clarification_asks = 0
    violations = 0
    input_chars = 0

    for turn in turns:
        user = turn.get("user", {}).get("content", "")
        assistant = turn.get("assistant", {}).get("content", "")
        if user.strip().lower() == "reset memory":
            continue
        before = score
        user_turns += 1
        input_chars += len(user)
        reasons = ["用户轮数 +10"]
        delta = 10
        char_cost = ceil(len(user) / 30)
        delta += char_cost
        reasons.append(f"输入字数 +{char_cost}")

        if _is_correction(user):
            corrections += 1
            delta += 15
            reasons.append("纠错/换方向 +15")
        if _is_repeated_explanation(user, turn):
            repeated += 1
            if turn.get("script_session") == "session2":
                second_session_repeated += 1
            delta += 20
            reasons.append("重复解释 +20")
        if _asks_for_missing_memory(assistant, turn):
            clarification_asks += 1
            delta += 12
            reasons.append("缺记忆追问 +12")
        bad = _gift_violation(assistant, turn)
        if bad:
            violations += 1
            delta += 25
            reasons.append(f"违反约束 +25：{bad}")

        score += delta
        if turn.get("script_session") == "session2":
            second_session_score += delta
        trace.append(
            {
                "turn_id": turn.get("id"),
                "session": turn.get("script_session", ""),
                "user": user,
                "assistant_brief": assistant[:160],
                "before": before,
                "delta": delta,
                "after": score,
                "reasons": reasons,
            }
        )

    return {
        "scale": "lower is less user effort; paired A/B score is additive",
        "score": score,
        "second_session_score": second_session_score,
        "user_turns": user_turns,
        "input_chars": input_chars,
        "corrections": corrections,
        "repeated_explanations": repeated,
        "second_session_repeated_explanations": second_session_repeated,
        "clarification_asks": clarification_asks,
        "violations": violations,
        "trace": trace,
    }


def _needs_repeated_explanation(reply: str) -> bool:
    return "不知道上次" in reply or "上次具体选了什么" in reply or "告诉我上次" in reply


def _is_correction(text: str) -> bool:
    return any(token in text for token in ["太多了", "换一个", "不是", "不满意", "为什么"])


def _is_repeated_explanation(text: str, turn: dict[str, Any]) -> bool:
    if turn.get("injected_for_comparison"):
        return True
    return any(token in text for token in ["上次已经", "我之前说过", "已经选了", "已经送过"])


def _asks_for_missing_memory(reply: str, turn: dict[str, Any]) -> bool:
    asks = (turn.get("tool_calls") or [{}])[0].get("output", {}).get("asks", [])
    asks_for_prior_choice = any("上次" in str(ask) or "选过" in str(ask) for ask in asks)
    return asks_for_prior_choice or _needs_repeated_explanation(reply)


def _gift_violation(reply: str, turn: dict[str, Any]) -> str:
    if turn.get("script_session") != "session2":
        return ""
    text = (
        reply.replace("避开银色手链和玫瑰金耳钉", "")
        .replace("避开银色手链", "")
        .replace("避开玫瑰金耳钉", "")
        .replace("别推荐玫瑰金耳钉", "")
        .replace("不是香水方向", "")
        .replace("不是香水", "")
    )
    if "推荐" not in text:
        return ""
    if "银色手链" in text:
        return "重复已选银色手链"
    if "玫瑰金耳钉" in text:
        return "回退到已否候选玫瑰金耳钉"
    if "香水" in text or "香氛" in text:
        return "回到前女友事件污染的香水/香氛"
    return ""
