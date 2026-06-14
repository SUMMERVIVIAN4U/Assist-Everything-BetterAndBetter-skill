from __future__ import annotations

import argparse
import json

from .env import load_env
from .llm import LLM_PROVIDER_LABELS
from .runner import run_all
from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(prog="evalharness")
    parser.add_argument("--env-file", default=".env", help="Path to env file loaded before running commands.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--output", default="eval/output/latest")
    run.add_argument("--judge", default="heuristic", choices=["auto", "heuristic", "external", *LLM_PROVIDER_LABELS.keys()])
    run.add_argument("--agent", default="local", choices=["local", *LLM_PROVIDER_LABELS.keys()])
    server = sub.add_parser("serve")
    server.add_argument("--port", type=int, default=8787)
    server.add_argument("--agent", default=None, choices=list(LLM_PROVIDER_LABELS), help="Workbench only supports real LLM mode. Defaults to shared runtime config.")
    args = parser.parse_args()
    load_env(args.env_file)

    if args.cmd == "run":
        report = run_all(args.output, judge_mode=args.judge, agent_mode=args.agent)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.cmd == "serve":
        serve(port=args.port, agent_mode=args.agent)


if __name__ == "__main__":
    main()
