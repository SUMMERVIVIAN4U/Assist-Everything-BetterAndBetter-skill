from __future__ import annotations

import argparse
import json

from .evaluator import run_all
from .skill import AssistSkill


def main() -> None:
    parser = argparse.ArgumentParser(prog="assist-better-skill")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("eval")
    sub.add_parser("profile")
    sub.add_parser("snapshot")
    sub.add_parser("layers")
    sub.add_parser("privacy")
    chat = sub.add_parser("chat")
    chat.add_argument("text", nargs="+")
    manage = sub.add_parser("memory")
    manage.add_argument("text", nargs="+")
    args = parser.parse_args()

    if args.cmd == "eval":
        print(json.dumps(run_all(), ensure_ascii=False, indent=2))
    elif args.cmd == "profile":
        print(json.dumps(AssistSkill().memory_profile(), ensure_ascii=False, indent=2))
    elif args.cmd == "snapshot":
        print(json.dumps(AssistSkill().compact_snapshot(), ensure_ascii=False, indent=2))
    elif args.cmd == "layers":
        print(json.dumps(AssistSkill().memory_layers(), ensure_ascii=False, indent=2))
    elif args.cmd == "privacy":
        print(json.dumps(AssistSkill().privacy_report(), ensure_ascii=False, indent=2))
    elif args.cmd == "chat":
        skill = AssistSkill()
        print(json.dumps(skill.process_message(" ".join(args.text)).to_dict(), ensure_ascii=False, indent=2))
    elif args.cmd == "memory":
        skill = AssistSkill()
        print(skill.manage_memory(" ".join(args.text)).text)


if __name__ == "__main__":
    main()
