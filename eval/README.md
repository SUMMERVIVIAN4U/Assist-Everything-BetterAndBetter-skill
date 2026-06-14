# Eval

CLI smoke run:

```bash
python3 -m evalharness.cli run --judge heuristic --agent local --output /tmp/assist-eval-smoke
```

Workbench real LLM run:

```bash
python3 -m evalharness.cli serve --port 8787 --agent deepseek_pro
```

Then open `http://127.0.0.1:8787` and use `Run Scenarios` or `Run LLM Eval`.

The Scenario Library executes:

- C01 family travel planning
- C02 project report and decision material
- C03 exam study planning
- C04 literature review and research design
- GIFT-01 girlfriend birthday gift

Each case covers reset, first task, feedback, memory view, second task, preference change, third task, delete retest, six-dimension scoring, and user-effort trajectory scoring.

Workbench eval is real LLM only. The default provider is DeepSeek V4 Pro, with DeepSeek V4 Flash and Mimo also selectable in the Workbench. Local + heuristic remains available only as an engineering smoke/contract check.
