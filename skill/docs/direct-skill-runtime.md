# Direct Skill Runtime

This document is for host agents such as Codex, Claude Code, or similar shells after the Skill is installed.

## Runtime Dependency

Installing only the `skill/` folder is not enough to run memory tools. The Skill instructions call:

```bash
python3 -m assist_everything_betterandbetter_skill.cli ...
```

That command requires the repository runtime to be present:

- `assist_everything_betterandbetter_skill/`
- `evalharness/`
- `pyproject.toml` or an equivalent editable/package install
- local `.env` / runtime config

Use the commands from the repo root, or install the package into the host environment first.

The `scripts/` folder is auxiliary. It contains verification, stability eval, and document/demo builders. Direct Skill does not primarily run through `scripts/`.

## Runtime Model

The host agent's own model is the agent. The Python CLI is the memory tool layer.

Default installed flow:

1. Check config once at first activation.
2. Call `memory-pack`.
3. Answer with the host agent model.
4. Call `memory-write` for reusable preferences, constraints, history, decisions, corrections, and workflow lessons.
5. Call `memory-manage` for explicit memory commands.

Do not ask the user to repeat the skill name after the first invocation.

## Memory Tool Commands

```bash
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env config
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env memory-pack "帮我给女朋友选个生日礼物"
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env memory-write --context "user: 帮我给女朋友选个生日礼物" "预算1000元"
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env memory-manage "展示当前记忆"
```

## Natural Memory Management

Users can manage memory in the same conversation:

- `展示当前记忆`
- `清空记忆`
- `删除 她喜欢紫色`
- `降级 父亲膝盖不好`
- `归档 番茄钟`
- `画像`
- `快照`
- `三层记忆`
- `隐私报告`

## Smoke Test

After installing the Skill, verify the host-agent memory tool path:

```bash
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env config
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env memory-pack "帮我给女朋友选个生日礼物"
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env memory-manage "展示当前记忆"
```

## Standalone Fallback

`agent-turn` and `agent-chat` still exist for standalone CLI, Workbench-equivalent smoke tests, and eval reproduction.

These commands require a configured LLM provider because they run outside the host agent's model. Do not use them as the default installed Skill path.

Raw mode is for memory-runtime debugging only:

```bash
python3 -m assist_everything_betterandbetter_skill.cli chat --allow-no-llm --raw-skill "展示当前记忆"
```

Raw mode returns the memory-tool draft and does not match Workbench answer quality.
