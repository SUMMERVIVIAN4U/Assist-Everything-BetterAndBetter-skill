# Skill Configuration

This document expands the minimal configuration contract in `skill/SKILL.md`.

## Priority

Configuration is shared by the memory backend.

Priority:

1. `.env` provides secrets and default values.
2. `memories/config/runtime.json` is the shared saved runtime config. Workbench Settings writes here, and direct Skill reads here.
3. CLI flags such as `--provider`, `--memory-backend`, and `--memory-dir` are temporary process overrides. They do not rewrite shared config unless an explicit save API/action is used.

Installed Skill mode and Workbench Agent Chat use the `default` profile unless explicitly overridden. Eval and performance demo should use isolated profiles or temporary memory dirs so they do not pollute real user memory.

## Installed Skill

Installed Skill mode does not require a separate business LLM provider because the host agent is the model.

Memory variables:

```bash
ASSIST_MEMORY_ENABLED=1
ASSIST_MEMORY_PERSIST=1
ASSIST_MEMORY_DIR=memories/default
ASSIST_MEMORY_BACKEND=local
ASSIST_MEMORY_LLM_EXTRACTOR=1
ASSIST_RUNTIME_PROFILE=default
```

Default backend is `local`.

`LocalMemoryStore` is the local JSON/Markdown engine. `HostedMem0Client` is the Mem0 Hosted / REST-compatible durable engine. The two backends are mutually exclusive for active long-term memory: one selected engine is used at a time.

## Mem0 Hosted

Use Mem0 Hosted when the demo needs a durable remote memory backend or cross-client persistence.

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

Mem0 operations must stay scoped to the configured `user_id`. Never run a global reset when user-scoped deletion is available.

## Workbench And Eval LLM

Workbench and standalone eval need real LLM provider settings:

```bash
ASSIST_AGENT_PROVIDER=minimax
MINIMAX_API_KEY=<fill-your-minimax-api-key>
MINIMAX_BASE_URL=https://api.minimax.io/v1
MINIMAX_MODEL=MiniMax-M2.7
MINIMAX_TIMEOUT=60
```

DeepSeek can also be configured if selected in Workbench:

```bash
DEEPSEEK_API_KEY=<fill-your-deepseek-api-key>
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_PRO_MODEL=deepseek-v4-pro
DEEPSEEK_FLASH_MODEL=deepseek-v4-flash
DEEPSEEK_TIMEOUT=60
```

These provider settings are not required for installed Skill memory tools.

## Privacy

```bash
ASSIST_PRIVACY_MARKERS=身份证,银行卡,token
```

Sensitive/private observations are redacted and should not be saved as memory.

Show effective config:

```bash
python3 -m assist_everything_betterandbetter_skill.cli --env-file .env config
```

The output includes `config_path`. For shared config this should be `memories/config/runtime.json`.
