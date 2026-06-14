---
name: assist-everything-betterandbetter-skill
description: 当任务需要授权协作记忆、Workbench 记忆检查、隐私友好的本地或 Mem0 记忆后端、可复现记忆评测，或需要记录纠正、错误、能力缺口和可复用经验以形成自我改进闭环时使用。
---

# 万事越来越好协作记忆 Skill

这个 Skill 提供一套经过授权的协作记忆工作流，也支持“从错误中学习、在经验中成长”。它把记忆视为用户可控、可审计的能力，而不是静默记录完整对话。

## Trigger

Use when the user or evaluator asks for:

- remembering preferences, constraints, context facts, decisions, or workflow rules with consent
- applying remembered preferences to a similar later task
- handling preference changes, conflict, narrowed scope, downgrade, archive, delete, or reset
- inspecting memory profile, compact snapshot, three-layer memory state, privacy controls, or backend state
- running reproducible evals for memory extraction, application, update/decay, transparency, and result quality
- operating the Workbench Agent Chat, History Evals, Stats, Performance Demo, Settings, Workbench Memory, Mem0 Memory, 当前 Memory, or `/api/current-memory` views
- logging corrections, command failures, integration errors, feature requests, and recurring patterns into `.learnings/`

## Memory Policy

Never silently turn every statement into long-term memory. Extract only reusable information:

- `preference`: soft user, audience, or subject preference used for ranking or style
- `constraint`: hard limit, taboo, exclusion, or conditional rule used for filtering
- `workflow`: reusable process, output structure, study method, or research method
- `decision`: current-task choice or settled interim conclusion that should be continued
- `history`: past action or event that should inform future avoidance or continuity
- `context_fact`: stable background fact or external signal that informs judgment

Use generic `type` values. Put the task domain in `scope` such as `life_family_travel`, `work_report`, `study_plan`, `research_review`, or another user-provided domain. Do not create scenario-specific memory types or hardcoded case logic.

Each memory must carry: `id`, `type`, `content`, `scope`, `source`, `confidence`, `status`, `evidence`, `applies_when`, and `user_approved`. Runtime memory may also carry `subject`, `target`, `object`, `predicate`, and `validity`.

Only say a memory was saved after a real add/update action exists in the trace. If a sentence is only a question such as "你还记得之前做过什么吗？", do not save it as memory.

Use confidence tiers before writing memory:

- `reject`: sensitive or temporary content is not written
- `ask`: weak signal needs clarification
- `propose`: medium-confidence long-term memory waits for user approval
- `add`: high-confidence structured or scoped memory can be saved
- `dedupe`: duplicate active memory is reported but not saved again

Simple `[q]` or greeting turns use instant mode and skip long-term retrieval. Normal tasks use standard mode with active matching memory. Deep/history turns expose snapshot, matching memory, and event-log intent in diagnostics.

Statuses:

- `active`: can be retrieved and applied
- `superseded`: kept for audit, lower priority
- `archived`: retained for history, not applied by default
- `deleted`: must not be retrieved or applied

## Storage And Backends

Default runtime storage is local Markdown/JSON under `ASSIST_MEMORY_DIR`; Workbench uses `memories/workbench/`. `ASSIST_MEMORY_PERSIST=0` disables persistence for reproducible eval runs.

The Workbench can switch the long-term memory engine between two mutually exclusive adapters:

- `LocalMemoryStore`: 本地 JSON/Markdown, using the skill's transparent local extraction and audit trail
- `HostedMem0Client`: hosted or REST-compatible Mem0, using the skill's structured extraction and Mem0 as durable storage/search

Only one engine is active at a time. Extraction rules are mutually exclusive, and memory results are mutually exclusive:

- local mode never mirrors extracted memories to Mem0
- hosted Mem0 mode runs the same structured extraction path as local mode, then stores structured memories remotely
- UI snapshots may share the same display structure, but the content must come only from the selected engine

Retrieval ranking is unified across both engines. Each adapter may produce candidates differently, but before memories are applied to a response the runtime must:

- keep only `active` items and filter polluted or deleted content
- annotate each candidate with `validity.retrieval_score` and `validity.retrieval_rank_strategy=score_time`
- sort by retrieval score descending, then by `updated_at`/`created_at` descending
- apply at most the top matching memories for the current turn

When Mem0 is selected, do not expose endpoint URLs, project IDs, or API keys in the UI. The public UI only shows whether endpoint, API key, and user are configured.

Configuration:

- `ASSIST_MEMORY_BACKEND=local|mem0_hosted`
- `ASSIST_MEMORY_ENABLED=0|1`
- `MEM0_BASE_URL`
- `MEM0_API_KEY`
- `MEM0_USER_ID`
- `MEM0_APP_ID`

Keep the public path focused on local JSON for the competition demo and Hosted Mem0 for durable external memory.

## Workbench Features

Run the interactive Workbench:

```bash
python3 -m evalharness.cli serve --port 8787
```

Workbench tabs:

- `Agent Chat`: live conversation through the same `process_message(...)` path as evals.
- `History Evals`: saved preset and chat eval runs.
- `统计`: summary metrics across historical runs.
- `Performance Demo`: 本地 JSON、Mem0 Hosted 的超大记忆端到端性能演示，展示写入、检索、score+time 排序、样例 TopK、阶段耗时和 reset 结果。
- `设置`: Agent 配置, Workbench Memory, Mem0 Memory, 隐私设置.

Agent Chat must keep the right-side `当前 Memory` panel intuitive:

- show `记忆功能：开启/关闭`
- show the user-selected engine: 本地 Markdown / JSON or Mem0
- show only the selected engine's corresponding content
- use `/api/current-memory` for refreshable current-memory state

Settings rules:

- Agent 配置 exposes only the memory feature switch and long-term memory backend choice.
- Workbench Memory shows local trace/audit memory.
- Mem0 Memory shows remote memory for comparison when configured.
- 隐私设置 lets the user maintain private marker lines; matching content is rejected/redacted and not saved.

Performance Demo rules:

- 默认使用 `Dry Run`，只生成确定性数据和模拟指标，不访问远端 Mem0。
- 本地 JSON 引擎的 `Real Run` 使用临时内存索引模拟本地记忆库写入、检索和清理，不写入 Workbench 当前记忆文件。
- `Real Run` 只允许使用隔离用户 `workbench-demo-large-memory`，不得复用 Agent Chat 当前用户。
- 演示结果必须展示 `demo_user_id`、记忆规模、写入 QPS、检索 P50/P95、错误率、阶段时间线和检索样例。
- 检索样例使用与运行时一致的统一策略：按 `retrieval_score` 降序，再按更新时间降序，并标记 `retrieval_rank_strategy=score_time`。
- `Reset Demo Memory` 只清理隔离 demo 用户的记忆；不得清理当前 Agent Chat 用户或全局记忆库。

Reset Memory must reset only the selected engine. For HostedMem0Client, deletion/reset must be scoped to the configured `user_id`; never call a global reset when user-scoped deletion is available.

Because Mem0 automatic extraction is less explainable than the local MemoryItem path, every remote action must still expose a trace event with backend, raw detail, success/failure, and returned result summary.

## Commands

Support slash-like commands or natural language:

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

## Self-Improvement Loop

Use `.learnings/` as the growth ledger for the skill and project. Initialize it before logging:

```bash
mkdir -p .learnings
```

Create missing files without overwriting existing content:

- `.learnings/LEARNINGS.md`: corrections, insights, knowledge gaps, best practices
- `.learnings/ERRORS.md`: command failures, exceptions, integration errors
- `.learnings/FEATURE_REQUESTS.md`: user-requested missing capabilities

Do not log secrets, tokens, API keys, private identifiers, or full config files. Prefer short redacted summaries and related file paths.

Log immediately when:

- a command or operation fails unexpectedly
- the user corrects the agent
- an external API, memory backend, or tool call fails
- the agent discovers outdated knowledge or a better recurring approach
- the user asks for a capability the skill does not yet provide

Entry IDs use `LRN-YYYYMMDD-XXX`, `ERR-YYYYMMDD-XXX`, and `FEAT-YYYYMMDD-XXX`.

Minimum entry fields:

- logged timestamp
- priority: `low | medium | high | critical`
- status: `pending | in_progress | resolved | promoted | wont_fix`
- area: `frontend | backend | infra | tests | docs | config`
- summary, context, suggested action, related files

## Recurring Pattern And Promotion

Before logging a new learning, search for similar entries:

```bash
grep -r "keyword" .learnings/
```

If similar, link it with `See Also`, bump priority when recurring, and add a stable `Pattern-Key` when it reflects a recurring pattern.

Recurring Pattern handling:

- same `Pattern-Key` increments `Recurrence-Count`
- keep `First-Seen` and `Last-Seen`
- recurring issues should lead to systemic fixes, tests, docs, or skill changes

Promotion rules:

- promote broadly applicable, resolved, or recurring learnings into this `SKILL.md`, `AGENTS.md`, or other project guidance
- write promoted rules as short prevention rules, not incident transcripts
- update the original entry status to `promoted` and record the target

## Eval Flow

Run the harness eval:

```bash
python3 -m evalharness.cli run
```

Output:

- `eval/output/latest/eval_report.json`
- `eval/output/latest/eval_report.md`

Each eval case starts from `reset memory`. Eval cases are scripts only; do not put case-specific extraction, update, or response logic inside the skill. Send each case step as ordinary user text through the same runtime path used by Agent Chat.

Recommended flow:

1. Round 1: perform an initial no-preference task, receive explicit feedback, extract authorized memory.
2. Show memory: prove memory is inspectable and explainable.
3. Round 2: run a similar but different task; apply active memory without asking the user to repeat it.
4. Round 3: receive changed or narrowed preference; downgrade, condition, archive, or replace old memory.
5. Delete retest: delete a selected memory and prove it is no longer retrieved or applied.

Round cards do not receive full scores. Full six-dimensional score exists only at the case level.

For Mimo LLM agent/judge mode:

```bash
cp .env.example .env
python3 -m evalharness.cli serve --port 8787 --agent mimo
python3 -m evalharness.cli run --agent mimo --judge mimo
```

The CLI also supports `--env-file .env.local`. Without Mimo env vars, the harness uses the local tool agent and offline trace judge for reproducible local development. `EVALHARNESS_JUDGE_CMD` is still supported for a custom external judge command.
