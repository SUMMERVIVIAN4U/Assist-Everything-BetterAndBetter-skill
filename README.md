# Assist Everything BetterAndBetter Skill

This repository contains planning artifacts for an adaptive memory collaboration Skill:

- Architecture and eval design documents under `docs/`
- Real eval harness workbench served by a local API
- Generated diagrams under `assets/`
- Document generation scripts under `scripts/`

Core focus:

- Authorized memory
- Preference and feedback learning
- Workflow adaptation
- Case-based eval
- Memory state transition and user effort reduction
- Capability ON/OFF ablation comparison

## Implemented Skill

Skill name: `assist-everything-betterandbetter-skill`

Runtime package:

- `assist_everything_betterandbetter_skill/`
- `skill/SKILL.md`

Key features:

- Authorized long-term memory schema
- Memory types and scopes
- Bio-Memory inspired confidence tiers: reject, ask, propose, auto-record, dedupe
- Instant/standard/deep memory loading modes
- Compact snapshot, user profile aggregation, and three-layer memory view
- Privacy report and sensitive-content redaction before memory write
- Reset, show/query, delete, downgrade, archive
- Slash-like and natural language memory commands
- Three-round eval flow with delete retest
- Agent harness that drives the skill through conversation turns
- Trace-based eval with memory snapshots and tool calls
- Offline judge by default, with configurable Mimo agent/judge adapters

Architecture boundary:

- Skill runtime is generic and processes natural-language messages through `process_message(...)`.
- Eval cases are only scripted user messages; they do not call case-specific skill methods.
- Agent Chat and automated eval share the same memory extraction, update, retrieval, deletion, and response path.
- Workbench memories are persisted as Markdown files under `memories/workbench/`; automated eval uses an isolated in-memory store for reproducibility.

## Memory Storage

Runtime memory can be persisted as Markdown:

- each memory is one `.md` file with JSON-compatible front matter
- `_events.jsonl` records memory lifecycle events
- `_state.json` records the current memory version

Config:

```dotenv
ASSIST_MEMORY_PERSIST=1
ASSIST_MEMORY_DIR=memories/default
```

`memories/` is git-ignored so local user memory and secrets do not leak into commits.

The workbench also supports an optional Mem0-compatible backend. In Settings, switch `长期记忆后端` from local storage to Mem0, then fill in the project endpoint, user id, app id, and API key. The API key is stored only in the git-ignored workbench settings file and is never returned to the browser in plaintext.

Environment variables are also supported:

```dotenv
ASSIST_MEMORY_BACKEND=mem0
MEM0_PROJECT_NAME=test-self-improving-202606
MEM0_BASE_URL=https://...
MEM0_API_KEY=...
MEM0_USER_ID=workbench-user
MEM0_APP_ID=test-self-improving-202606
```

When Mem0 is enabled, local memory still keeps the trace/audit state needed by the eval workbench, while long-term additions are mirrored to Mem0 and retrieval merges relevant Mem0 search results.

## Memory Introspection

The runtime exposes the lightweight evidence views migrated from the self-improving prototype:

```bash
python3 -m assist_everything_betterandbetter_skill.cli chat "我特别喜欢以后先看结论，再看评分标准。"
python3 -m assist_everything_betterandbetter_skill.cli profile
python3 -m assist_everything_betterandbetter_skill.cli snapshot
python3 -m assist_everything_betterandbetter_skill.cli layers
python3 -m assist_everything_betterandbetter_skill.cli privacy
```

Equivalent natural-language or slash-like commands also work in Agent Chat:

- `profile` / `画像`
- `snapshot` / `快照`
- `layers` / `三层记忆`
- `privacy` / `隐私报告`

Simple messages such as `[q] 你好` use instant mode and skip long-term memory retrieval. Deep/history requests such as `[d] 回顾之前偏好` expose the deep-mode loading plan in response diagnostics.

## Run Eval Harness

```bash
python3 -m evalharness.cli run
```

Outputs:

- `eval/output/latest/eval_report.json`
- `eval/output/latest/eval_report.md`

## Run Workbench

```bash
python3 -m evalharness.cli serve --port 8787
```

Open:

- `http://127.0.0.1:8787`

Workbench tabs:

- Dashboard: overall scores and judge mode
- Cases: documented three-round case cards with per-dimension scores and user-effort trajectories
- Trace: full user/assistant/tool-call/memory snapshots
- Agent Chat: direct conversation with the installed harness agent

## Verify Stability

Runs the documented cases five times and asserts every case is at least 90/100 with positive user-effort reduction.

```bash
python3 scripts/verify_eval.py
```

## Optional External LLM Judge

Both the workbench Agent Chat and eval judge can use Mimo through an OpenAI-compatible chat endpoint.

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

Run with the default `.env`:

```bash
python3 -m evalharness.cli serve --port 8787 --agent mimo
python3 -m evalharness.cli run --agent mimo --judge mimo
```

Or pass a custom env file:

```bash
python3 -m evalharness.cli --env-file .env.local run --agent mimo --judge mimo
```

Defaults:

- no Mimo env: local deterministic agent plus offline trace judge
- `--agent mimo`: run memory tools first, then ask Mimo to produce the final assistant wording from the tool trace
- `--judge mimo`: ask Mimo to score the case trace against the six competition dimensions

Set `EVALHARNESS_JUDGE_CMD` to a command that reads case-run JSON from stdin and returns score JSON:

```bash
EVALHARNESS_JUDGE_CMD="python3 scripts/my_llm_judge.py" python3 -m evalharness.cli run --judge external
```
