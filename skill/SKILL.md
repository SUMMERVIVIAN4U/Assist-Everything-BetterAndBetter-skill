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
- inspecting memory profile, compact snapshot, three-layer memory state, and privacy controls
- running eval cases for memory extraction, application, update/decay, transparency, and result quality

## Memory Policy

Never silently turn every statement into long-term memory. Extract only reusable information:

- `preference`: soft user, audience, or subject preference used for ranking or style
- `constraint`: hard limit, taboo, exclusion, or conditional rule used for filtering
- `workflow`: reusable process, output structure, study method, or research method
- `decision`: current-task choice or settled interim conclusion that should be continued
- `history`: past action or event that should inform future avoidance or continuity
- `context_fact`: stable background fact or external signal that informs judgment

Use generic `type` values. Put the task domain in `scope` such as `relationship_gift`, `life_family_travel`, `work_report`, `study_plan`, or `research_review`. Do not create domain-specific types such as `gift_history`; use `type=history` and `scope=relationship_gift`.

Each memory must carry: `id`, `type`, `content`, `scope`, `source`, `confidence`, `status`, `evidence`, `applies_when`, and `user_approved`. Runtime memory may also carry structured fields: `subject`, `target`, `object`, `predicate`, and `validity`.

Only say a memory was saved after a real add/update memory action exists in the trace. If a sentence is only a question such as "你还记得之前送过什么吗？", do not save it as memory.

Use confidence tiers before writing memory:

- `reject`: sensitive or temporary content is not written
- `ask`: weak signal needs clarification
- `propose`: medium-confidence long-term memory waits for user approval
- `add`: high-confidence structured or scoped memory can be saved
- `dedupe`: duplicate active memory is reported but not saved again

Simple `[q]` or greeting turns use instant mode and skip long-term memory retrieval. Normal tasks use standard mode with active matching memory. Deep/history turns use deep mode and expose snapshot, matching memory, and event-log intent in diagnostics.

By default, runtime memory is persisted as Markdown files. `ASSIST_MEMORY_DIR` controls the storage directory, and `ASSIST_MEMORY_PERSIST=0` disables persistence for reproducible eval runs. Workbench uses `memories/workbench/`.

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
- `profile`, `画像`
- `snapshot`, `快照`
- `layers`, `三层记忆`
- `privacy`, `隐私报告`

The profile view aggregates active preferences, workflow rules, scene rules, project/context facts, interaction style, and confidence average. The layers view shows L0 instant interaction, L1 profile snapshot, and L2 long-term audit ledger with retention reasons.

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
