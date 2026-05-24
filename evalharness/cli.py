from __future__ import annotations

import argparse
import json

from .runner import run_all
from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(prog="evalharness")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--output", default="eval/output/latest")
    run.add_argument("--judge", default="auto", choices=["auto", "heuristic", "external", "mimo"])
    run.add_argument("--agent", default="local", choices=["local", "mimo"])
    server = sub.add_parser("serve")
    server.add_argument("--port", type=int, default=8787)
    server.add_argument("--agent", default="auto", choices=["auto", "local", "mimo"])
    args = parser.parse_args()

    if args.cmd == "run":
        report = run_all(args.output, judge_mode=args.judge, agent_mode=args.agent)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.cmd == "serve":
        serve(port=args.port, agent_mode=args.agent)


if __name__ == "__main__":
    main()
