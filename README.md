# apriday-self-Improving

`apriday-self-Improving` is a runnable self-improving AI Skill for the WASC "自成长·越用越懂你" challenge. It gives an agent a small, auditable memory loop: extract durable user preferences, ask for/record authorization, apply the memory in later tasks, update conflicting memories, and verify deletion.

The implementation is intentionally local and deterministic. It does not call external model APIs, so judges can reset it and replay the same evaluation script with stable results.

## What It Does

- Extracts long-term preferences, workflow rules, scene rules, and project context from user feedback.
- Proactively detects high-confidence durable signals without requiring the user to say "remember this" every time.
- Uses Bio-Memory Pro inspired confidence tiers: auto-record, natural confirmation, light ask, or ignore.
- Filters temporary instructions such as "这次", "本次", and "临时" so they do not become long-term memory.
- Provides a lightweight snapshot and instant/standard/deep loading modes to avoid unnecessary memory reads.
- Builds a compact user profile from active preference, workflow, scene, and project memories.
- Learns from feedback by adjusting memory confidence after successful or poor applications.
- Exposes privacy controls and redacts sensitive observations instead of storing them.
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
python3 scripts/apriday_self_improving.py observe "我特别喜欢先看结论再看细节。"
python3 scripts/apriday_self_improving.py observe "以后做架构方案先分析评分标准，再写实现。" --approve
python3 scripts/apriday_self_improving.py snapshot
python3 scripts/apriday_self_improving.py profile
python3 scripts/apriday_self_improving.py apply "帮我做一个新的赛事方案"
python3 scripts/apriday_self_improving.py feedback mem_0001 "这个偏好应用准确，继续保持。" --rating 1
python3 scripts/apriday_self_improving.py privacy
python3 scripts/apriday_self_improving.py evaluate
python3 -m unittest discover -s tests
```

## Bio-Memory Pro Integration

This version references the design ideas from the MIT-licensed `xihuanhai8-blip/bio-memory` project and ports the useful concepts into this repo's local deterministic runtime:

- proactive memory detection,
- confidence-based recording decisions,
- topic/scope separation,
- duplicate suppression,
- lightweight snapshots,
- instant/standard/deep memory loading modes.

The implementation in `scripts/apriday_self_improving.py` is self-contained and uses only the Python standard library.

## Challenge Alignment

| WASC dimension | How this repo addresses it |
| --- | --- |
| 可复测性 | `reset`, `view`, `edit`, `delete`, deterministic `evaluate` |
| 有效记忆提取 | typed extraction, temporary instruction filtering, evidence capture |
| 记忆应用效果 | `apply` turns active memory into explicit plan adaptations |
| 记忆更新与淘汰 | conflict detection marks old memory as `superseded` |
| 用户控制与透明度 | all memory is viewable, editable, deletable, and source-linked |
| 结果质量与真实可用性 | eval script checks final task output after memory changes and deletion |

## Encouraged Direction Coverage

| 鼓励方向 | Runnable evidence |
| --- | --- |
| 偏好记忆与画像沉淀 | `profile` aggregates active preferences, workflow rules, and interaction style |
| 反馈学习与自我调整 | `feedback` adjusts confidence and archives low-confidence memories |
| 上下文压缩与长期记忆 | `snapshot` returns compression metrics and recent active memory only |
| 个性化结果与交互方式 | `apply` returns `personalization.interaction_style` and adapts the plan |
| 隐私可控的记忆管理 | `privacy`, `delete`, `reset`, and redacted sensitive observations |
| 面向真实工作场景 | `evaluate` and `self-improving-visual-demo.html` cover cross-industry workflows |

## Existing Planning Artifacts

This repository also contains the original planning materials:

- Architecture and eval design documents under `docs/`
- Static eval workbench demo in `eval-case-workbench-simple.html`
- Cross-industry auto demo in `self-improving-visual-demo.html`
- Generated diagrams under `assets/`
- Document generation scripts under `scripts/`
