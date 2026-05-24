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
    run.add_argument("--judge", default="auto", choices=["auto", "heuristic", "external"])
    server = sub.add_parser("serve")
    server.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    if args.cmd == "run":
        report = run_all(args.output, judge_mode=args.judge)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.cmd == "serve":
        serve(port=args.port)


if __name__ == "__main__":
    main()
