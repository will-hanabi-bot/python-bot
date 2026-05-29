"""Reactor mistake-recovery scenarios.

Port of scala-bot/src/test/reactor/mistakes.scala.
"""

from __future__ import annotations

from hanabi_bot.basics.card import CardStatus
from hanabi_bot.conventions.reactor import Reactor

from ..conftest import Player, has_status, setup, take_turn


def test_cancels_a_missed_reaction_1() -> None:
    """Bob is signalled to play but discards instead — the signal should be cleared."""
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["g1", "r1", "g4", "b4", "b4"],
            ["b1", "r3", "r4", "y4", "y4"],
        ],
    )
    g = take_turn(g, "Alice clues 4 to Cathy")
    has_status(g, Player.BOB, 2, CardStatus.CALLED_TO_PLAY)
    g = take_turn(g, "Bob discards g1", draw="y3")

    bob_s2 = g.state.hands[Player.BOB.value][1]
    assert g.meta[bob_s2].status == CardStatus.NONE
    # Its inferred should equal its possible (info is back to "everything").
    t = g.common.thoughts[bob_s2]
    assert t.inferred.length == t.possible.length
    # Cathy's slot 1 not called to play.
    has_status(g, Player.CATHY, 1, CardStatus.NONE)
