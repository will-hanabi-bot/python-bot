"""Reactor stable-clue scenarios.

Port of scala-bot/src/test/reactor/stable.scala.
"""

from __future__ import annotations

from hanabi_bot.basics.card import CardStatus
from hanabi_bot.conventions.reactor import Reactor

from ..conftest import Player, has_infs, has_status, setup, take_turn


def test_understands_ref_play() -> None:
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["b1", "g2", "r2", "r3", "g5"],
            ["p4", "b5", "p2", "b1", "g4"],
        ],
    )
    g = take_turn(g, "Alice clues green to Bob")

    has_status(g, Player.BOB, 1, CardStatus.CALLED_TO_PLAY)
    has_infs(g, None, Player.BOB, 1, ["r1", "y1", "b1", "p1"])


def test_understands_gapped_ref_play() -> None:
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["p4", "b1", "p2", "b5", "g4"],
            ["b1", "g2", "r2", "r3", "g5"],
        ],
    )
    g = take_turn(g, "Alice clues purple to Bob")

    has_status(g, Player.BOB, 2, CardStatus.CALLED_TO_PLAY)
    has_infs(g, None, Player.BOB, 2, ["r1", "y1", "g1", "b1"])


def test_understands_chop_ref_play() -> None:
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["b1", "b2", "p2", "b5", "g4"],
            ["b1", "g2", "r2", "r3", "g5"],
        ],
    )
    g = take_turn(g, "Alice clues blue to Bob")

    has_infs(g, None, Player.BOB, 1, ["b1"])


def test_understands_ref_discard() -> None:
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["p4", "p2", "p2", "b5", "g3"],
            ["b1", "g2", "r2", "r3", "g5"],
        ],
    )
    g = take_turn(g, "Alice clues 4 to Bob")

    has_status(g, Player.BOB, 2, CardStatus.CALLED_TO_DISCARD)


def test_gives_ref_discard() -> None:
    from hanabi_bot.basics.action import PerformRank

    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["p4", "p2", "p2", "b5", "g3"],
            ["b3", "g2", "r2", "r3", "g5"],
        ],
    )
    # Alice should clue 4 to Bob.
    action = g.take_action()
    assert action == PerformRank(Player.BOB.value, 4), f"got {action}"


def test_eliminates_direct_ranks_from_focus() -> None:
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["p4", "p2", "p2", "r5", "g3"],
            ["b5", "y4", "g2", "r4", "y3"],
        ],
        starting=Player.CATHY,
        play_stacks=[1, 1, 0, 1, 1],
    )
    g = take_turn(g, "Cathy clues 1 to Alice (slots 2,3)")
    has_status(g, Player.ALICE, 4, CardStatus.NONE)
    has_infs(g, None, Player.ALICE, 2, ["g1"])
    # Alice's slot 3 should be trash.
    hand = g.state.hands[Player.ALICE.value]
    trash = g.common.thinks_trash(g, Player.ALICE.value)
    assert hand[2] in trash, f"slot 3 (order {hand[2]}) not in {trash}"


def test_understands_lock() -> None:
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["p4", "p2", "p2", "b5", "g4"],
            ["b1", "g2", "r2", "r3", "g5"],
        ],
    )
    g = take_turn(g, "Alice clues 4 to Bob")
    assert g.common.obvious_locked(g, Player.BOB.value)


def test_interprets_a_fix() -> None:
    from hanabi_bot.basics.interp import ClueInterp

    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["r1", "p2", "p2", "b5", "g4"],
            ["r1", "g2", "r2", "r3", "g5"],
        ],
        starting=Player.CATHY,
    )
    g = take_turn(g, "Cathy clues 2 to Bob")
    g = take_turn(g, "Alice plays y1 (slot 1)")
    g = take_turn(g, "Bob clues green to Cathy")
    g = take_turn(g, "Cathy plays r1", draw="g3")
    g = take_turn(g, "Alice clues red to Bob")
    # Bob's slot 1 should be known trash now.
    hand = g.state.hands[Player.BOB.value]
    assert hand[0] in g.common.thinks_trash(g, Player.BOB.value)
    has_infs(g, None, Player.BOB, 1, ["r1"])
    assert g.last_move == ClueInterp.FIX
