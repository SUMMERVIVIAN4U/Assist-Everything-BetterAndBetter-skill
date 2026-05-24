from __future__ import annotations

import argparse
import json

from .evaluator import run_all
from .skill import AssistSkill


def main() -> None:
    parser = argparse.ArgumentParser(prog="assist-better-skill")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("eval")
    manage = sub.add_parser("memory")
    manage.add_argument("text", nargs="+")
    args = parser.parse_args()

    if args.cmd == "eval":
        print(json.dumps(run_all(), ensure_ascii=False, indent=2))
    elif args.cmd == "memory":
        skill = AssistSkill()
        print(skill.manage_memory(" ".join(args.text)).text)


if __name__ == "__main__":
    main()
