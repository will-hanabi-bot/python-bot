"""Entry point: dispatches to a subcommand or runs the live bot.

Subcommands:
    python -m hanabi_bot                             # live bot (default; needs .env)
    python -m hanabi_bot index=0 bot_to_join=create  # live bot with options
    python -m hanabi_bot self-play games=10 seed=0
    python -m hanabi_bot replay id=123456 index=0
    python -m hanabi_bot replay file=seeds/42.json index=0
    python -m hanabi_bot analyze id=123456
    python -m hanabi_bot analyze file=seeds/42.json
"""

from __future__ import annotations

import asyncio
import sys

import dotenv

from .logging_setup import setup_logging
from .net.auth import login
from .net.commands import BotClient
from .net.ws_transport import BotTransport
from .settings import BotConfig, parse_argv


async def _async_main(config: BotConfig, cookie: str) -> None:
    """Open the WebSocket, wire BotClient as the message handler, run until stopped."""
    transport = BotTransport(
        ws_url=config.ws_url,
        cookie=cookie,
        on_message=lambda c, p: None,  # placeholder; rewired below
    )
    client = BotClient(transport=transport, config=config)
    transport.on_message = client.handle_message
    try:
        await transport.run()
    except KeyboardInterrupt:
        await transport.stop()


def _run_live(args: dict[str, str]) -> int:
    dotenv.load_dotenv(override=False)
    config = BotConfig.from_env(args)
    setup_logging(config.username)
    cookie = login(config.login_url, config.username, config.password)
    print(f"got cookie ({len(cookie)} bytes); connecting to {config.ws_url}")
    try:
        asyncio.run(_async_main(config, cookie))
    except KeyboardInterrupt:
        print("interrupted; exiting")
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)

    # Detect subcommand: first positional arg that doesn't contain '='.
    subcommand: str | None = None
    if raw_args and "=" not in raw_args[0]:
        subcommand = raw_args[0]
        raw_args = raw_args[1:]

    args = parse_argv(raw_args)

    if subcommand is None:
        return _run_live(args)
    if subcommand == "self-play":
        from .cli.self_play import main as sp_main

        return sp_main(args)
    if subcommand == "replay":
        from .cli.replay import main as rp_main

        return rp_main(args)
    if subcommand == "analyze":
        from .cli.analyze import main as an_main

        return an_main(args)
    print(f"unknown subcommand {subcommand!r}; available: self-play, replay, analyze")
    print("(or run with no subcommand to start the live bot)")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
