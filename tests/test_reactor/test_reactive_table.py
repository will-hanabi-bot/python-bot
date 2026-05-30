"""Tests for the reactive-value table used by rainbow-ish variants."""

from __future__ import annotations

import pytest

from hanabi_bot.basics.variant import Suit, SuitType, Variant
from hanabi_bot.conventions.reactor.reactive_table import (
    VANILLA_ORDER,
    reactive_value_table,
)


def _suit(name: str) -> Suit:
    return Suit(name=name, abbreviation=name[0].lower(), suit_type=SuitType.of_name(name))


def _variant(*colourable_names: str, name: str = "test") -> Variant:
    """Build a minimal Variant whose colourable_suits has the given names in order.

    suits == colourable_suits for these tests (none of the names match NO_COLOUR).
    """
    suits = tuple(_suit(n) for n in colourable_names)
    return Variant(
        id=999,
        name=name,
        suits=suits,
        short_forms=tuple(s.abbreviation or "" for s in suits),
        colourable_suits=suits,
        critical_rank=None,
        clue_starved=False,
        special_rank=None,
        rainbow_s=False,
        white_s=False,
        pink_s=False,
        brown_s=False,
        deceptive_s=False,
        scarce_ones=False,
    )


def test_three_color_variant_red_blue_pink() -> None:
    v = _variant("Red", "Blue", "Pink", name="t1")
    assert reactive_value_table(v, hand_size=5) == (1, 4, 5)


def test_five_with_wrap_red_green_blue_pink_brown() -> None:
    v = _variant("Red", "Green", "Blue", "Pink", "Brown", name="t2")
    assert reactive_value_table(v, hand_size=5) == (1, 3, 4, 5, 2)


def test_six_all_used_defaults_to_one() -> None:
    v = _variant("Red", "Yellow", "Green", "Blue", "Pink", "Brown", name="t3")
    assert reactive_value_table(v, hand_size=5) == (1, 2, 3, 4, 5, 1)


def test_red_plus_specials_walks_from_red() -> None:
    v = _variant("Red", "Pink", "Brown", name="t4")
    assert reactive_value_table(v, hand_size=5) == (1, 2, 3)


def test_four_player_hand_size_mod() -> None:
    """In 4-5p (hand_size=4), vanilla values wrap mod 4."""
    v = _variant("Red", "Yellow", "Green", "Blue", "Purple", "Teal", name="t5")
    assert reactive_value_table(v, hand_size=4) == (1, 2, 3, 4, 1, 2)


def test_four_player_specials_wrap_to_one() -> None:
    """With hand_size=4 and 4 vanilla colors filling 1..4, a special defaults to 1."""
    v = _variant("Red", "Yellow", "Green", "Blue", "Pink", name="t6")
    assert reactive_value_table(v, hand_size=4) == (1, 2, 3, 4, 1)


def test_missing_red_asserts() -> None:
    v = _variant("Yellow", "Blue", "Pink", name="t7")
    with pytest.raises(AssertionError, match="Red"):
        reactive_value_table(v, hand_size=5)


def test_vanilla_order_is_six_canonical_names() -> None:
    assert VANILLA_ORDER == ("Red", "Yellow", "Green", "Blue", "Purple", "Teal")


def test_cache_returns_same_tuple() -> None:
    v = _variant("Red", "Blue", "Pink", name="t_cache")
    a = reactive_value_table(v, hand_size=5)
    b = reactive_value_table(v, hand_size=5)
    assert a is b
