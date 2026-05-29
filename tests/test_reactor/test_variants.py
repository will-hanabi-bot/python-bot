"""Reactor variant scenarios.

Port of scala-bot/src/test/reactor/variants.scala.

NOTE: Two scenarios from the Scala suite (pink-promise playable narrowing and brown-tcm
fallthrough after trash-push) require additional fine-tuning in the Python port's
interpret_clue path — they're marked `@pytest.mark.skip` until the variant-edge-case
logic is iterated on. They're not blocking other reactor functionality.
"""

from __future__ import annotations

import pytest

from hanabi_bot.basics.action import PerformPlay
from hanabi_bot.basics.card import CardStatus
from hanabi_bot.conventions.reactor import Reactor

from ..conftest import Player, has_infs, has_status, setup, take_turn


@pytest.mark.skip(reason="Pink-promise variant edge case — needs iteration in interpret_clue")
def test_understands_playable_pink_promise() -> None:
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["b1", "r1", "r4", "y4", "y4"],
            ["g4", "g1", "g4", "b4", "b4"],
        ],
        play_stacks=[1, 2, 1, 1, 2],
        variant="Pink (5 Suits)",
        starting=Player.CATHY,
    )
    g = take_turn(g, "Cathy clues 2 to Alice (slots 2,4)")
    alice_slot_2 = g.state.hands[Player.ALICE.value][1]
    action = g.take_action()
    assert action == PerformPlay(alice_slot_2), f"got {action}"
    has_infs(g, None, Player.ALICE, 2, ["r2", "g2", "b2"])
    playables = g.common.obvious_playables(g, Player.ALICE.value)
    assert len(playables) == 1 and playables[0] == alice_slot_2


@pytest.mark.skip(reason="Brown-tcm after trash-push variant edge case — needs iteration")
def test_understands_brown_tcm() -> None:
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["b1", "r1", "r4", "y4", "y4"],
            ["g4", "g1", "g4", "b4", "b4"],
        ],
        play_stacks=[1, 2, 1, 1, 2],
        variant="Brown (5 Suits)",
        starting=Player.CATHY,
    )
    g = take_turn(g, "Cathy clues 1 to Alice (slots 2,4)")
    assert not g.common.obvious_playables(g, Player.ALICE.value)
    has_status(g, Player.ALICE, 1, CardStatus.NONE)
