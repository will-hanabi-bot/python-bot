"""Smoke tests for the test harness in conftest.py.

These exercise setup(), take_turn(), parse_action(), pre_clue(), fully_known()
and the has_* assertion helpers using the base Game class (no convention).

When the reactor convention lands in Stage 4, reactor-specific test files can
pass `Reactor` as the constructor to setup() and use the same harness.
"""

from __future__ import annotations

import pytest

from hanabi_bot.basics.card import CardStatus
from hanabi_bot.basics.identity import Identity

from .conftest import (
    Player,
    fully_known,
    has_infs,
    has_poss,
    has_status,
    parse_action,
    pre_clue,
    setup,
    str_to_clue,
    take_turn,
)

# --- setup() smoke ---


def test_setup_basic_2p() -> None:
    g = setup(
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["g2", "b1", "r2", "r3", "g5"],
        ],
    )
    assert g.state.num_players == 2
    assert g.state.names == ("Alice", "Bob")
    # Both hands fully dealt
    assert all(len(h) == 5 for h in g.state.hands)
    # Slot 1 of Bob's hand should be g2 (per Scala convention: input[0] = slot 1).
    bob_slot_1 = g.state.hands[1][0]
    assert g.state.deck[bob_slot_1].suit_index == 2
    assert g.state.deck[bob_slot_1].rank == 2
    # Bob's slot 5 = g5 (input[4]).
    bob_slot_5 = g.state.hands[1][4]
    assert g.state.deck[bob_slot_5].suit_index == 2
    assert g.state.deck[bob_slot_5].rank == 5


def test_setup_starting_player() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "r1", "r2", "r3", "r4"]],
        starting=Player.BOB,
    )
    assert g.state.current_player_index == 1


def test_setup_clue_tokens_and_strikes() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "r1", "r2", "r3", "r4"]],
        clue_tokens=3,
        strikes=2,
    )
    assert g.state.clue_tokens == 3
    assert g.state.strikes == 2


def test_setup_with_play_stacks() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["g2", "b1", "r2", "r3", "g5"]],
        play_stacks=[2, 0, 0, 0, 0],
    )
    assert g.state.play_stacks == (2, 0, 0, 0, 0)
    assert g.state.score == 2
    # Played cards advance base_count.
    assert g.state.base_count[Identity(0, 1).to_ord()] == 1
    assert g.state.base_count[Identity(0, 2).to_ord()] == 1
    # cards_left decremented appropriately.
    assert g.state.cards_left == g.state.cards_total - 2 - sum(len(h) for h in g.state.hands)


def test_setup_with_discarded() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["g2", "b1", "r2", "r3", "g5"]],
        discarded=["r1", "y5"],
    )
    # r1 and y5 should be in the discard piles
    assert Identity(0, 1).to_ord() in [
        Identity(suit, rank).to_ord()
        for suit in range(5)
        for rank in range(1, 6)
        if len(g.state.discard_stacks[suit][rank - 1]) > 0
    ]
    # y5 was critical (only copy); discarding it should lower max rank
    assert g.state.max_ranks[1] == 4


def test_setup_with_variant_name() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "y1", "g1", "i1", "i5"]],
        variant="Prism (5 Suits)",
    )
    assert g.state.variant.name == "Prism (5 Suits)"
    # Prism suit is at index 4; rank 1 of prism uses colour 0 in colourable suits.
    assert g.state.variant.suits[4].suit_type.prism


def test_setup_rejects_oversized_hand() -> None:
    with pytest.raises(ValueError):
        setup(
            hands=[
                ["xx", "xx", "xx", "xx", "xx", "xx"],  # 6 cards in 2p game
                ["r1", "r1", "r2", "r3", "r4"],
            ],
        )


def test_setup_rejects_too_many_copies() -> None:
    # Try to put 4 r1s in hands (only 3 exist).
    with pytest.raises(ValueError):
        setup(
            hands=[
                ["xx", "xx", "xx", "xx", "xx"],
                ["r1", "r1", "r1", "r1", "r2"],
            ],
        )


# --- str_to_clue + parse_action ---


def test_str_to_clue_rank() -> None:
    g = setup(hands=[["xx"], ["r1"]])
    bc = str_to_clue(g.state, "3")
    assert bc.value == 3


def test_str_to_clue_colour() -> None:
    g = setup(hands=[["xx"], ["r1"]])
    bc = str_to_clue(g.state, "red")
    assert bc.value == 0  # red is suit 0
    bc = str_to_clue(g.state, "blue")
    assert bc.value == 3


def test_str_to_clue_unknown_colour_raises() -> None:
    g = setup(hands=[["xx"], ["r1"]])
    with pytest.raises(ValueError):
        str_to_clue(g.state, "octarine")


def test_parse_action_clue_to_other() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["g2", "b1", "r2", "r3", "g5"]],
    )
    action = parse_action(g.state, "Alice clues red to Bob")
    assert action.__class__.__name__ == "ClueAction"
    assert action.giver == 0
    assert action.target == 1
    # Bob's red cards are slot 3 (r2) and slot 4 (r3).
    bob_hand = g.state.hands[1]
    red_orders = [bob_hand[2], bob_hand[3]]
    assert set(action.list_) == set(red_orders)


def test_parse_action_clue_to_us_requires_slot() -> None:
    g = setup(hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "r1", "r2", "r3", "r4"]])
    with pytest.raises(ValueError):
        parse_action(g.state, "Bob clues red to Alice")


def test_parse_action_clue_to_us_with_slots() -> None:
    g = setup(hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "r1", "r2", "r3", "r4"]])
    action = parse_action(g.state, "Bob clues red to Alice (slots 1,3)")
    # Slot 1, 3 of Alice's hand
    alice_hand = g.state.hands[0]
    assert set(action.list_) == {alice_hand[0], alice_hand[2]}


def test_parse_action_play_unambiguous() -> None:
    g = setup(hands=[["xx", "xx", "xx", "xx", "xx"], ["g2", "b1", "r2", "r3", "g5"]])
    action = parse_action(g.state, "Bob plays r2")
    assert action.__class__.__name__ == "PlayAction"
    # r2 is at slot 3 (index 2) — order 7 from my earlier trace
    assert action.suit_index == 0
    assert action.rank == 2


def test_parse_action_play_ambiguous_requires_slot() -> None:
    g = setup(hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "r1", "r2", "r3", "r4"]])
    with pytest.raises(ValueError):
        parse_action(g.state, "Bob plays r1")


def test_parse_action_discard_with_8_tokens_raises() -> None:
    g = setup(hands=[["xx", "xx", "xx", "xx", "xx"], ["g2", "b1", "r2", "r3", "g5"]])
    with pytest.raises(ValueError):
        parse_action(g.state, "Bob discards g2")


def test_parse_action_bombs_with_8_tokens_ok() -> None:
    g = setup(hands=[["xx", "xx", "xx", "xx", "xx"], ["g2", "b1", "r2", "r3", "g5"]])
    action = parse_action(g.state, "Bob bombs g2")
    assert action.__class__.__name__ == "DiscardAction"
    assert action.failed is True


def test_parse_action_invalid_raises() -> None:
    g = setup(hands=[["xx"], ["r1"]])
    with pytest.raises(ValueError):
        parse_action(g.state, "Alice does the boogie")


# --- take_turn ---


def test_take_turn_clue_advances_turn() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["g2", "b1", "r2", "r3", "g5"]],
    )
    g = take_turn(g, "Alice clues red to Bob")
    # After Alice's turn it's Bob's.
    assert g.state.current_player_index == 1
    assert g.state.clue_tokens == 7


def test_take_turn_play_with_draw() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["g2", "b1", "r1", "r3", "g5"]],
        starting=Player.BOB,
    )
    g = take_turn(g, "Bob plays r1", draw="b3")
    # play_stacks[red] now 1
    assert g.state.play_stacks[0] == 1
    # Bob drew a b3
    bob_hand = g.state.hands[1]
    new_card_order = bob_hand[0]  # newest = leftmost
    assert g.state.deck[new_card_order].suit_index == 3
    assert g.state.deck[new_card_order].rank == 3
    # Turn advanced back to Alice
    assert g.state.current_player_index == 0


def test_take_turn_play_from_other_requires_draw() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["g2", "b1", "r1", "r3", "g5"]],
        starting=Player.BOB,
    )
    with pytest.raises(ValueError):
        take_turn(g, "Bob plays r1")


def test_take_turn_discard_regains_clue() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["g2", "b1", "r1", "r3", "g5"]],
        starting=Player.BOB,
        clue_tokens=4,
    )
    g = take_turn(g, "Bob discards g2", draw="r4")
    assert g.state.clue_tokens == 5


# --- pre_clue ---


def test_pre_clue_red() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "y2", "g3", "b4", "p5"]],
    )
    # Pre-clue Alice's slot 1 as red.
    g = pre_clue(g, Player.ALICE, slot=1, clues=["red"])
    alice_slot_1 = g.state.hands[0][0]
    # The card is now marked clued.
    assert g.state.deck[alice_slot_1].clued
    # Possible set should be 5 red ids.
    has_poss(g, None, Player.ALICE, 1, ["r1", "r2", "r3", "r4", "r5"])


def test_pre_clue_combined() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "y2", "g3", "b4", "p5"]],
    )
    # Pre-clue Alice's slot 1 as red AND rank 3.
    g = pre_clue(g, Player.ALICE, slot=1, clues=["red", "3"])
    # Possible = intersection = just r3.
    has_poss(g, None, Player.ALICE, 1, ["r3"])


# --- fully_known ---


def test_fully_known_simple() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "y2", "g3", "b4", "p5"]],
    )
    g = fully_known(g, Player.BOB, slot=3, short="g3")
    # Bob's slot 3 (g3) should be fully known.
    has_poss(g, None, Player.BOB, 3, ["g3"])


def test_fully_known_mismatch_raises() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "y2", "g3", "b4", "p5"]],
    )
    # Bob's slot 3 is g3, not r3.
    with pytest.raises(ValueError):
        fully_known(g, Player.BOB, slot=3, short="r3")


def test_fully_known_prism_picks_correct_colour() -> None:
    """For prism, fully_known picks the colour where (rank-1) % colourable_suits == suit_index.

    The result isn't always a singleton — in prism (5 Suits), rank-5 + colour-0 (red) touches
    {red 1..5, prism rank 5} ∩ {all rank 5s} = {r5, i5}. This matches the Scala helper's
    docstring caveat: "only works for simple variants".
    """
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "y2", "g3", "b4", "i5"]],
        variant="Prism (5 Suits)",
    )
    g = fully_known(g, Player.BOB, slot=5, short="i5")
    has_poss(g, None, Player.BOB, 5, ["r5", "i5"])


# --- Assertion helpers ---


def test_has_infs_pass_and_fail() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "y2", "g3", "b4", "p5"]],
    )
    g = pre_clue(g, Player.ALICE, slot=1, clues=["red"])
    # The pre-clue sets both inferred and possible to red ids.
    has_infs(g, None, Player.ALICE, 1, ["r1", "r2", "r3", "r4", "r5"])
    with pytest.raises(AssertionError):
        has_infs(g, None, Player.ALICE, 1, ["r1"])


def test_has_poss_pass_and_fail() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "y2", "g3", "b4", "p5"]],
    )
    g = pre_clue(g, Player.ALICE, slot=1, clues=["red"])
    has_poss(g, None, Player.ALICE, 1, ["r1", "r2", "r3", "r4", "r5"])
    with pytest.raises(AssertionError):
        has_poss(g, None, Player.ALICE, 1, ["r1", "r2"])


def test_has_status() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "y2", "g3", "b4", "p5"]],
    )
    # Initial status is NONE.
    has_status(g, Player.ALICE, slot=1, status=CardStatus.NONE)
    with pytest.raises(AssertionError):
        has_status(g, Player.ALICE, slot=1, status=CardStatus.CALLED_TO_PLAY)


def test_has_per_player_perspective() -> None:
    g = setup(
        hands=[["xx", "xx", "xx", "xx", "xx"], ["r1", "y2", "g3", "b4", "p5"]],
    )
    # From Bob's perspective, Bob's own slot 1 is unknown — possible = all 25.
    has_poss(g, Player.BOB, Player.BOB, 1, [
        f"{c}{r}" for c in "rygbp" for r in "12345"
    ])


# --- Prism scenario from Scala test/reactor/variants.scala ---


def test_prism_clue_narrows_possibilities() -> None:
    """Mirrors the 'understands prism touch' scenario from scala-bot/.../reactor/variants.scala.

    In Prism (5 Suits), a red clue to a card touches all red identities PLUS prism
    identities at rank where (rank-1) % 4 == 0 (i.e. ranks 1 and 5).
    """
    g = setup(
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["g2", "b1", "r2", "r3", "g5"],
        ],
        starting=Player.BOB,
        variant="Prism (5 Suits)",
    )
    # Bob clues red to Alice (slot 1) — this requires telling parse_action which slot.
    g = take_turn(g, "Bob clues red to Alice (slot 1)")
    # Alice's slot 1 possibilities: red 1-5 + prism 1 + prism 5 (rank-1 mod 4 == 0).
    has_poss(g, None, Player.ALICE, 1, ["r1", "r2", "r3", "r4", "r5", "i1", "i5"])
