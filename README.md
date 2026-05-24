# Assist Everything BetterAndBetter Skill

This repository contains planning artifacts for an adaptive memory collaboration Skill:

- Architecture and eval design documents under `docs/`
- Static eval workbench demos in HTML
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
- Deterministic scoring for the four workbench cases

## Run Eval

```bash
python3 -m assist_everything_betterandbetter_skill.evaluator
```

Outputs:

- `eval/output/latest/eval_report.json`
- `eval/output/latest/eval_report.md`

## Build Eval Workbench

```bash
python3 scripts/build_eval_workbench.py
```

Open:

- `reports/eval-workbench.html`

## Verify Stability

Runs the four documented cases three times and asserts every case is at least 90/100.

```bash
python3 scripts/verify_eval.py
```
