"""Replay a finished game and emit per-turn analysis comments.

Port of scala-bot/src/scala_bot/analyze.scala.

Walks the action list from each player's POV and prints comments where the bot's
take_action diverges from the actual move.

Run:
    python -m hanabi_bot analyze id=123456
    python -m hanabi_bot analyze file=seeds/42.json
"""

from __future__ import annotations

from hanabi_bot.basics.action import (
    DrawAction,
    GameOverAction,
    TurnAction,
)
from hanabi_bot.basics.options import TableOptions
from hanabi_bot.basics.state import HAND_SIZE, State
from hanabi_bot.basics.variant import get_variant
from hanabi_bot.conventions.reactor import Reactor

from .replay import GameData, _perform_to_action, fetch_file, fetch_id


def analyze_game(game: Reactor, data: GameData) -> list[str]:
    """Reconstruct the game from `data` (all cards visible from POV 0) and call game.analyze().

    Port of analyze.scala `analyzeGame` (lines 42-73).
    """
    g = game.copy_with(catchup=True)

    # Deal — every card visible (we're analyzing from outside).
    for player_index in range(g.state.num_players):
        for _ in range(HAND_SIZE[g.state.num_players]):
            order = g.state.next_card_order
            id_ = data.deck[order]
            g = g.handle_action(DrawAction(player_index, order, id_.suit_index, id_.rank))

    # Apply actions.
    for perform in data.actions:
        cpi = g.state.current_player_index
        action = _perform_to_action(perform, g.state, cpi, data.deck)
        g = g.handle_action(action)
        if g.state.next_card_order < len(data.deck) and hasattr(action, "requires_draw") and action.requires_draw:
            order = g.state.next_card_order
            id_ = data.deck[order]
            g = g.handle_action(DrawAction(cpi, order, id_.suit_index, id_.rank))
        from hanabi_bot.basics.action import PerformPlay
        if isinstance(perform, PerformPlay) and g.state.strikes == 3:
            g = g.handle_action(GameOverAction(0, cpi))
            break
        g = g.handle_action(TurnAction(g.state.turn_count, g.state.next_player_index(cpi)))

    g = g.copy_with(catchup=False)
    return g.analyze()


def main(args: dict[str, str]) -> int:
    game_id = args.get("id")
    file_arg = args.get("file")
    if game_id is None and file_arg is None:
        print("error: must provide id= or file=")
        return 2
    data = fetch_id(game_id) if game_id is not None else fetch_file(file_arg)  # type: ignore[arg-type]
    variant = get_variant(data.options.variant_name)
    opts = TableOptions(
        num_players=len(data.players),
        variant_name=variant.name,
        deck_plays=data.options.deck_plays,
        empty_clues=data.options.empty_clues,
    )
    state = State.create(
        names=data.players, our_player_index=0, variant=variant, options=opts
    )
    game = Reactor.create(0, state, in_progress=False)
    comments = analyze_game(game, data)
    for c in comments:
        print(c)
    if not comments:
        print("(no notable mistakes or suggestions)")
    return 0
