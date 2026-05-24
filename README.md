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
- Reset, show/query, delete, downgrade, archive
- Slash-like and natural language memory commands
- Three-round eval flow with delete retest
- Agent harness that drives the skill through conversation turns
- Trace-based eval with memory snapshots and tool calls
- Offline judge by default, with an external LLM judge adapter

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
- Cases: four three-round case cards with per-dimension scores
- Trace: full user/assistant/tool-call/memory snapshots
- Agent Chat: direct conversation with the installed harness agent

## Verify Stability

Runs the four documented cases three times and asserts every case is at least 90/100.

```bash
python3 scripts/verify_eval.py
```

## Optional External LLM Judge

Set `EVALHARNESS_JUDGE_CMD` to a command that reads case-run JSON from stdin and returns score JSON:

```bash
EVALHARNESS_JUDGE_CMD="python3 scripts/my_llm_judge.py" python3 -m evalharness.cli run --judge external
```
