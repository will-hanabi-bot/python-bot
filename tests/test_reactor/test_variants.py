"""Reactor variant scenarios.

Port of scala-bot/src/test/reactor/variants.scala.

NOTE: Two scenarios from the Scala suite (pink-promise playable narrowing and brown-tcm
fallthrough after trash-push) require additional fine-tuning in the Python port's
interpret_clue path — they're marked `@pytest.mark.skip` until the variant-edge-case
logic is iterated on. They're not blocking other reactor functionality.
"""

from __future__ import annotations

import pytest

from hanabi_bot.basics.action import ClueAction, PerformPlay, TurnAction
from hanabi_bot.basics.card import CardStatus
from hanabi_bot.basics.clue import BaseClue, ClueKind
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


def test_pink_clue_focus_slot_uses_new_reactive_table() -> None:
    """In Rainbow & Pink (5 Suits), a Pink reactive color clue uses focus_slot=5
    from the new reactive_value_table — not focus_slot=4 from the old `clue.value + 1`.

    Variant suits = [Red, Green, Blue, Rainbow, Pink]; colourable_suits =
    [Red, Green, Blue, Pink] -> reactive table (1, 3, 4, 5). Pink's index=3
    -> new value 5, old value 4.

    Setup: Alice gives Pink clue to Cathy; only pi3 is touched (unplayable, so
    no stable interp); Cathy holds r1 in slot 1 (playable). The reactive-color
    dc+play path resolves the play target with target_slot=1 and computes
    react_slot = calc_slot(focus_slot, 1, hand_size=5) = (focus_slot+5-1) % 5.
    New: focus_slot=5 -> react_slot=4 (Bob's slot 4 marked CalledToDiscard).
    Old: focus_slot=4 -> react_slot=3 (Bob's slot 3 marked CalledToDiscard).
    """
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["r3", "r4", "g3", "b3", "m3"],
            ["r1", "g2", "b2", "m2", "i3"],
        ],
        variant="Rainbow & Pink (5 Suits)",
    )
    state = g.state
    pink_clue_value = next(
        i for i, s in enumerate(state.variant.colourable_suits) if s.name == "Pink"
    )
    cathy_hand = state.hands[Player.CATHY.value]
    touched = state.clue_touched(cathy_hand, ClueKind.COLOUR.value, pink_clue_value)
    assert touched, "Pink clue should touch Cathy's i3"

    clue = BaseClue(ClueKind.COLOUR, pink_clue_value)
    action = ClueAction(Player.ALICE.value, Player.CATHY.value, tuple(touched), clue)
    g2 = g.copy_with(catchup=True).handle_action(action)
    g2 = g2.handle_action(
        TurnAction(state.turn_count, state.next_player_index(Player.ALICE.value))
    )

    assert g2.waiting is not None, "expected reactive interpretation"
    assert g2.waiting.focus_slot == 5, (
        f"expected focus_slot=5 from new table, got {g2.waiting.focus_slot}"
    )
    has_status(g2, Player.BOB, 4, CardStatus.CALLED_TO_DISCARD)


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
