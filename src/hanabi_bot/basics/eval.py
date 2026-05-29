"""Force-clue evaluator: assume `giver` will spend a clue, then evaluate the resulting state.

Port of scala-bot/src/scala_bot/basics/eval.scala.

Used by state_eval to score game-tree branches where a teammate is forced into a clue.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import TYPE_CHECKING

from .action import ClueAction
from .clue import Clue
from .interp import ClueInterp

if TYPE_CHECKING:
    from .game import Game


def force_clue(
    game: Game,
    giver: int,
    advance: Callable[[Game], float],
    only: int | None = None,
    clue_filter: Callable[[Clue], bool] = lambda c: True,
) -> float:
    """Find the best clue `giver` could give and return advance() applied to that hypothetical."""
    state = game.state
    if not state.can_clue:
        return -999.0
    if state.num_players == 2:
        return advance(
            game.with_state(lambda s: dataclasses.replace(s, clue_tokens=s.clue_tokens - 1))
        )

    candidates: list[ClueAction] = []
    for i in range(state.num_players):
        if i == giver or i == state.our_player_index:
            continue
        if only is not None and only != i:
            continue
        for clue in state.all_valid_clues(i):
            if not clue_filter(clue):
                continue
            list_ = tuple(state.clue_touched(state.hands[i], clue.kind.value, clue.value))
            candidates.append(ClueAction(giver, i, list_, clue.base))

    best = -100.0
    for action in candidates:
        hypo = game.simulate(action)
        if hypo.last_move == ClueInterp.MISTAKE:
            continue
        v = advance(hypo)
        if v > best:
            best = v

    if best == -100.0:
        return advance(
            game.with_state(lambda s: dataclasses.replace(s, clue_tokens=s.clue_tokens - 1))
        )
    return best
