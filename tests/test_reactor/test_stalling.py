"""Reactor stalling-clue scenarios.

Port of scala-bot/src/test/reactor/stalling.scala.
"""

from __future__ import annotations

from hanabi_bot.basics.card import CardStatus
from hanabi_bot.conventions.reactor import Reactor

from ..conftest import Player, has_status, setup, take_turn


def test_understands_a_bad_play() -> None:
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["r4", "r4", "y4", "y4", "g4"],
            ["g1", "p4", "p4", "b4", "g4"],
        ],
    )
    g = take_turn(g, "Alice clues blue to Cathy")
    # Bob's slot 3 should be called to discard, since p4 isn't playable.
    has_status(g, Player.BOB, 3, CardStatus.CALLED_TO_DISCARD)


def test_reacts_to_cathy_color_when_bob_unloaded() -> None:
    """New convention: Alice→Cathy + Bob unloaded forces reactive (no stable).

    Pre-change: this was 'doesnt_react_to_cathy_play' — the locked/8-clue branch unconditionally
    went stable, so Cathy's slot 3 (p1) got CalledToPlay and Bob untouched. Per the new rule,
    Alice→Cathy clues with Bob having no obvious playables are interpreted reactively instead.

    Here: focus_slot=4 (blue clue, focus is b4 in Cathy's slot 4), Cathy's playable is p1 in slot 3
    → target_slot=3, react_slot=calc_slot(4,3)=1. Reactive dc+play (color) → Bob's slot 1
    marked CalledToDiscard.
    """
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["r4", "r4", "y4", "y4", "g4"],
            ["g4", "p4", "p1", "b4", "g5"],
        ],
    )
    g = take_turn(g, "Alice clues blue to Cathy")
    has_status(g, Player.BOB, 1, CardStatus.CALLED_TO_DISCARD)


def test_reacts_to_cathy_1s() -> None:
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["r4", "r4", "y4", "y4", "g1"],
            ["g4", "p4", "p1", "b1", "g5"],
        ],
    )
    g = take_turn(g, "Alice clues 1 to Cathy")
    # Bob's slot 5 is called to play.
    has_status(g, Player.BOB, 5, CardStatus.CALLED_TO_PLAY)


def test_reacts_to_cathy_rank_when_bob_unloaded() -> None:
    """New convention: Alice→Cathy + Bob unloaded forces reactive even when the rank clue
    has no equivalent color clue to Cathy.

    Pre-change: this was 'doesnt_react_to_untargetable_cathy_1s' — the locked/8-clue branch
    kept the rank-1 clue stable. Per the new rule, the same setup interprets reactively.
    Reactive play+play → Bob's slot 5 (g1) marked CalledToPlay.
    """
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["r4", "r4", "y4", "y4", "g1"],
            ["g4", "p4", "p1", "p3", "g5"],
        ],
    )
    g = take_turn(g, "Alice clues 1 to Cathy")
    has_status(g, Player.BOB, 5, CardStatus.CALLED_TO_PLAY)
