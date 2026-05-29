"""Interactive REPL for inspecting bot state.

Port of scala-bot/src/scala_bot/console.scala.

Supports `hand <name>` and `navigate <turn|+|-|++|-->` commands. Designed to run
alongside a live BotClient (Stage 5), but also usable standalone via the
`debug_loop()` helper for testing.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass

from hanabi_bot.basics.game import Game
from hanabi_bot.basics.identity import Identity


class NavTarget(enum.Enum):
    PREV_ROUND = "--"
    PREV = "-"
    NEXT = "+"
    NEXT_ROUND = "++"


@dataclass(frozen=True)
class HandCmd:
    name: str
    from_: str | None = None


@dataclass(frozen=True)
class NavCmd:
    target: NavTarget | int


ConsoleCmd = HandCmd | NavCmd


def parse_console_cmd(line: str) -> ConsoleCmd | None:
    """Parse a single REPL line; None if unrecognized."""
    parts = line.strip().split()
    if not parts:
        return None
    head = parts[0]
    if head == "hand":
        if len(parts) == 2:
            return HandCmd(name=parts[1])
        if len(parts) == 3:
            return HandCmd(name=parts[1], from_=parts[2])
        return None
    if head in ("navigate", "nav"):
        if len(parts) != 2:
            return None
        arg = parts[1]
        if arg in ("++", "+", "-", "--"):
            return NavCmd(target=NavTarget(arg))
        try:
            return NavCmd(target=int(arg))
        except ValueError:
            return None
    return None


def hand_string(game: Game, name: str, from_: str | None = None) -> str:
    """Return a human-readable description of `name`'s hand from `from_`'s perspective.

    `from_` defaults to the common perspective. If a name isn't in the game, returns an error.
    """
    state = game.state
    try:
        target_idx = state.names.index(name)
    except ValueError:
        return f"unknown player {name!r}"
    if from_ is None:
        perspective = game.common
    else:
        try:
            from_idx = state.names.index(from_)
        except ValueError:
            return f"unknown perspective {from_!r}"
        perspective = game.players[from_idx]

    parts: list[str] = []
    for slot, order in enumerate(state.hands[target_idx], 1):
        thought = perspective.thoughts[order]
        infs = ",".join(
            state.log_id(i) for i in sorted(thought.inferred, key=Identity.to_ord)
        )
        parts.append(f"slot {slot} (order {order}): [{infs}]")
    return f"{name}'s hand:\n  " + "\n  ".join(parts)


def navigate_target(game: Game, target: NavTarget | int) -> int:
    """Compute the desired turn number given a navigation argument."""
    if isinstance(target, int):
        return target
    current = game.state.turn_count
    if target == NavTarget.NEXT:
        return current + 1
    if target == NavTarget.PREV:
        return current - 1
    if target == NavTarget.NEXT_ROUND:
        return current + game.state.num_players
    return current - game.state.num_players  # PREV_ROUND


def debug_loop(get_game: Callable[[], Game | None]) -> None:
    """Blocking REPL: reads lines from stdin, applies inspection commands.

    Exits on EOF or empty input. Used standalone (e.g. in replay mode).
    """
    print("console ready. Commands: hand <name> [from], navigate <turn|+|-|++|-->. Ctrl+D to exit.")
    while True:
        try:
            line = input("> ")
        except EOFError:
            print()
            return
        cmd = parse_console_cmd(line)
        if cmd is None:
            print("unknown command")
            continue
        game = get_game()
        if game is None:
            print("(no game loaded)")
            continue
        if isinstance(cmd, HandCmd):
            print(hand_string(game, cmd.name, cmd.from_))
        elif isinstance(cmd, NavCmd):
            t = navigate_target(game, cmd.target)
            print(f"navigate -> turn {t} (no live game machinery in standalone console)")
