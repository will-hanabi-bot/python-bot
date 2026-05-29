"""Replay a finished game from hanab.live (or a local seed file) through the bot.

Port of scala-bot/src/scala_bot/replay.scala.

Run:
    python -m hanabi_bot replay id=123456 index=0
    python -m hanabi_bot replay file=seeds/42.json index=0
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from hanabi_bot.basics.action import (
    DrawAction,
    GameOverAction,
    PerformAction,
    PerformColour,
    PerformDiscard,
    PerformPlay,
    PerformRank,
    PerformTerminate,
    PlayAction,
    TurnAction,
    perform_action_from_json,
)
from hanabi_bot.basics.identity import Identity
from hanabi_bot.basics.options import TableOptions
from hanabi_bot.basics.state import HAND_SIZE, State
from hanabi_bot.basics.variant import get_variant
from hanabi_bot.conventions.reactor import Reactor


@dataclass(frozen=True, slots=True)
class GameData:
    players: tuple[str, ...]
    deck: tuple[Identity, ...]
    actions: tuple[PerformAction, ...]
    options: TableOptions


def fetch_id(game_id: int | str, *, timeout: float = 30.0) -> GameData:
    """Fetch a game from hanab.live's /export endpoint."""
    url = f"https://hanab.live/export/{game_id}"
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return _parse_data(resp.json())


def fetch_file(path: Path | str) -> GameData:
    """Load a game from a local JSON file (e.g. one written by self-play)."""
    return _parse_data(json.loads(Path(path).read_text(encoding="utf-8")))


def _parse_data(data: dict[str, Any]) -> GameData:
    players = tuple(data["players"])
    deck = tuple(
        Identity(int(c["suitIndex"]), int(c["rank"])) for c in data["deck"]
    )
    actions = tuple(perform_action_from_json(a) for a in data["actions"])
    opts_raw = data.get("options", {})
    options = TableOptions(
        num_players=len(players),
        variant_name=opts_raw.get("variant", opts_raw.get("variantName", "No Variant")),
        deck_plays=bool(opts_raw.get("deckPlays", False)),
        empty_clues=bool(opts_raw.get("emptyClues", False)),
    )
    return GameData(players=players, deck=deck, actions=actions, options=options)


def _perform_to_action(  # noqa: PLR0911
    perform: PerformAction, state: State, current_player_index: int, deck: tuple[Identity, ...]
):  # type: ignore[no-untyped-def]
    """Same as self_play._perform_to_action but for replay (deck is a tuple)."""
    from hanabi_bot.basics.action import (
        ClueAction,
        DiscardAction,
    )
    from hanabi_bot.basics.clue import BaseClue, ClueKind

    if isinstance(perform, PerformPlay):
        order = perform.target
        if order >= len(deck):
            return DiscardAction(current_player_index, order, -1, -1, True)
        id_ = deck[order]
        if state.is_playable(id_):
            return PlayAction(current_player_index, order, id_.suit_index, id_.rank)
        return DiscardAction(current_player_index, order, id_.suit_index, id_.rank, True)
    if isinstance(perform, PerformDiscard):
        order = perform.target
        id_ = deck[order]
        return DiscardAction(current_player_index, order, id_.suit_index, id_.rank, False)
    if isinstance(perform, PerformColour):
        clue = BaseClue(ClueKind.COLOUR, perform.value)
        return ClueAction(
            current_player_index,
            perform.target,
            tuple(state.clue_touched(state.hands[perform.target], 0, perform.value)),
            clue,
        )
    if isinstance(perform, PerformRank):
        clue = BaseClue(ClueKind.RANK, perform.value)
        return ClueAction(
            current_player_index,
            perform.target,
            tuple(state.clue_touched(state.hands[perform.target], 1, perform.value)),
            clue,
        )
    if isinstance(perform, PerformTerminate):
        return GameOverAction(perform.value, perform.target)
    raise ValueError(f"unknown PerformAction: {perform!r}")


def process_game(game: Reactor, data: GameData, index: int) -> Reactor:
    """Replay the action list through `game`, treating `index` as our_player_index.

    Port of replay.scala `processGame` (lines 87-120).
    """
    g = game.copy_with(catchup=True)

    # Deal: index sees -1/-1 for own cards, real ids for everyone else.
    for player_index in range(g.state.num_players):
        for _ in range(HAND_SIZE[g.state.num_players]):
            order = g.state.next_card_order
            own = player_index == index
            suit = -1 if own else data.deck[order].suit_index
            rank = -1 if own else data.deck[order].rank
            g = g.handle_action(DrawAction(player_index, order, suit, rank))

    for perform in data.actions:
        cpi = g.state.current_player_index
        action = _perform_to_action(perform, g.state, cpi, data.deck)
        g = g.handle_action(action)
        if g.state.next_card_order < len(data.deck) and isinstance(
            perform, (PerformPlay, PerformDiscard)
        ):
            order = g.state.next_card_order
            own = cpi == index
            suit = -1 if own else data.deck[order].suit_index
            rank = -1 if own else data.deck[order].rank
            g = g.handle_action(DrawAction(cpi, order, suit, rank))
        if isinstance(perform, PerformPlay) and g.state.strikes == 3:
            g = g.handle_action(GameOverAction(0, cpi))
            break
        g = g.handle_action(TurnAction(g.state.turn_count, g.state.next_player_index(cpi)))

    return g.copy_with(catchup=False)


def replay(
    *,
    game_id: int | str | None = None,
    file: str | Path | None = None,
    index: int = 0,
) -> Reactor:
    """Fetch a game (by hanab.live id or file path) and replay it. Returns the final game."""
    if game_id is None and file is None:
        raise ValueError("Must provide either game_id= or file=")
    data = fetch_id(game_id) if game_id is not None else fetch_file(file)  # type: ignore[arg-type]
    if index >= len(data.players):
        raise ValueError(f"Replay only has {len(data.players)} players")

    variant = get_variant(data.options.variant_name)
    state = State.create(
        names=data.players, our_player_index=index, variant=variant, options=data.options
    )
    game = Reactor.create(0, state, in_progress=False)
    return process_game(game, data, index)


def main(args: dict[str, str]) -> int:
    """CLI entry."""
    game_id = args.get("id")
    file_arg = args.get("file")
    index = int(args.get("index", "0"))
    if game_id is None and file_arg is None:
        print("error: must provide id= or file=")
        return 2
    final = replay(game_id=game_id, file=file_arg, index=index)
    state = final.state
    print(f"Replay complete. Score {state.score}/{state.max_score}, strikes {state.strikes}.")
    print(f"Play stacks: {state.play_stacks}")
    print(f"Last move: {final.last_move}")
    return 0
