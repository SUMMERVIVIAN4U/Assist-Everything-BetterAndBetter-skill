# Setup

`apriday-self-Improving` runs with Python 3 and the standard library only.

## Install

No package installation is required.

```bash
python3 --version
```

## Run the Skill CLI

```bash
python3 scripts/apriday_self_improving.py reset
python3 scripts/apriday_self_improving.py observe "我特别喜欢先看结论再看细节。"
python3 scripts/apriday_self_improving.py observe "以后做架构方案先分析评分标准，再写实现。" --approve
python3 scripts/apriday_self_improving.py snapshot
python3 scripts/apriday_self_improving.py view
python3 scripts/apriday_self_improving.py apply "帮我做一个新的赛事方案"
```

Memory is stored locally in `.apriday_memory/memory.json`. Override this path for tests or sandboxed runs:

```bash
APRIDAY_MEMORY_DIR=/tmp/apriday-memory python3 scripts/apriday_self_improving.py evaluate
```

## Run Automated Evaluation

```bash
python3 scripts/apriday_self_improving.py evaluate
python3 -m unittest discover -s tests
```

The evaluation replays the required continuous-use structure:

1. reset memory
2. first task
3. user feedback
4. view memory
5. second task
6. preference change
7. third task
8. deletion replay

## Demo Workbench

The static eval workbench can be viewed with:

```bash
python3 -m http.server 8000 --bind 127.0.0.1
```

Then open:

- `http://127.0.0.1:8000/eval-case-workbench-simple.html`
- `http://127.0.0.1:8000/self-improving-visual-demo.html`
