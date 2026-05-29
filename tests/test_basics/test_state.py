"""State construction and immutable update operations."""

from __future__ import annotations

import pytest

from hanabi_bot.basics.identity import Identity
from hanabi_bot.basics.options import TableOptions
from hanabi_bot.basics.state import State
from hanabi_bot.basics.variant import get_variant


@pytest.fixture
def no_variant_state() -> State:
    v = get_variant("No Variant")
    opts = TableOptions(num_players=3, variant_name="No Variant")
    return State.create(
        names=("Alice", "Bob", "Cathy"),
        our_player_index=0,
        variant=v,
        options=opts,
    )


# --- Construction ---


def test_create_initial_state_shape(no_variant_state: State) -> None:
    s = no_variant_state
    assert s.turn_count == 0
    assert s.clue_tokens == 8
    assert s.strikes == 0
    assert s.endgame_turns is None
    assert s.num_players == 3
    assert s.names == ("Alice", "Bob", "Cathy")
    assert s.our_player_index == 0
    assert len(s.hands) == 3
    assert all(h == () for h in s.hands)
    assert s.deck == ()
    assert s.holders == ()
    assert s.play_stacks == (0, 0, 0, 0, 0)
    assert s.max_ranks == (5, 5, 5, 5, 5)
    # Discard stacks: 5 suits, 5 ranks, empty piles
    assert len(s.discard_stacks) == 5
    assert all(len(suit) == 5 for suit in s.discard_stacks)
    assert all(pile == () for suit in s.discard_stacks for pile in suit)


def test_create_cards_left_matches_total_deck_size(no_variant_state: State) -> None:
    # 5 suits * 10 cards/suit (3+2+2+2+1) = 50
    assert no_variant_state.cards_left == 50
    assert no_variant_state.cards_total == 50


def test_create_playable_set_is_all_rank_1s(no_variant_state: State) -> None:
    s = no_variant_state
    expected = {Identity(suit_index, 1) for suit_index in range(5)}
    assert {i for i in s.playable_set} == expected


def test_create_critical_set_is_all_rank_5s(no_variant_state: State) -> None:
    # In No Variant, only the 5s have a single copy (card_count == 1)
    s = no_variant_state
    expected = {Identity(suit_index, 5) for suit_index in range(5)}
    assert {i for i in s.critical_set} == expected


def test_create_all_ids_is_full_25(no_variant_state: State) -> None:
    assert no_variant_state.all_ids.length == 25


def test_create_card_count(no_variant_state: State) -> None:
    s = no_variant_state
    # 5 suits * (3+2+2+2+1) per suit
    for suit_index in range(5):
        for rank, expected in zip(range(1, 6), (3, 2, 2, 2, 1), strict=True):
            assert s.card_count[Identity(suit_index, rank).to_ord()] == expected


# --- Pure helpers ---


def test_is_basic_trash(no_variant_state: State) -> None:
    s = no_variant_state
    # rank below play stack (none played yet, so nothing trash) and rank above max
    assert not s.is_basic_trash(Identity(0, 1))
    assert not s.is_basic_trash(Identity(0, 5))
    # After we "play" a 1 (manually advance stack), the 1 becomes trash
    s2 = s.with_play(Identity(0, 1))
    assert s2.is_basic_trash(Identity(0, 1))
    assert not s2.is_basic_trash(Identity(0, 2))


def test_is_useful(no_variant_state: State) -> None:
    s = no_variant_state
    assert s.is_useful(Identity(0, 1))
    assert s.is_useful(Identity(0, 5))


def test_is_playable(no_variant_state: State) -> None:
    s = no_variant_state
    assert s.is_playable(Identity(0, 1))
    assert not s.is_playable(Identity(0, 2))


def test_playable_away(no_variant_state: State) -> None:
    s = no_variant_state
    assert s.playable_away(Identity(0, 1)) == 0
    assert s.playable_away(Identity(0, 3)) == 2


def test_score_and_max_score(no_variant_state: State) -> None:
    s = no_variant_state
    assert s.score == 0
    assert s.max_score == 25
    assert s.rem_score == 25


def test_pace(no_variant_state: State) -> None:
    s = no_variant_state
    # pace = score + cards_left + num_players - max_score = 0 + 50 + 3 - 25 = 28
    assert s.pace == 28


def test_next_and_last_player_index(no_variant_state: State) -> None:
    s = no_variant_state
    assert s.next_player_index(0) == 1
    assert s.next_player_index(2) == 0
    assert s.last_player_index(0) == 2
    assert s.last_player_index(2) == 1


def test_can_clue(no_variant_state: State) -> None:
    import dataclasses

    assert no_variant_state.can_clue
    s2 = dataclasses.replace(no_variant_state, clue_tokens=0)
    assert not s2.can_clue


def test_multiplicity(no_variant_state: State) -> None:
    s = no_variant_state
    # All rank-1s: 3 copies * 5 suits = 15
    from hanabi_bot.basics.identity import IdentitySet
    ones = IdentitySet.from_iter(Identity(i, 1) for i in range(5))
    assert s.multiplicity(ones) == 15
    # All rank-5s: 1 copy * 5 suits = 5
    fives = IdentitySet.from_iter(Identity(i, 5) for i in range(5))
    assert s.multiplicity(fives) == 5


# --- with_play ---


def test_with_play_advances_stack(no_variant_state: State) -> None:
    s = no_variant_state.with_play(Identity(0, 1))
    assert s.play_stacks == (1, 0, 0, 0, 0)
    assert s.score == 1
    # Playable set now contains 2 of the played suit (next playable), no longer 1
    assert Identity(0, 2) in s.playable_set
    assert Identity(0, 1) not in s.playable_set
    # 1 is now in trash set
    assert Identity(0, 1) in s.trash_set


def test_with_play_5_regains_clue(no_variant_state: State) -> None:
    s = no_variant_state
    # Walk the stack up to 4
    for r in range(1, 5):
        s = s.with_play(Identity(0, r))
    # Burn some clue tokens manually
    import dataclasses
    s = dataclasses.replace(s, clue_tokens=3)
    s = s.with_play(Identity(0, 5))
    assert s.play_stacks[0] == 5
    assert s.clue_tokens == 4  # gained one back from playing the 5


def test_with_play_5_caps_clue_at_8(no_variant_state: State) -> None:
    s = no_variant_state
    for r in range(1, 5):
        s = s.with_play(Identity(0, r))
    s = s.with_play(Identity(0, 5))
    # clue_tokens was 8, playing a 5 caps it at 8 (not 9)
    assert s.clue_tokens == 8


# --- with_discard ---


def test_with_discard_non_critical(no_variant_state: State) -> None:
    s = no_variant_state.with_discard(Identity(0, 1), order=42)
    # The 1 should be in the discard pile for suit 0
    assert s.discard_stacks[0][0] == (42,)
    # base_count incremented
    assert s.base_count[Identity(0, 1).to_ord()] == 1
    # 3 copies of r1 still possible — none of the sets change
    assert Identity(0, 1) in s.playable_set
    assert Identity(0, 1) not in s.critical_set


def test_with_discard_second_to_last_r1_becomes_critical(no_variant_state: State) -> None:
    s = no_variant_state
    s = s.with_discard(Identity(0, 1), order=42)
    s = s.with_discard(Identity(0, 1), order=43)
    # 2 of 3 r1s now discarded; the last one is critical
    assert Identity(0, 1) in s.critical_set


def test_with_discard_critical_5_lowers_max(no_variant_state: State) -> None:
    s = no_variant_state.with_discard(Identity(0, 5), order=42)
    # All 5s are critical (1 copy each). Discarding it lowers max_rank for suit 0 to 4
    assert s.max_ranks[0] == 4
    assert s.max_score == 4 + 5 * 4  # one suit capped at 4, rest still 5
    assert Identity(0, 5) in s.trash_set
    assert Identity(0, 5) not in s.critical_set
    assert Identity(0, 5) not in s.playable_set


# --- regain_clue ---


def test_regain_clue_caps_at_8(no_variant_state: State) -> None:
    s = no_variant_state.regain_clue()
    assert s.clue_tokens == 8  # already at cap


def test_regain_clue_normal_increment() -> None:
    import dataclasses
    v = get_variant("No Variant")
    opts = TableOptions(num_players=3, variant_name="No Variant")
    s = State.create(names=("a", "b", "c"), our_player_index=0, variant=v, options=opts)
    s = dataclasses.replace(s, clue_tokens=5)
    s = s.regain_clue()
    assert s.clue_tokens == 6


def test_regain_clue_clue_starved_uses_half_tokens() -> None:
    """In a clue-starved variant, playing a 5 only gives back half a token."""
    import dataclasses
    v = get_variant("Clue Starved (5 Suits)")
    opts = TableOptions(num_players=3, variant_name="Clue Starved (5 Suits)")
    s = State.create(names=("a", "b", "c"), our_player_index=0, variant=v, options=opts)
    s = dataclasses.replace(s, clue_tokens=4)
    # First gain: sets half_clue_token, doesn't increment
    s2 = s.regain_clue()
    assert s2.clue_tokens == 4
    assert s2.half_clue_token is True
    # Second gain: consumes half token, increments
    s3 = s2.regain_clue()
    assert s3.clue_tokens == 5
    assert s3.half_clue_token is False


# --- expand_short ---


def test_expand_short(no_variant_state: State) -> None:
    s = no_variant_state
    # No Variant suits: Red, Yellow, Green, Blue, Purple
    # Short forms picked by Variant constructor (lowercased first letters)
    assert s.expand_short("r1") == Identity(0, 1)
    assert s.expand_short("y3") == Identity(1, 3)
    assert s.expand_short("g5") == Identity(2, 5)


def test_expand_short_invalid_length(no_variant_state: State) -> None:
    with pytest.raises(ValueError):
        no_variant_state.expand_short("r")
    with pytest.raises(ValueError):
        no_variant_state.expand_short("r10")


def test_expand_short_unknown_colour(no_variant_state: State) -> None:
    with pytest.raises(ValueError):
        no_variant_state.expand_short("z1")


# --- log_id ---


def test_log_id(no_variant_state: State) -> None:
    s = no_variant_state
    assert s.log_id(Identity(0, 1)) == "r1"
    assert s.log_id(Identity(4, 5)) == "p5"
    assert s.log_id(None) == "xx"


# --- ended ---


def test_ended_three_strikes() -> None:
    import dataclasses
    v = get_variant("No Variant")
    opts = TableOptions(num_players=3, variant_name="No Variant")
    s = State.create(names=("a", "b", "c"), our_player_index=0, variant=v, options=opts)
    assert not s.ended
    s2 = dataclasses.replace(s, strikes=3)
    assert s2.ended


def test_ended_max_score(no_variant_state: State) -> None:
    s = no_variant_state
    # Walk every stack to 5
    for suit_index in range(5):
        for rank in range(1, 6):
            s = s.with_play(Identity(suit_index, rank))
    assert s.score == 25
    assert s.ended
