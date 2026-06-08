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
    report = build_report(cases, judge_mode=judge_mode, agent_mode=agent_mode, source="preset_cases")
    save_report(out, report, save_history=Path(output_dir) == Path("eval/output/latest"))
    return report


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
