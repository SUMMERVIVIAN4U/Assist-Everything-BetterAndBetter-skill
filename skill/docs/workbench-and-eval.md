# Workbench And Eval

Workbench is the visual shell around the same Agent chain.

Workbench requires the full repository runtime. It does not run from the `skill/` folder alone.

Primary Workbench entry point is the module command below. The `scripts/` folder contains auxiliary verification/stability/document generation helpers, not the main Workbench server.

## Run Workbench

```bash
python3 -m evalharness.cli --env-file .env serve --port 8787 --agent minimax
```

## Modules

- `Agent Chat`: real LLM chat with current memory panel and manual session reset
- `History Evals`: real LLM eval history, grouped by task/session, newest first
- `Settings`: Agent persona/LLM, Skill config, Memory config, Eval rules
- `Performance Demo`: separate large-memory performance demo line

Workbench memory views:

- `当前 Memory`: selected active engine and whether `记忆功能` is enabled
- `Workbench Memory`: local runtime trace/audit view
- `Mem0 Memory`: remote memory inspection view when Mem0 Hosted is configured
- `隐私设置`: private markers and redaction rules
- `/api/current-memory`: refreshable API backing the current memory panel

Removed or disabled:

- no local deterministic chat mode in Workbench
- no local agent mode in Workbench
- no Run All button in Workbench UI
- no Statistics tab

## Manual Multi-Session Eval

The competition demo should use manual replay in Agent Chat:

1. Round 1: initial task, feedback, memory formation, eval
2. Reset session
3. Round 2: similar task, memory reuse, eval
4. Reset session
5. Round 3: changed/deleted preference, retest, eval

History Evals should show score, user effort, and reused memory information points across sessions.

## CLI Eval

CLI eval still exists for reproducible testing:

```bash
python3 -m evalharness.cli --env-file .env run --agent minimax --judge minimax
```

Use isolated profiles or temporary memory dirs for eval so test data does not pollute real user memory.
