"""Bot configuration: read .env + argv to produce a BotConfig.

Port of scala-bot/src/scala_bot/bot.scala BotConfig.fromEnv (lines 32-61).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BotConfig:
    """Resolved bot configuration."""

    username: str
    password: str
    host: str
    index: int
    bot_to_join: str | None = None  # username to join, or "create" to make a new table, or None to idle
    convention: str = "Reactor1"
    table_name: str = "bots"
    max_num_players: int = 5
    disconnect_on_game_end: bool = False
    use_https: bool = True

    @property
    def login_url(self) -> str:
        protocol = "https" if self.use_https else "http"
        return f"{protocol}://{self.host}/login"

    @property
    def ws_url(self) -> str:
        protocol = "wss" if self.use_https else "ws"
        return f"{protocol}://{self.host}/ws"

    @classmethod
    def from_env(cls, args: dict[str, str]) -> BotConfig:
        """Build from a parsed-args dict plus environment variables.

        `args` should contain at least `index=<N>`. Other keys override defaults.
        """
        index = int(args.get("index", "0"))
        username = os.environ.get(f"HANABI_USERNAME{index}") or args.get("username")
        password = os.environ.get(f"HANABI_PASSWORD{index}") or args.get("password")
        if not username:
            raise RuntimeError(f"Missing HANABI_USERNAME{index} env variable")
        if not password:
            raise RuntimeError(f"Missing HANABI_PASSWORD{index} env variable")
        host = os.environ.get("HANABI_HOST") or args.get("host", "hanab.live")
        # localhost / 127.0.0.1 → no TLS
        use_https = not (host.startswith("localhost") or host.startswith("127.") or host.startswith("0.0.0.0"))

        return cls(
            username=username,
            password=password,
            host=host,
            index=index,
            bot_to_join=args.get("bot_to_join"),
            convention=args.get("convention", "Reactor1"),
            table_name=args.get("table", "bots"),
            max_num_players=int(args.get("max_players", "5")),
            disconnect_on_game_end=args.get("disconnect_on_game_end", "false").lower() == "true",
            use_https=use_https,
        )


def parse_argv(argv: list[str]) -> dict[str, str]:
    """Parse `key=value` style args (matches the Scala bot's argv format)."""
    out: dict[str, str] = {}
    for a in argv:
        if "=" not in a:
            raise ValueError(f"Invalid argument {a!r}")
        k, _, v = a.partition("=")
        out[k] = v
    return out
