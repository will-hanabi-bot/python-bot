"""Unit tests for endgame/helper.py."""

from __future__ import annotations

from hanabi_bot.conventions.reactor import Reactor
from hanabi_bot.endgame.helper import (
    find_must_plays,
    remaining_remove,
    trivially_winnable,
    unwinnable_state,
)

from ..conftest import Player, setup


def test_find_must_plays_returns_only_useful_lone_copies() -> None:
    """A card is a must-play only if it's useful AND all remaining unseen copies are in the hand."""
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["r1", "r5", "g3", "b4", "p2"],  # r5 is critical (only copy in deck)
            ["y4", "y5", "g4", "b5", "p5"],
        ],
        play_stacks=[0, 0, 0, 0, 0],
    )
    bob_hand = g.state.hands[Player.BOB.value]
    must = find_must_plays(g.state, bob_hand)
    # r5 is the only copy in Bob's hand AND only copy globally → must-play.
    assert any(id_.suit_index == 0 and id_.rank == 5 for id_ in must)


def test_unwinnable_state_pace_negative() -> None:
    """pace < 0 → unwinnable."""
    import dataclasses

    g = setup(
        Reactor.create,
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "y2", "g3", "b4", "p5"]],
        play_stacks=[0, 0, 0, 0, 0],
    )
    # Force a negative-pace state by depleting cards_left + discarding criticals.
    state = dataclasses.replace(g.state, cards_left=0, max_ranks=(2, 2, 2, 2, 2), play_stacks=(0, 0, 0, 0, 0))
    assert unwinnable_state(state, 0)


def test_unwinnable_state_normal_mid_game() -> None:
    g = setup(
        Reactor.create,
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "y2", "g3", "b4", "p5"]],
    )
    assert not unwinnable_state(g.state, 0)


def test_trivially_winnable_returns_error_outside_endgame() -> None:
    g = setup(
        Reactor.create,
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "y2", "g3", "b4", "p5"]],
    )
    # endgame_turns is None → trivially_winnable returns error string.
    assert isinstance(trivially_winnable(g, 0), str)


def test_remaining_remove_decrements_then_deletes() -> None:
    from hanabi_bot.basics.identity import Identity

    r1 = Identity(0, 1)
    y2 = Identity(1, 2)
    rem = {r1: 2, y2: 1}
    rem2 = remaining_remove(rem, r1)
    assert rem2[r1] == 1
    assert rem2[y2] == 1
    rem3 = remaining_remove(rem2, r1)
    assert r1 not in rem3
    rem4 = remaining_remove(rem3, y2)
    assert y2 not in rem4
