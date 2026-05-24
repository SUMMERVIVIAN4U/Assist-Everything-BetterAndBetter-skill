---
name: assist-everything-betterandbetter-skill
description: Use this skill when a task requires authorized collaboration memory: remembering user preferences, learning feedback, adapting workflows, managing memory through natural language or slash-like commands, and running reproducible three-round eval cases with reset, query, update, downgrade, delete, and workbench reporting.
---

# Assist Everything BetterAndBetter Skill

This skill provides an authorized collaboration-memory workflow.

## Trigger

Use when the user or evaluator asks for:

- remembering preferences or workflow rules with consent
- showing, resetting, deleting, downgrading, or archiving memories
- applying remembered preferences to a similar later task
- handling preference changes, conflict, or narrowed scope
- running eval cases for memory extraction, application, update/decay, transparency, and result quality

## Memory Policy

Never silently turn every statement into long-term memory. Extract only reusable information:

- `communication_preference`
- `workflow_rule`
- `scene_rule`
- `format_preference`
- `learning_preference`
- `research_method`
- `project_context`
- `temporary_instruction`
- `taboo_or_negative_preference`

Each memory must carry: `id`, `type`, `content`, `scope`, `source`, `confidence`, `status`, `evidence`, `applies_when`, and `user_approved`.

Statuses:

- `active`: can be retrieved and applied
- `superseded`: kept for audit, lower priority
- `archived`: retained for history, not applied by default
- `deleted`: must not be retrieved or applied

## Commands

Support either slash-like commands or natural language:

- `reset memory`, `清空记忆`, `重置记忆`
- `show memory`, `展示当前记忆`, `查看记忆`
- `delete <query>`, `删除...这条记忆`
- `downgrade <query>`, `降权...`
- `archive <query>`, `归档...`

## Three-Round Eval Flow

Each eval case starts from `reset memory`.

Eval cases are test scripts only. Do not put case-specific extraction, update, or response logic inside the skill. The agent must send each case step as ordinary user text through the same `process_message(...)` path used by normal chat.

1. Round 1: perform an initial no-preference task, receive explicit feedback, extract authorized memory.
2. Show memory: prove memory is inspectable and explainable.
3. Round 2: run a similar but different task; apply relevant active memory without asking the user to repeat it.
4. Round 3: receive changed or narrowed preference; downgrade, condition, archive, or replace old memory; run a task proving the new rule applies.
5. Delete retest: delete a selected memory and prove it is no longer retrieved or applied.

Round cards do not receive full scores. Full six-dimensional score exists only at the case level.

## Eval

Run the agent harness eval:

```bash
python3 -m evalharness.cli run
```

Output:

- `eval/output/latest/eval_report.json`
- `eval/output/latest/eval_report.md`

Run the interactive workbench:

```bash
python3 -m evalharness.cli serve --port 8787
```

The workbench exposes Dashboard, Cases, Trace, and Agent Chat tabs. Trace is the source of truth: every case turn records user input, assistant output, tool calls, applied memories, and memory snapshots.

For Mimo LLM agent/judge mode, configure:

```bash
cp .env.example .env
```

Then edit `.env`:

```dotenv
MIMO_API_KEY=...
MIMO_BASE_URL=https://api.mimo.chat/v1
MIMO_MODEL=mimo-v1
MIMO_TIMEOUT=60
```

Run:

```bash
python3 -m evalharness.cli serve --port 8787 --agent mimo
python3 -m evalharness.cli run --agent mimo --judge mimo
```

The CLI also supports `--env-file .env.local`. Without Mimo env vars, the harness uses the local tool agent and offline trace judge for reproducible local development. `EVALHARNESS_JUDGE_CMD` is still supported for a custom external judge command.
