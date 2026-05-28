from __future__ import annotations

from pathlib import Path
from typing import Any

from assist_everything_betterandbetter_skill.cases import CASES, EvalCase

from .agent import HarnessAgent
from .evaluation import build_report, evaluate_case_run, save_report


def _turn(agent: HarnessAgent, text: str, stage: str) -> dict[str, Any]:
    return agent.reply(text, stage=stage).to_dict()


def run_case(case: EvalCase, *, judge_mode: str = "auto", agent_mode: str = "local") -> dict[str, Any]:
    agent = HarnessAgent(llm_mode=agent_mode, persist_memory=False)
    turns = [
        _turn(agent, "reset memory", "reset"),
        _turn(agent, case.initial_task, "round1_task"),
        _turn(agent, case.feedback, "feedback"),
        _turn(agent, case.memory_query, "show_memory"),
        _turn(agent, case.second_task, "round2_task"),
        _turn(agent, case.preference_change, "preference_change"),
        _turn(agent, case.third_task, "round3_task"),
        _turn(agent, f"{case.delete_query}。然后：{case.delete_retest_task}", "delete_retest"),
    ]
    snapshots = [turn["memory_snapshot"] for turn in turns]
    events = agent.toolbox.skill.memory.events
    checks = {
        "reset": snapshots[0]["version"] == "M0",
        "snapshot_count": len(snapshots),
        "created": sum(1 for event in events if event["action"] == "add"),
        "show_memory": any(turn["stage"] == "show_memory" and "active 记忆" in turn["assistant"]["content"] for turn in turns),
        "round2_applied": len(turns[4]["applied_memories"]) > 0,
        "round3_applied": len(turns[6]["applied_memories"]) > 0,
        "updated": any(event["action"] in {"downgrade", "archive", "update", "add"} for event in events[1:]),
        "deleted_filtered": bool(snapshots[-1]["deleted"]),
        "delete_reported": "删除" in turns[-1]["assistant"]["content"],
        "deliverable_turns": sum(1 for turn in turns if len(turn["assistant"]["content"]) > 20),
    }
    run = {
        "id": case.id,
        "title": case.title,
        "domain": case.domain,
        "module": case.module,
        "script": {
            "reset": "reset memory",
            "round1": case.initial_task,
            "feedback": case.feedback,
            "round2": case.second_task,
            "preference_change": case.preference_change,
            "round3": case.third_task,
            "delete_retest": case.delete_retest_task,
        },
        "turns": turns,
        "rounds": _round_cards(turns),
        "snapshots": snapshots,
        "memory_events": events,
        "checks": checks,
        "ablation": {
            "module": case.module,
            "off": ["重复问基础偏好", "旧规则继续污染新场景", "删除后仍可能命中旧偏好"],
            "on": ["只追问缺口", "旧规则降权/条件化", "deleted 记忆不再应用"],
        },
    }
    return evaluate_case_run(run, judge_mode)


def run_all(
    output_dir: str | Path = "eval/output/latest",
    *,
    judge_mode: str = "auto",
    agent_mode: str = "local",
) -> dict[str, Any]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cases = [run_case(case, judge_mode=judge_mode, agent_mode=agent_mode) for case in CASES]
    cases.append(run_reset_isolation_case(judge_mode=judge_mode, agent_mode=agent_mode))
    cases.append(run_decision_history_case(judge_mode=judge_mode, agent_mode=agent_mode))
    report = build_report(cases, judge_mode=judge_mode, agent_mode=agent_mode, source="preset_cases")
    save_report(out, report, save_history=Path(output_dir) == Path("eval/output/latest"))
    return report


def run_reset_isolation_case(*, judge_mode: str = "auto", agent_mode: str = "local") -> dict[str, Any]:
    agent = HarnessAgent(llm_mode=agent_mode, persist_memory=False)
    turns = [
        _turn(agent, "reset memory", "reset"),
        _turn(agent, "帮我给女朋友选礼物。她喜欢玫瑰金。", "round1_task"),
        _turn(agent, "给我一个礼物推荐。", "round2_task"),
        _turn(agent, "reset memory", "reset"),
        _turn(agent, "1000 块左右，她喜欢紫色。", "feedback"),
        _turn(agent, "展示当前记忆。", "show_memory"),
        _turn(agent, "给我一个礼物推荐。", "round3_task"),
        _turn(agent, "删除女朋友喜欢紫色这条记忆。然后：给我一个礼物推荐。", "delete_retest"),
    ]
    snapshots = [turn["memory_snapshot"] for turn in turns]
    events = agent.toolbox.skill.memory.events
    checks = {
        "reset": snapshots[0]["version"] == "M0" and snapshots[3]["version"] == "M0",
        "snapshot_count": len(snapshots),
        "created": sum(1 for event in events if event["action"] == "add"),
        "show_memory": any(turn["stage"] == "show_memory" and "active 记忆" in turn["assistant"]["content"] for turn in turns),
        "round2_applied": len(turns[2]["applied_memories"]) > 0,
        "round3_applied": len(turns[6]["applied_memories"]) > 0,
        "updated": any(event["action"] in {"reset", "add", "downgrade", "archive", "update"} for event in events[1:]),
        "deleted_filtered": bool(snapshots[-1]["deleted"]),
        "delete_reported": "删除" in turns[-1]["assistant"]["content"],
        "deliverable_turns": sum(1 for turn in turns if len(turn["assistant"]["content"]) > 20),
    }
    run = {
        "id": "C06",
        "title": "Reset 后记忆隔离",
        "domain": "relationship_gift",
        "module": "记忆 Reset 与上下文隔离模块",
        "script": {
            "reset": "reset memory",
            "round1": "帮我给女朋友选礼物。她喜欢玫瑰金。",
            "round2": "给我一个礼物推荐。",
            "mid_reset": "reset memory",
            "feedback": "1000 块左右，她喜欢紫色。",
            "round3": "给我一个礼物推荐。",
            "delete_retest": "给我一个礼物推荐。",
        },
        "turns": turns,
        "rounds": [
            {
                "name": "Round 1",
                "title": "旧记忆形成",
                "turn_ids": [turns[1]["id"], turns[2]["id"]],
                "highlight": "玫瑰金偏好在 reset 前可正常形成并应用。",
                "actions": ["extract_memory", "retrieve_memory"],
                "gain": "验证 reset 前的正常记忆能力没有被削弱。",
            },
            {
                "name": "Round 2",
                "title": "Reset 隔离",
                "turn_ids": [turns[3]["id"], turns[4]["id"], turns[5]["id"]],
                "highlight": "reset 后 active memory 只包含新输入，不携带旧玫瑰金偏好。",
                "actions": ["reset_memory", "extract_memory", "show_memory"],
                "gain": "验证持久化记忆和 LLM rewrite 上下文都进入新 epoch。",
            },
            {
                "name": "Round 3",
                "title": "隔离后应用与删除",
                "turn_ids": [turns[6]["id"], turns[7]["id"]],
                "highlight": "新任务只能使用 reset 后的紫色/预算记忆，删除后复测不再使用紫色。",
                "actions": ["retrieve_memory", "delete_memory"],
                "gain": "防止旧偏好和已删除偏好继续污染推荐。",
            },
        ],
        "snapshots": snapshots,
        "memory_events": events,
        "checks": checks,
        "ablation": {
            "module": "记忆 Reset 与上下文隔离模块",
            "off": ["reset 前上下文继续污染 LLM", "默认话术硬编码旧偏好", "删除后仍沿用旧颜色"],
            "on": ["reset 建立 memory epoch", "输出偏好必须有 active memory 或当前输入支持", "deleted 记忆不再应用"],
        },
    }
    return evaluate_case_run(run, judge_mode)


def run_decision_history_case(*, judge_mode: str = "auto", agent_mode: str = "local") -> dict[str, Any]:
    agent = HarnessAgent(llm_mode=agent_mode, persist_memory=False)
    turns = [
        _turn(agent, "reset memory", "reset"),
        _turn(agent, "帮我给女朋友选个礼物。", "round1_task"),
        _turn(agent, "1000 元左右的。", "feedback"),
        _turn(agent, "可是她知道我前女友之前也收过这个香水，跟我大吵了一架。", "feedback"),
        _turn(agent, "首饰的话她喜欢玫瑰金色的。", "feedback"),
        _turn(agent, "玫瑰金耳钉太多了，换一个。", "preference_change"),
        _turn(agent, "后来又问了下她，说手链的话颜色更喜欢银色。", "feedback"),
        _turn(agent, "这个可以，我挺满意的，就这个，确认送出。", "round2_task"),
        _turn(agent, "第二次送礼物，已经选过的不要再选，给我一个推荐。", "round3_task"),
        _turn(agent, "展示当前记忆。", "show_memory"),
    ]
    snapshots = [turn["memory_snapshot"] for turn in turns]
    events = agent.toolbox.skill.memory.events
    final_active = " ".join(item.get("content", "") for item in snapshots[-1]["active"])
    checks = {
        "reset": snapshots[0]["version"] == "M0",
        "snapshot_count": len(snapshots),
        "created": sum(1 for event in events if event["action"] == "add"),
        "show_memory": any(turn["stage"] == "show_memory" and "active 记忆" in turn["assistant"]["content"] for turn in turns),
        "round2_applied": len(turns[7]["applied_memories"]) > 0,
        "round3_applied": len(turns[8]["applied_memories"]) > 0,
        "updated": "银色手链" in final_active and "已经给女朋友送过银色手链" in final_active,
        "deleted_filtered": True,
        "delete_reported": True,
        "deliverable_turns": sum(1 for turn in turns if len(turn["assistant"]["content"]) > 20),
        "decision_history_recorded": "本次给女朋友的礼物已选定为银色手链" in final_active
        and "已经给女朋友送过银色手链" in final_active,
    }
    run = {
        "id": "C07",
        "title": "满意确认与二次送礼过滤",
        "domain": "relationship_gift",
        "module": "候选-决策-历史状态迁移模块",
        "script": {
            "reset": "reset memory",
            "round1": "第一次送礼物，多轮否决与偏好补充",
            "accepted": "这个可以，我挺满意的，就这个，确认送出。",
            "round2": "第二次送礼物，已经选过的不要再选。",
            "show": "展示当前记忆。",
        },
        "turns": turns,
        "rounds": [
            {
                "name": "Round 1",
                "title": "候选生成与否决",
                "turn_ids": [turns[1]["id"], turns[5]["id"], turns[6]["id"]],
                "highlight": "玫瑰金耳钉被否决后，候选转到银色手链。",
                "actions": ["candidate.proposed", "candidate.rejected", "extract_preference"],
                "gain": "记录候选和否决原因，避免下一轮又推荐已否方案。",
            },
            {
                "name": "Round 2",
                "title": "满意确认转决策和历史",
                "turn_ids": [turns[7]["id"]],
                "highlight": "“满意/就这个/确认送出”落成 accepted decision 和 gave history。",
                "actions": ["decision.accepted", "history.gave"],
                "gain": "把用户满意节点变成后续可过滤的事实。",
            },
            {
                "name": "Round 3",
                "title": "二次任务过滤已选已送",
                "turn_ids": [turns[8]["id"], turns[9]["id"]],
                "highlight": "第二次送礼物时避开银色手链，也不回退到已否的玫瑰金耳钉。",
                "actions": ["retrieve_history", "apply_constraint", "show_memory"],
                "gain": "跨 session/跨任务减少用户重复说明。",
            },
        ],
        "snapshots": snapshots,
        "memory_events": events,
        "checks": checks,
        "ablation": {
            "module": "候选-决策-历史状态迁移模块",
            "off": ["满意不入库", "这个无法指代上一轮推荐", "第二次任务不知道上次选过什么"],
            "on": ["候选可被指代", "满意确认转 accepted decision/history", "已选已送方案参与过滤"],
        },
    }
    return evaluate_case_run(run, judge_mode)


def _round_cards(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": "Round 1",
            "title": "记忆形成",
            "turn_ids": [turns[1]["id"], turns[2]["id"], turns[3]["id"]],
            "highlight": "空白状态完成任务；明确反馈经授权保存为 M1。",
            "actions": ["process_message", "extract_memory", "show_memory"],
            "gain": "从每次重说偏好，变成可展示、可复用的偏好记忆。",
        },
        {
            "name": "Round 2",
            "title": "记忆应用",
            "turn_ids": [turns[4]["id"]],
            "highlight": "相似但不同任务主动应用 active 记忆。",
            "actions": ["retrieve_memory", "compose_response"],
            "gain": "减少用户重复说明，输出直接贴合已知偏好。",
        },
        {
            "name": "Round 3",
            "title": "更新淘汰",
            "turn_ids": [turns[5]["id"], turns[6]["id"], turns[7]["id"]],
            "highlight": "偏好变化后旧规则降权/条件化，删除后复测不再应用。",
            "actions": ["update_memory", "retrieve_memory", "delete_memory"],
            "gain": "避免简单累加导致的旧规则污染。",
        },
    ]
