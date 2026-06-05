from __future__ import annotations

import argparse
import json

from .env import load_env
from .runner import run_all
from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(prog="evalharness")
    parser.add_argument("--env-file", default=".env", help="Path to env file loaded before running commands.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--output", default="eval/output/latest")
    run.add_argument("--judge", default="heuristic", choices=["auto", "heuristic", "external", "mimo", "deepseek-flash", "deepseek-pro"])
    run.add_argument("--agent", default="local", choices=["local", "mimo", "deepseek-flash", "deepseek-pro"])
    server = sub.add_parser("serve")
    server.add_argument("--port", type=int, default=8787)
    server.add_argument("--agent", default="local", choices=["auto", "local", "mimo", "deepseek-flash", "deepseek-pro"])
    args = parser.parse_args()
    load_env(args.env_file)

    if args.cmd == "run":
        report = run_all(args.output, judge_mode=args.judge, agent_mode=args.agent)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.cmd == "serve":
        serve(port=args.port, agent_mode=args.agent)


if __name__ == "__main__":
    main()
