from __future__ import annotations

import argparse
import json

from .direct_agent import (
    DEFAULT_SESSION_ID,
    build_direct_agent,
    config_report,
    direct_agent_config,
    direct_agent_session_turn,
    direct_agent_turn,
    load_direct_env,
    memory_manage,
    memory_pack,
    memory_write,
    print_json,
)
from .evaluator import run_all
from .skill import AssistSkill


def _add_agent_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", default=None, help="LLM provider: deepseek_pro, deepseek_flash, mimo.")
    parser.add_argument("--memory-dir", default=None, help="Memory directory. Defaults to ASSIST_MEMORY_DIR or memories/default.")
    parser.add_argument("--memory-backend", default=None, choices=["local", "mem0_hosted"], help="Long-term memory backend.")
    parser.add_argument("--memory-enabled", default=None, choices=["0", "1"], help="Enable or disable memory for this process.")
    parser.add_argument("--profile", default=None, help="Runtime config profile. Defaults to ASSIST_RUNTIME_PROFILE or default.")
    parser.add_argument("--allow-no-llm", action="store_true", help="Do not require a configured real LLM provider.")


def _memory_enabled_arg(value: str | None) -> bool | None:
    if value is None:
        return None
    return value != "0"


def main() -> None:
    parser = argparse.ArgumentParser(prog="assist-better-skill")
    parser.add_argument("--env-file", default=".env", help="Path to env file loaded before running commands.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("eval")
    sub.add_parser("profile")
    sub.add_parser("snapshot")
    sub.add_parser("layers")
    sub.add_parser("privacy")
    chat = sub.add_parser("chat")
    _add_agent_args(chat)
    chat.add_argument("--json", action="store_true", help="Print full turn trace JSON instead of assistant text.")
    chat.add_argument("--raw-skill", action="store_true", help="Bypass the Workbench-equivalent LLM agent wrapper.")
    chat.add_argument("text", nargs="+")
    agent_turn = sub.add_parser("agent-turn")
    _add_agent_args(agent_turn)
    agent_turn.add_argument("--session", default=DEFAULT_SESSION_ID, help="Persistent direct Skill session id.")
    agent_turn.add_argument("--reset-session", action="store_true", help="Reset this direct session without clearing long-term memory.")
    agent_turn.add_argument("--json", action="store_true", help="Print full turn trace JSON instead of assistant text.")
    agent_turn.add_argument("text", nargs="+")
    agent_chat = sub.add_parser("agent-chat")
    _add_agent_args(agent_chat)
    config = sub.add_parser("config")
    _add_agent_args(config)
    pack = sub.add_parser("memory-pack")
    _add_agent_args(pack)
    pack.add_argument("--context", default="", help="Recent host-agent conversation context to guide retrieval.")
    pack.add_argument("--session", default="host-default", help="Stable host-agent session id for current-task memories.")
    pack.add_argument("text", nargs="+")
    write = sub.add_parser("memory-write")
    _add_agent_args(write)
    write.add_argument("--context", default="", help="Recent host-agent conversation context to guide extraction/update.")
    write.add_argument("--session", default="host-default", help="Stable host-agent session id for current-task memories.")
    write.add_argument("text", nargs="+")
    manage2 = sub.add_parser("memory-manage")
    _add_agent_args(manage2)
    manage2.add_argument("--json", action="store_true", help="Print full management payload instead of text.")
    manage2.add_argument("--session", default="host-default", help="Stable host-agent session id for current-task memories.")
    manage2.add_argument("text", nargs="+")
    manage = sub.add_parser("memory")
    manage.add_argument("text", nargs="+")
    args = parser.parse_args()
    load_direct_env(args.env_file)

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
        message = " ".join(args.text)
        if args.raw_skill:
            skill = AssistSkill()
            print(json.dumps(skill.process_message(message).to_dict(), ensure_ascii=False, indent=2))
            return
        payload = direct_agent_turn(
            message,
            provider=args.provider,
            memory_dir=args.memory_dir,
            memory_backend=args.memory_backend,
            memory_enabled=_memory_enabled_arg(args.memory_enabled),
            require_llm=not args.allow_no_llm,
            profile=args.profile,
        )
        if args.json:
            print_json(payload)
        else:
            print(payload["turn"]["assistant"]["content"])
    elif args.cmd == "agent-turn":
        payload = direct_agent_session_turn(
            " ".join(args.text),
            session_id=args.session,
            provider=args.provider,
            memory_dir=args.memory_dir,
            memory_backend=args.memory_backend,
            memory_enabled=_memory_enabled_arg(args.memory_enabled),
            require_llm=not args.allow_no_llm,
            profile=args.profile,
            reset_session=args.reset_session,
        )
        if args.json:
            print_json(payload)
        else:
            print(payload["turn"]["assistant"]["content"])
    elif args.cmd == "agent-chat":
        _run_agent_chat(args)
    elif args.cmd == "config":
        print_json(
            config_report(
                provider=args.provider,
                memory_dir=args.memory_dir,
                memory_backend=args.memory_backend,
                memory_enabled=_memory_enabled_arg(args.memory_enabled),
                require_llm=not args.allow_no_llm,
                profile=args.profile,
            )
        )
    elif args.cmd == "memory-pack":
        print_json(
            memory_pack(
                " ".join(args.text),
                context=args.context,
                memory_dir=args.memory_dir,
                memory_backend=args.memory_backend,
                memory_enabled=_memory_enabled_arg(args.memory_enabled),
                profile=args.profile,
                session_id=args.session,
            )
        )
    elif args.cmd == "memory-write":
        print_json(
            memory_write(
                " ".join(args.text),
                context=args.context,
                memory_dir=args.memory_dir,
                memory_backend=args.memory_backend,
                memory_enabled=_memory_enabled_arg(args.memory_enabled),
                profile=args.profile,
                session_id=args.session,
            )
        )
    elif args.cmd == "memory-manage":
        payload = memory_manage(
            " ".join(args.text),
            memory_dir=args.memory_dir,
            memory_backend=args.memory_backend,
            memory_enabled=_memory_enabled_arg(args.memory_enabled),
            profile=args.profile,
            session_id=args.session,
        )
        if args.json:
            print_json(payload)
        else:
            print(payload["response"]["text"])
    elif args.cmd == "memory":
        skill = AssistSkill()
        print(skill.manage_memory(" ".join(args.text)).text)


def _run_agent_chat(args: argparse.Namespace) -> None:
    config = direct_agent_config(
        provider=args.provider,
        memory_dir=args.memory_dir,
        memory_backend=args.memory_backend,
        memory_enabled=_memory_enabled_arg(args.memory_enabled),
        require_llm=not args.allow_no_llm,
        profile=args.profile,
    )
    agent = build_direct_agent(
        provider=config.provider,
        memory_dir=config.memory_dir,
        memory_backend=config.memory_backend,
        memory_enabled=config.memory_enabled,
        require_llm=config.require_llm,
        profile=config.profile,
    )
    print(f"assist direct agent ready: provider={config.provider}, memory={config.memory_backend}, dir={config.memory_dir}")
    print("输入消息开始对话；:config 查看配置；:memory 展示记忆；:reset-session 重置会话；:quit 退出。")
    while True:
        try:
            message = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not message:
            continue
        if message in {":quit", ":q", "exit", "quit"}:
            return
        if message == ":config":
            print_json(
                config_report(
                    provider=config.provider,
                    memory_dir=config.memory_dir,
                    memory_backend=config.memory_backend,
                    memory_enabled=config.memory_enabled,
                    require_llm=config.require_llm,
                    profile=config.profile,
                )
            )
            continue
        if message == ":memory":
            response, _ = agent.toolbox.show_memory()
            print(response.text)
            continue
        if message == ":reset-session":
            agent = build_direct_agent(
                provider=config.provider,
                memory_dir=config.memory_dir,
                memory_backend=config.memory_backend,
                memory_enabled=config.memory_enabled,
                require_llm=config.require_llm,
                profile=config.profile,
            )
            print("Session 已重置；memory 保持不变。")
            continue
        turn = agent.reply(message)
        print(turn.assistant.content)


if __name__ == "__main__":
    main()
