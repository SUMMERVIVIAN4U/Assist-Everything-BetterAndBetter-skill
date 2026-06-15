---
name: assist-everything-betterandbetter-skill
description: 当任务需要授权式协作记忆、跨会话偏好复用、记忆查看/删除/降级、Local JSON 或 Mem0 Hosted 后端、真实 LLM Agent Chat、或记忆 eval/workbench 演示时使用。
---

# 万事越来越好协作记忆 Skill

这个 Skill 提供一个可安装的协作记忆 Agent。安装后，Agent 应该使用与 Workbench Agent Chat 相同的运行链路：`HarnessAgent -> AssistSkill -> memory backend -> LLM semantic extractor -> LLM final answer`。区别只是没有浏览器前端。

不要把它当作“只存 JSON 的工具”。它的目标是：在用户授权和可审计的前提下，把偏好、约束、历史、决策和纠错经验变成可复用记忆，并在后续相似任务里减少用户重复说明。它也要支持“从错误中学习、在经验中成长”的自我改进闭环。

## When To Use

Use this skill when the user asks to:

- remember or reuse preferences, constraints, decisions, context facts, history, or workflow rules
- inspect, reset, delete, downgrade, archive, or search memory
- handle changed preferences, narrowed scope, temporary overrides, or memory conflicts
- compare Local JSON memory with Mem0 Hosted memory
- run the no-frontend direct Agent or the browser Workbench
- evaluate whether memory reduces user effort across multiple sessions

## Installed Skill Runtime Path

For installed Skill usage in any host agent, the host agent's own model is the agent. This includes Codex, Claude Code, and similar coding/agent shells. Do not call a second business LLM by default. Use the Python CLI only as a memory tool layer.

Normal task flow:

1. Call `memory-pack` with the current user message and a concise recent conversation context.
2. Use the host agent's own model to answer the user, applying only `apply_now` memories by default and using `confirm_first` memories as cautious reminders. When `confirm_first` contains a previous same-scene budget or temporary constraint, do not ask an empty open-ended question; say “I saw last time it was X, if this still applies I’ll use it” and still give a concrete answer.
3. If the user provides reusable preferences, constraints, history, choices, decisions, or corrections, call `memory-write` after answering or before the next turn.
4. For commands like `展示当前记忆`, `删除...`, `降级...`, `清空记忆`, call `memory-manage` and return its text.

Return a normal conversational answer. Do not mention the memory tool calls unless the user asks about memory/debug/eval/config.

Conversational routing contract:

- The user only needs to mention `$assist-everything-betterandbetter-skill` once to start a task.
- After the first invocation, treat follow-up messages in the same host-agent conversation as continuing this Skill task when they look like task details, feedback, choices, corrections, or memory-management commands.
- Continue using the memory tool layer for those follow-ups until the user explicitly exits, starts a different non-memory task, or asks to reset the session.
- Do not ask the user to repeat the skill name, choose a session id, configure a provider, or understand CLI modes.

Installed Skill memory tool commands from the repo root:

```bash
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env memory-pack "帮我给女朋友选个生日礼物。"
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env memory-write --context "user: 帮我给女朋友选个生日礼物。" "预算1000元"
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env memory-manage "展示当前记忆"
```

The user can manage memory naturally in the same conversation:

- `展示当前记忆`
- `清空记忆`
- `删除 她喜欢紫色`
- `降级 父亲膝盖不好`
- `归档 番茄钟`
- `画像`
- `快照`
- `三层记忆`
- `隐私报告`

Only use the raw memory runtime for debugging:

```bash
python3 -m assist_everything_betterandbetter_skill.cli chat --raw-skill "展示当前记忆"
```

Raw mode returns the deterministic memory-tool draft and does not match Workbench answer quality.

Standalone fallback:

- `agent-turn` and `agent-chat` still exist for standalone CLI, Workbench-equivalent smoke tests, and eval reproduction.
- These commands require a configured LLM provider because they run outside the host agent's model.
- Do not use them as the default installed Skill path.

## Configuration

Configuration is shared by the memory backend. Installed Skill mode does not require a separate business LLM provider because the host agent is the model.

Priority:

1. `.env` provides secrets and default values.
2. `memories/config/runtime.json` is the shared saved runtime config. Workbench Settings writes here, and direct Skill reads here.
3. CLI flags such as `--provider` and `--memory-backend` are temporary process overrides and do not rewrite shared config unless an explicit save API/action is used.

Installed Skill mode and Workbench Agent Chat use the `default` profile unless explicitly overridden. Eval and performance demo should use isolated profiles or temporary memory dirs so they do not pollute real user memory.

Standalone / Workbench / Eval LLM only:

- `ASSIST_AGENT_PROVIDER=minimax`
- `MINIMAX_API_KEY`
- `MINIMAX_BASE_URL`
- `MINIMAX_MODEL`

These provider settings are not required for installed Skill memory tools.

Memory:

- `ASSIST_MEMORY_ENABLED=1|0`
- `ASSIST_MEMORY_DIR=memories/default`
- `ASSIST_MEMORY_BACKEND=local|mem0_hosted`
- `ASSIST_MEMORY_LLM_EXTRACTOR=1|0`
- `ASSIST_RUNTIME_PROFILE=default|eval|workbench-demo|mem0-performance`
- `LocalMemoryStore` is the local JSON/Markdown engine.
- `HostedMem0Client` is the Mem0 Hosted / REST-compatible durable engine.
- The two backends are mutually exclusive for active long-term memory: one selected engine is used at a time.

Mem0 Hosted:

- `MEM0_BASE_URL`
- `MEM0_API_KEY`
- `MEM0_USER_ID`
- `MEM0_APP_ID`
- `MEM0_PROJECT_ID`
- `MEM0_PROJECT_NAME`

Mem0 operations must stay scoped to the configured `user_id`; never run a global reset when user-scoped deletion is available.

Recommended local `.env` block for installed Skill memory persistence:

```bash
# Markdown memory persistence. Workbench defaults to memories/workbench.
ASSIST_MEMORY_PERSIST=1
ASSIST_MEMORY_DIR=memories/default
ASSIST_MEMORY_BACKEND=mem0_hosted
MEM0_PROJECT_NAME=test-self-improving-202606
MEM0_BASE_URL=https://mem0-cnlfjzigaku8gczkzo.mem0.volces.com:8000
MEM0_API_KEY=<fill-your-mem0-api-key>
MEM0_USER_ID=workbench-user
MEM0_APP_ID=assist-everything-betterandbetter-skill
MEM0_TIMEOUT=15
MEM0_PROJECT_ID=<fill-your-mem0-project-id>
```

Do not commit a real `MEM0_API_KEY` to git. Put real secrets in the local `.env` only.

Privacy:

- `ASSIST_PRIVACY_MARKERS=身份证,银行卡,token`

Show effective config:

```bash
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env config --provider minimax
```

The output includes `config_path`. For the shared config this should be `memories/config/runtime.json`.

## Memory Model

The memory schema is generic. Do not create scenario-specific memory types.

Types:

- `preference`: soft preference used for ranking or style
- `constraint`: hard limit, taboo, exclusion, or conditional rule
- `workflow`: reusable process or interaction rule
- `decision`: current-task choice that should be continued
- `history`: past action or event that should affect future avoidance or continuity
- `context_fact`: background fact useful for the task

Scopes are domains, not memory types:

- `gift_planning`
- `life_family_travel`
- `study_plan`
- `work_report`
- `research_review`
- `general`

Validity layers:

- `current_task`: only applies in the current session/task
- `scene_memory`: same scene can recall it, but should confirm before applying
- `long_term`: stable preference or rule, applies by default when scope matches
- `past`: historical fact, mainly for continuity and avoiding repetition

Statuses:

- `active`: can be retrieved
- `superseded`: kept for audit, lower priority
- `archived`: retained but not applied by default
- `deleted`: must not be retrieved or applied

## Write Policy

Never silently convert every utterance into long-term memory.

The write path is hybrid:

1. Rule extraction catches high-confidence structured signals such as budget, taboo, previous gifts, explicit deletion, and obvious travel/study/work constraints.
2. LLM semantic extraction handles context-dependent intent such as “选拍立得”, “重复候选名就是选定”, and user corrections.
3. The skill validates the candidate, assigns confidence, scopes it, dedupes it, and writes only valid memory.

Confidence behavior:

- `reject`: sensitive/private or inappropriate memory
- `ask`: weak signal, needs clarification
- `propose`: medium-confidence long-term memory waits for user approval
- `add`: high-confidence memory can be saved
- `dedupe`: equivalent active memory already exists

Only say memory was saved when the trace contains a real `add` or successful update action.

## Recall Policy

Recall is filtered before ranking:

1. status must be `active`
2. scope must match current task
3. gift recipient/target must match when available
4. deleted or polluted memories are excluded
5. validity layer decides whether memory goes to `apply_now` or `confirm_first`

Ranking uses `retrieval_score` plus time:

- base confidence or Mem0 score
- layer bonus: current task > long term > scene memory > past
- scope match bonus
- keyword/entity hit bonus
- user-approved bonus
- final order: score descending, then update/create time descending

The final LLM answer may only default-use `apply_now`; `confirm_first` is for cautious confirmation. For expired `current_task` memories such as a previous gift budget, the answer should confirm-and-proceed: mention the previous value as a tentative assumption and provide a recommendation under that assumption, instead of asking the user to repeat the value.

## Workbench

Workbench is the visual shell around the same Agent chain.

Run:

```bash
python3 -m evalharness.cli --env-file .env serve --port 8787 --agent minimax
```

Current Workbench modules:

- `Agent Chat`: real LLM chat with current memory panel and manual session reset
- `History Evals`: real LLM eval history, grouped by task/session, newest first
- `Settings`: Agent persona/LLM, Skill config, Memory config, Eval rules
- `Performance Demo`: separate large-memory performance demo line

Workbench memory views:

- `当前 Memory`: shows the selected active engine and whether `记忆功能` is enabled
- `Workbench Memory`: local runtime trace/audit view
- `Mem0 Memory`: remote memory inspection view when Mem0 Hosted is configured
- `隐私设置`: edits private markers and redaction rules
- `/api/current-memory`: refreshable API backing the current memory panel

Removed or disabled:

- no local deterministic chat mode in Workbench
- no local agent mode in Workbench
- no Run All button in Workbench UI
- no Statistics tab

## Eval

CLI eval still exists for reproducible testing:

```bash
python3 -m evalharness.cli --env-file .env run --agent minimax --judge minimax
```

The competition demo should use manual multi-session replay in Agent Chat:

1. Round 1: initial task, feedback, memory formation, eval
2. Reset session
3. Round 2: similar task, memory reuse, eval
4. Reset session
5. Round 3: changed/deleted preference, retest, eval

History Evals should show score, user effort, and reused memory information points across sessions.

## Installed Skill Smoke Test

After installing the skill, verify the host-agent memory tool path:

```bash
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env config
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env memory-pack "帮我给女朋友选个生日礼物"
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env memory-manage "展示当前记忆"
```

If no standalone LLM is configured, `agent-turn` / `agent-chat` should fail clearly instead of pretending to produce a local business answer. For memory-runtime debugging only:

```bash
python3 -m assist_everything_betterandbetter_skill.cli chat --allow-no-llm --raw-skill "展示当前记忆"
```

## Self-Improvement Ledger

Use `.learnings/` to log recurring corrections, tool failures, integration failures, and feature requests. Do not log secrets or full env files.

Create these files when needed:

- `.learnings/LEARNINGS.md`
- `.learnings/ERRORS.md`
- `.learnings/FEATURE_REQUESTS.md`

Recurring Pattern:

- Give recurring issues a stable pattern key.
- Track first seen, last seen, recurrence count, and related files.
- Recurring issues should lead to a systemic fix, not one-off patching.

Promotion:

- Promote recurring resolved learnings into this `SKILL.md` or project docs as short prevention rules.
- Update the original learning entry status to `promoted`.
