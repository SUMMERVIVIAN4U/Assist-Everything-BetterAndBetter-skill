---
name: assist-everything-betterandbetter-skill
description: 当任务需要授权式协作记忆、跨会话偏好复用、记忆查看/删除/降级、Local JSON 或 Mem0 Hosted 后端、真实 LLM Agent Chat、或记忆 eval/workbench 演示时使用。
---

# 万事越来越好协作记忆 Skill

这是一个可安装到任意 host agent 的协作记忆 Skill。host agent 自己的大模型负责理解、推理和回答；本 Skill 只提供记忆工具层，用来读取、写入、管理和审计记忆。

目标：在用户授权和可审计的前提下，把偏好、约束、历史、决策和纠错经验变成可复用记忆，并在后续相似任务里减少用户重复说明。

运行边界：只安装 `skill/` 文件夹只会安装 host-agent 触发入口和说明，不能单独运行记忆工具。`memory-pack` / `memory-write` / `memory-manage` 依赖仓库根目录里的 Python runtime，或依赖已经安装到环境里的 Python 包。Workbench 同样依赖完整仓库 runtime。`scripts/` 是验证、稳定性评测、文档/演示构建辅助脚本，不是 Workbench 或 Direct Skill 的主运行入口。

## When To Use

Use this skill when the user asks to:

- remember or reuse preferences, constraints, decisions, context facts, history, or workflow rules
- inspect, reset, delete, downgrade, archive, or search memory
- handle changed preferences, narrowed scope, temporary overrides, or memory conflicts
- compare Local JSON memory with Mem0 Hosted memory
- run the no-frontend direct Skill path or the browser Workbench
- evaluate whether memory reduces user effort across multiple sessions

## First Activation

The user only needs to mention `$assist-everything-betterandbetter-skill` once. After that, treat follow-up messages in the same host-agent conversation as continuing this Skill task when they look like task details, feedback, choices, corrections, or memory-management commands.

Before the first task in a Skill conversation, check configuration:

```bash
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env config
```

Tell the user the effective setup before doing task work:

- memory enabled or disabled
- active backend: `local` or `mem0_hosted`
- memory is enabled by default when the config says enabled
- continuing the Skill task means the user authorizes memory reads/writes for this conversation
- users can manage memory with commands such as `展示当前记忆`, `删除...`, `降级...`, `清空记忆`
- users can exit the Skill with `退出 skill` or `不允许记忆`
- if the user does not allow memory, exit this Skill flow instead of continuing silently

Keep the notice short:

> 我先确认一下记忆配置：记忆默认开启，后端是 Mem0 Hosted。继续使用本 Skill 表示你允许我在本轮读取并写入相关记忆；如果不允许，请直接说“不允许记忆”，我会退出 Skill 流程。你可以随时说“展示当前记忆”“删除...”“降级...”“清空记忆”，也可以说“退出 skill”。现在开始处理你的任务。

If config is invalid, do not start the task yet. Say what is missing and provide the minimal `.env` fields to fix it. If memory is disabled, continue only after saying that this run will not persist new memory. If the user refuses memory authorization, stop using this Skill for the task and say that the Skill flow has exited.

## Per-Turn Flow

1. Call `memory-pack` with the current user message and concise recent conversation context.
2. Use the host agent's own model to answer the user.
3. Apply `apply_now` memories by default.
4. Treat `confirm_first` memories as cautious reminders. If they contain a previous same-scene budget or temporary constraint, do not ask an empty open-ended question; say “上次是 X，如果这次仍适用我先按它来” and still give a concrete answer.
5. If the user provides reusable preferences, constraints, history, choices, decisions, corrections, or workflow lessons, call `memory-write` after answering or before the next turn.
6. For explicit memory commands, call `memory-manage` and return its text.

Return a normal conversational answer. Do not mention memory tool calls unless the user asks about memory, debug, eval, or config.

## Tool Commands

Use these from the repo root:

```bash
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env memory-pack "帮我给女朋友选个生日礼物。"
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env memory-write --context "user: 帮我给女朋友选个生日礼物。" "预算1000元"
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env memory-manage "展示当前记忆"
```

Natural memory commands include:

- `展示当前记忆`
- `清空记忆`
- `删除 她喜欢紫色`
- `降级 父亲膝盖不好`
- `归档 番茄钟`
- `画像`
- `快照`
- `三层记忆`
- `隐私报告`

## Usage Rules

- Do not ask the user to repeat the skill name, choose a session id, configure a provider, or understand CLI modes.
- Do not call a second business LLM by default; the host agent is the model.
- Do not use raw mode for user-facing answers.
- Do not claim memory was saved unless `memory-write` or `memory-manage` actually reports a write/update/delete action.
- Do not apply deleted memory.
- Do not proactively delete previous selected or given gifts as "conflicts" with a new gift direction. Keep them as history/exclusions unless the user explicitly asks to delete that exact memory.
- Do not silently convert every utterance into long-term memory.
- Do not run a global Mem0 reset when user-scoped deletion is available.

## Minimal Configuration

Installed Skill mode does not require a separate business LLM provider. It does require memory configuration.

Default behavior:

- backend: `local`
- memory dir: `memories/default`
- profile: `default`
- memory enabled: true

Recommended Mem0 Hosted `.env` block:

```bash
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

Put real secrets in local `.env`, not in git.

## More Detail

Read these only when needed:

- `docs/skill-configuration.md`: configuration priority, Local JSON, Mem0 Hosted, privacy markers
- `docs/direct-skill-runtime.md`: installed Skill runtime, CLI smoke tests, standalone fallback
- `docs/memory-model-and-policy.md`: memory schema, layers, write policy, recall policy, decay
- `docs/workbench-and-eval.md`: Workbench modules, manual multi-session eval, competition demo path
- `docs/self-improvement.md`: learning ledger for recurring corrections, failures, and promotions
