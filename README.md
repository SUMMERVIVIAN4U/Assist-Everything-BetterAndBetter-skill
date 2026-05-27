# apriday-self-Improving

`apriday-self-Improving` is a runnable self-improving AI Skill for the WASC "自成长·越用越懂你" challenge. It gives an agent a small, auditable memory loop: extract durable user preferences, ask for/record authorization, apply the memory in later tasks, update conflicting memories, and verify deletion.

The implementation is intentionally local and deterministic. It does not call external model APIs, so judges can reset it and replay the same evaluation script with stable results.

## What It Does

- Extracts long-term preferences, workflow rules, scene rules, and project context from user feedback.
- Filters temporary instructions such as "这次", "本次", and "临时" so they do not become long-term memory.
- Stores memory with source evidence, scope, confidence, approval status, and lifecycle status.
- Applies active memory to later tasks and explains which memory affected the plan.
- Handles preference changes by superseding older conflicting memory rather than endlessly accumulating rules.
- Supports reset, view, edit, delete, replay, and automatic scoring for the WASC 8-step continuous-use test.

## Project Structure

```text
project-root/
├── README.md
├── skill/
│   └── SKILL.md
├── scripts/
│   ├── apriday_self_improving.py
│   ├── build_architecture_doc.py
│   ├── build_viral_case_doc.py
│   └── build_workbench_requirements_doc.py
├── SETUP.md
├── tests/
│   └── test_apriday_self_improving.py
├── LICENSE
├── docs/
├── assets/
└── eval-case-workbench-simple.html
```

## Quick Start

```bash
python3 scripts/apriday_self_improving.py reset
python3 scripts/apriday_self_improving.py observe "以后做架构方案先分析评分标准，再写实现。" --approve
python3 scripts/apriday_self_improving.py apply "帮我做一个新的赛事方案"
python3 scripts/apriday_self_improving.py evaluate
python3 -m unittest discover -s tests
```

## Challenge Alignment

| WASC dimension | How this repo addresses it |
| --- | --- |
| 可复测性 | `reset`, `view`, `edit`, `delete`, deterministic `evaluate` |
| 有效记忆提取 | typed extraction, temporary instruction filtering, evidence capture |
| 记忆应用效果 | `apply` turns active memory into explicit plan adaptations |
| 记忆更新与淘汰 | conflict detection marks old memory as `superseded` |
| 用户控制与透明度 | all memory is viewable, editable, deletable, and source-linked |
| 结果质量与真实可用性 | eval script checks final task output after memory changes and deletion |

## Existing Planning Artifacts

This repository also contains the original planning materials:

- Architecture and eval design documents under `docs/`
- Static eval workbench demo in `eval-case-workbench-simple.html`
- Generated diagrams under `assets/`
- Document generation scripts under `scripts/`
