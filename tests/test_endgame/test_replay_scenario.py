"""Generic late-deck endgame coverage for the per-player-aware solver.

The faithful #1874799 reproduction lives in `test_replay_1874799.py`; this file
keeps lighter-weight synthetic positions that exercise the same code path
(`winnable_simpler` consulting per-player thoughts/meta instead of ground-truth
`state.deck`) on simpler setups, so failures here surface as quickly as possible
without running the full replay simulation.
"""

from __future__ import annotations

from hanabi_bot.basics.action import (
    PerformColour,
    PerformDiscard,
    PerformPlay,
    PerformRank,
)
from hanabi_bot.conventions.reactor import Reactor

from ..conftest import Player, fully_known, setup


def test_late_deck_with_called_to_play_does_not_crash() -> None:
    """Smoke: bot at deck=1 with a called-to-play 5 produces SOME legal action."""
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["r1", "y1", "g1", "b1", "p1"],
            ["r1", "y1", "g1", "b1", "p1"],
        ],
        play_stacks=[5, 5, 5, 5, 4],
    )
    # Alice's slot 1 is the missing p5.
    g = fully_known(g, Player.ALICE, slot=1, short="p5")
    g = g.elim()
    action = g.take_action()
    assert isinstance(action, (PerformPlay, PerformColour, PerformRank, PerformDiscard))


def test_solver_does_not_pick_uncommunicating_clue_in_endgame() -> None:
    """The class of bug from #1874799 / #1875252: with rem_score=1, deck empty, and the
    missing card in another player's hand, the solver must pick a clue that actually
    communicates the card to its owner — not just any clue that "looks winnable" under
    an omniscient model. We verify by setting up a position where bot69's hand is dead
    and the missing p5 sits in yagami's hand; under the old solver, multiple clues
    tied at winrate=1, but only some of them actually mark p5 as needing-to-play.
    """
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["r1", "y1", "g1", "b1", "p1"],
            ["p5", "r1", "y1", "g1", "b1"],
        ],
        play_stacks=[5, 5, 5, 5, 4],
    )
    assert g.state.rem_score == 1
    action = g.take_action()
    # Action must be a clue (no playable in our own hand under this setup).
    assert isinstance(action, (PerformColour, PerformRank)), (
        f"expected the solver to clue; got {action!r}"
    )
    # Clue must target a player who actually holds the must-play card, since otherwise
    # it can't communicate the missing identity.
    assert action.target == Player.CATHY.value, (
        f"clue should target the player holding p5 (Cathy); got target={action.target}"
    )
