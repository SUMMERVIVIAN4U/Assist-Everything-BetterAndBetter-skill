---
name: apriday-self-Improving
description: Use this skill when a user wants an AI assistant to become more useful over repeated interactions by remembering authorized preferences, applying them in later tasks, updating or deleting memory on request, and producing auditable self-improvement evaluation evidence.
---

# apriday-self-Improving

Use this skill to make an assistant "越用越懂你" while keeping memory explicit, testable, and reversible. The memory loop now includes Bio-Memory Pro inspired proactive detection: the user no longer has to say "记住这个" every time.

## Core Workflow

1. Start from a known state.
   - Run `python3 scripts/apriday_self_improving.py reset` for a clean eval.
   - Run `python3 scripts/apriday_self_improving.py view` before and after meaningful interactions.

2. Observe user feedback.
   - Use `observe "<user feedback>" --approve` when the user explicitly authorizes memory or asks you to remember something.
   - Use `observe "<message>"` for normal conversation. The detector will auto-record high-confidence durable signals, ask/confirm medium-confidence signals, and ignore weak signals.
   - Save only durable preferences, workflow rules, scene rules, project context, todos, decisions, and stable contact facts.
   - Do not save temporary instructions such as "这次", "本次", "今天", "临时", or one-off output format requests.

3. Apply memory to a new task.
   - Run `apply "<task>"` to retrieve active relevant memories.
   - Explain which memories changed the plan and what user effort was reduced.
   - If memory is stale or conflicting, prefer asking a narrow clarification over blindly applying it.

4. Update memory when preferences change.
   - If new approved feedback contradicts old memory, keep the new item active and mark the old one `superseded`.
   - Record evidence and reason for the transition.

5. Respect user control.
   - Use `delete <memory_id>` when the user asks to forget a memory.
   - Deleted memory must not affect future `apply` results.
   - Use `edit <memory_id> --content ...` only for user-approved corrections.

6. Evaluate automatically.
   - Run `python3 scripts/apriday_self_improving.py evaluate`.
   - The evaluation must include reset, first task, feedback, view memory, second task, preference change, third task, and deletion replay.

## Proactive Memory Policy

- High confidence `>= 0.8`: auto-record with `approval: auto_high_confidence`, then mention it naturally.
- Medium confidence `0.5-0.8`: do not write memory yet; return a natural confirmation prompt.
- Low confidence `< 0.5`: ask lightly or ignore, depending on signal strength.
- Duplicate active memories are rejected by content hash.
- Simple messages such as `[q] 你好` use instant mode and skip memory loading.
- Ordinary tasks use standard mode with snapshot plus matching active memories.
- Deep/history requests use deep mode with snapshot, matching memories, and event log.

## Memory Rules

- Prefer fewer, higher-quality memories over broad accumulation.
- Include evidence, scope, confidence, and status for each memory.
- Use `global` scope only for stable cross-task preferences.
- Use task-specific scope for workflow habits such as "architecture planning" or "gift selection".
- Never store secrets, credentials, payment data, health identifiers, or content the user marks as private.

## Commands

```bash
python3 scripts/apriday_self_improving.py reset
python3 scripts/apriday_self_improving.py observe "我特别喜欢先看结论再看细节。"
python3 scripts/apriday_self_improving.py observe "以后做方案先分析评分标准，再写实现。" --approve
python3 scripts/apriday_self_improving.py snapshot
python3 scripts/apriday_self_improving.py view
python3 scripts/apriday_self_improving.py apply "帮我做一个新的赛事方案"
python3 scripts/apriday_self_improving.py edit mem_0001 --content "做架构方案时先分析评分标准，再写实现。"
python3 scripts/apriday_self_improving.py delete mem_0001
python3 scripts/apriday_self_improving.py evaluate
```

## Output Standard

When using this skill in a task, produce:

- the task result,
- the active memories used,
- any memory patch made,
- user-control actions available,
- and a short self-evaluation note when the task is part of a replay or benchmark.
