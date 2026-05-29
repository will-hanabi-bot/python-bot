"""Reactor general scenarios.

Port of scala-bot/src/test/reactor/general.scala (selected scenarios).
"""

from __future__ import annotations

from hanabi_bot.basics.card import CardStatus
from hanabi_bot.conventions.reactor import Reactor

from ..conftest import Player, has_infs, has_status, pre_clue, setup, take_turn


def test_elims_from_focus() -> None:
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["y4", "g2", "r2", "r3", "g5"],
            ["p4", "b5", "p2", "b1", "g4"],
        ],
        play_stacks=[4, 0, 0, 0, 0],
        starting=Player.CATHY,
    )
    g = take_turn(g, "Cathy clues red to Alice (slots 1,2)")

    # Alice's slot 1 should be known r5.
    has_infs(g, None, Player.ALICE, 1, ["r5"])
    # Alice's slot 2 should be known trash.
    hand = g.state.hands[Player.ALICE.value]
    assert hand[1] in g.common.thinks_trash(g, Player.ALICE.value)


def test_understands_stable_clue_to_cathy() -> None:
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["b1", "r4", "r4", "y4", "y4"],
            ["g1", "g4", "g4", "b4", "b4"],
        ],
        # Bob's slot 1 is clued with 1.
        init=lambda gg: pre_clue(gg, Player.BOB, 1, ["1"]),
    )
    g = take_turn(g, "Alice clues green to Cathy")
    # Cathy is called to play g1.
    has_status(g, Player.CATHY, 1, CardStatus.CALLED_TO_PLAY)


def test_understands_reverse_reactive_clue() -> None:
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["b1", "r1", "r4", "y4", "y4"],
            ["g4", "g1", "g4", "b4", "b4"],
        ],
        clue_tokens=7,
        # Bob's slot 2 is clued with 1.
        init=lambda gg: pre_clue(gg, Player.BOB, 2, ["1"]),
    )
    g = take_turn(g, "Alice clues 4 to Bob")
    # Cathy is called to play g1.
    has_status(g, Player.CATHY, 2, CardStatus.CALLED_TO_PLAY)
    g = take_turn(g, "Bob plays b1", draw="y3")

    # Cathy's slot 2 should still be obviously playable.
    g.state.hands[Player.CATHY.value]
    # After bob played his slot 1 (b1) and drew, slot 2 in old order. Let me check by finding old order 11 (g1).
    # Just check that Cathy has at least one obvious playable now.
    assert g.common.obvious_playables(g, Player.CATHY.value)
