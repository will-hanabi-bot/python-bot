"""Coverage of Variant loader and per-predicate id_touched behavior.

Each test pins one variant's behavior under both colour and rank clues to
confirm the regex predicates (WHITISH/RAINBOWISH/PINKISH/BROWNISH/DARK/PRISM/
MUDDY) match the Scala port faithfully.
"""

from __future__ import annotations

import pytest

from hanabi_bot.basics.identity import Identity
from hanabi_bot.basics.variant import (
    BROWNISH,
    DARK,
    MUDDY,
    NO_COLOUR,
    PINKISH,
    PRISM,
    RAINBOWISH,
    WHITISH,
    SuitType,
    get_variant,
    load_variants,
)

CLUE_COLOUR = 0
CLUE_RANK = 1


# --- Regex predicates ---


@pytest.mark.parametrize(
    "name,whitish,rainbowish,pinkish,brownish,dark,prism,muddy",
    [
        ("Red", False, False, False, False, False, False, False),
        ("White", True, False, False, False, False, False, False),
        ("Rainbow", False, True, False, False, False, False, False),
        ("Pink", False, False, True, False, False, False, False),
        ("Brown", False, False, False, True, False, False, False),
        ("Black", False, False, False, False, True, False, False),
        ("Prism", False, False, False, False, False, True, False),
        ("Muddy Rainbow", False, True, False, True, False, False, True),
        ("Dark Rainbow", False, True, False, False, True, False, False),
        ("Cocoa Rainbow", False, True, False, True, True, False, True),
        ("Gray", True, False, False, False, True, False, False),
        ("Omni", False, True, True, False, False, False, False),
        ("Null", True, False, False, True, False, False, False),
        ("Light Pink", True, False, True, False, False, False, False),
    ],
)
def test_suit_type_of_name(
    name: str,
    whitish: bool,
    rainbowish: bool,
    pinkish: bool,
    brownish: bool,
    dark: bool,
    prism: bool,
    muddy: bool,
) -> None:
    st = SuitType.of_name(name)
    assert st.whitish is whitish
    assert st.rainbowish is rainbowish
    assert st.pinkish is pinkish
    assert st.brownish is brownish
    assert st.dark is dark
    assert st.prism is prism
    assert st.muddy is muddy


def test_no_colour_predicate() -> None:
    # No-colour suits don't get a colour-clue index
    assert NO_COLOUR.search("White")
    assert NO_COLOUR.search("Rainbow")
    assert NO_COLOUR.search("Prism")
    assert NO_COLOUR.search("Omni")
    assert not NO_COLOUR.search("Red")
    assert not NO_COLOUR.search("Pink")
    assert not NO_COLOUR.search("Black")


def test_regex_module_constants_compile() -> None:
    # Sanity that the regex objects are usable
    for r in (WHITISH, RAINBOWISH, PINKISH, BROWNISH, DARK, PRISM, MUDDY, NO_COLOUR):
        assert r.pattern  # truthy


# --- Variant loading ---


def test_load_variants_returns_full_catalog() -> None:
    variants = load_variants()
    # The hanabi-live catalog has 2000+ variants
    assert len(variants) > 2000
    assert "No Variant" in variants


def test_get_variant_unknown_raises() -> None:
    with pytest.raises(ValueError):
        get_variant("This Variant Does Not Exist")


# --- No Variant: baseline behavior ---


def test_no_variant_structure() -> None:
    v = get_variant("No Variant")
    assert len(v.suits) == 5
    assert [s.name for s in v.suits] == ["Red", "Yellow", "Green", "Blue", "Purple"]
    assert len(v.colourable_suits) == 5  # all five are colourable
    assert v.total_cards == 5 * 10  # 10 cards per suit (3+2+2+2+1)


def test_no_variant_card_count() -> None:
    v = get_variant("No Variant")
    assert v.card_count(Identity(0, 1)) == 3
    assert v.card_count(Identity(0, 2)) == 2
    assert v.card_count(Identity(0, 3)) == 2
    assert v.card_count(Identity(0, 4)) == 2
    assert v.card_count(Identity(0, 5)) == 1


def test_no_variant_colour_clue_touches_one_suit() -> None:
    v = get_variant("No Variant")
    # Red colour clue (value 0) touches red identities only
    for rank in range(1, 6):
        assert v.id_touched(Identity(0, rank), CLUE_COLOUR, 0)  # Red
        assert not v.id_touched(Identity(1, rank), CLUE_COLOUR, 0)  # Yellow not touched


def test_no_variant_rank_clue_touches_one_rank() -> None:
    v = get_variant("No Variant")
    for suit in range(5):
        assert v.id_touched(Identity(suit, 3), CLUE_RANK, 3)
        assert not v.id_touched(Identity(suit, 4), CLUE_RANK, 3)


# --- Pink: rank clues touch the pink suit ---


def test_pink_variant_pinkish_touched_by_every_rank_clue() -> None:
    v = get_variant("Pink (5 Suits)")
    pink_index = 4  # Red, Yellow, Green, Blue, Pink
    for clue_rank in range(1, 6):
        for actual_rank in range(1, 6):
            assert v.id_touched(Identity(pink_index, actual_rank), CLUE_RANK, clue_rank)


def test_pink_variant_normal_suits_unaffected_by_rank_clue() -> None:
    v = get_variant("Pink (5 Suits)")
    assert v.id_touched(Identity(0, 3), CLUE_RANK, 3)
    assert not v.id_touched(Identity(0, 4), CLUE_RANK, 3)


# --- Rainbow: colour clues touch the rainbow suit ---


def test_rainbow_variant_rainbowish_touched_by_every_colour_clue() -> None:
    v = get_variant("Rainbow (5 Suits)")
    rainbow_index = 4
    # Rainbow is not in colourable_suits — only 4 colourable suits
    assert len(v.colourable_suits) == 4
    for clue_color in range(len(v.colourable_suits)):
        for rank in range(1, 6):
            assert v.id_touched(Identity(rainbow_index, rank), CLUE_COLOUR, clue_color)


def test_rainbow_variant_rainbow_rank_clue_works_normally() -> None:
    v = get_variant("Rainbow (5 Suits)")
    rainbow_index = 4
    # Rainbow is NOT pinkish — rank clue only touches matching rank
    assert v.id_touched(Identity(rainbow_index, 3), CLUE_RANK, 3)
    assert not v.id_touched(Identity(rainbow_index, 4), CLUE_RANK, 3)


# --- White: colour clues never touch the white suit ---


def test_white_variant_whitish_untouched_by_any_colour_clue() -> None:
    v = get_variant("White (5 Suits)")
    white_index = 4
    for clue_color in range(len(v.colourable_suits)):
        for rank in range(1, 6):
            assert not v.id_touched(Identity(white_index, rank), CLUE_COLOUR, clue_color)


# --- Brown: rank clues never touch the brown suit ---


def test_brown_variant_brownish_untouched_by_any_rank_clue() -> None:
    v = get_variant("Brown (5 Suits)")
    brown_index = 4
    for clue_rank in range(1, 6):
        for actual_rank in range(1, 6):
            assert not v.id_touched(Identity(brown_index, actual_rank), CLUE_RANK, clue_rank)


# --- Black: dark suit only has 1 of every rank ---


def test_black_variant_dark_card_counts() -> None:
    v = get_variant("Black (5 Suits)")
    black_index = 4
    for rank in range(1, 6):
        assert v.card_count(Identity(black_index, rank)) == 1
    # Non-dark suits unaffected
    assert v.card_count(Identity(0, 1)) == 3
    assert v.card_count(Identity(0, 5)) == 1


# --- Prism: rank determines colour ---


def test_prism_variant_rank_determines_colour() -> None:
    v = get_variant("Prism (5 Suits)")
    prism_index = 4
    # Prism is not colourable (excluded by NO_COLOUR)
    assert len(v.colourable_suits) == 4
    # Per Variant.idTouched line 73-74:
    #   prism touched by colour clue iff ((rank - 1) % #colourable_suits) == clue_value
    for rank in range(1, 6):
        expected_colour = (rank - 1) % 4
        for clue_colour in range(4):
            assert v.id_touched(Identity(prism_index, rank), CLUE_COLOUR, clue_colour) == (
                clue_colour == expected_colour
            )


# --- Muddy Rainbow: rainbowish + brownish ---


def test_muddy_rainbow_brownish_and_rainbowish() -> None:
    v = get_variant("Muddy Rainbow (5 Suits)")
    muddy_index = 4
    # All colour clues touch (rainbowish)
    for clue_color in range(len(v.colourable_suits)):
        assert v.id_touched(Identity(muddy_index, 1), CLUE_COLOUR, clue_color)
    # No rank clue touches (brownish)
    for clue_rank in range(1, 6):
        assert not v.id_touched(Identity(muddy_index, 1), CLUE_RANK, clue_rank)


# --- Dark Rainbow: dark + rainbowish ---


def test_dark_rainbow_dark_and_rainbowish() -> None:
    v = get_variant("Dark Rainbow (5 Suits)")
    dr_index = 4
    # All colour clues touch
    for clue_color in range(len(v.colourable_suits)):
        assert v.id_touched(Identity(dr_index, 1), CLUE_COLOUR, clue_color)
    # Dark: 1 copy of every rank
    for rank in range(1, 6):
        assert v.card_count(Identity(dr_index, rank)) == 1
