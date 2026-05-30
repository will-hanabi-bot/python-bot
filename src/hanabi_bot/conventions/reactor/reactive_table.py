"""Per-variant reactive-value table for color clues in rainbow-ish variants.

Maps each colourable suit (in stack order) to a reactive value in 1..hand_size.

Vanilla colors (Red, Yellow, Green, Blue, Purple, Teal) get fixed values based
on their position in VANILLA_ORDER, wrapped mod hand_size. Every other suit
("special": Pink, Brown, Black, Orange, etc.) takes the first reactive slot not
yet claimed, scanning forward from the previous suit's value (mod hand_size).
If all hand_size slots are claimed, the special suit defaults to 1.

Examples (hand_size=5):
- [Red, Blue, Pink]                  -> (1, 4, 5)
- [Red, Green, Blue, Pink, Brown]    -> (1, 3, 4, 5, 2)
- [Red, Y, G, B, Pink, Brown]        -> (1, 2, 3, 4, 5, 1)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hanabi_bot.basics.variant import PINKISH, RAINBOWISH

if TYPE_CHECKING:
    from hanabi_bot.basics.variant import Variant


VANILLA_ORDER: tuple[str, ...] = ("Red", "Yellow", "Green", "Blue", "Purple", "Teal")


_cache: dict[tuple[Variant, int], tuple[int, ...]] = {}


def reactive_value_table(variant: Variant, hand_size: int) -> tuple[int, ...]:
    """For each colourable suit in stack order, the reactive value (1..hand_size).

    Requires `Red` to appear in variant.colourable_suits — asserts otherwise.
    Result is cached per (variant, hand_size). Variants are frozen dataclasses,
    so equal variants share cache entries.
    """
    key = (variant, hand_size)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    assert any(s.name == "Red" for s in variant.colourable_suits), (
        f"Variant {variant.name!r} has no Red in colourable_suits; "
        f"the rainbow-variant reactive table requires Red"
    )

    vanilla_value = {
        name: ((i) % hand_size) + 1 for i, name in enumerate(VANILLA_ORDER)
    }

    taken: set[int] = set()
    prev_value = 0
    result: list[int] = []
    for suit in variant.colourable_suits:
        if suit.name in vanilla_value:
            value = vanilla_value[suit.name]
        else:
            value = 1
            for offset in range(1, hand_size + 1):
                candidate = ((prev_value - 1 + offset) % hand_size) + 1
                if candidate not in taken:
                    value = candidate
                    break
        taken.add(value)
        prev_value = value
        result.append(value)

    out = tuple(result)
    _cache[key] = out
    return out


def _is_rainbowy(variant: Variant) -> bool:
    return variant.rainbow_s or any(RAINBOWISH.search(s.name) for s in variant.suits)


def _is_pinkish(variant: Variant) -> bool:
    return variant.pink_s or any(PINKISH.search(s.name) for s in variant.suits)


def _rank_blocked(variant: Variant, rank: int) -> bool:
    """Mirror of `State.all_valid_clues`'s rank-blocked rule.

    A rank can't be used as a clue value when it's the variant's special_rank AND the
    variant has pink_s / brown_s / deceptive_s set (which would make the clue
    indistinguishable from a normal rank-N clue for non-special cards).
    """
    return (
        variant.special_rank is not None
        and variant.special_rank == rank
        and (variant.pink_s or variant.brown_s or variant.deceptive_s)
    )


def format_reactive_settings(variant: Variant, hand_size: int) -> str:
    """Format the variant's reactive-clue table for the `/settings` chat command.

    Output shape: `"odd plays: {<colors>}, even plays: {<ranks>}"`, where each side
    is either the literal `"slot focus"` (when the clue type doesn't use the value-keyed
    reactive table) or a comma-separated list of length `hand_size` whose entry at
    slot N is the single-char abbreviation of the suit/rank mapped there, or `-`.
    """
    # Odd plays: color clues.
    if _is_rainbowy(variant):
        table = reactive_value_table(variant, hand_size)
        slot_to_suit: dict[int, str] = {}
        for i, suit in enumerate(variant.colourable_suits):
            slot_to_suit[table[i]] = suit.abbreviation or suit.name[0].lower()
        odd_items = [slot_to_suit.get(slot, "-") for slot in range(1, hand_size + 1)]
        odd = "{" + ", ".join(odd_items) + "}"
    else:
        odd = "{slot focus}"

    # Even plays: rank clues.
    if _is_pinkish(variant):
        rank_items = [
            "-" if _rank_blocked(variant, rank) else str(rank)
            for rank in range(1, hand_size + 1)
        ]
        even = "{" + ", ".join(rank_items) + "}"
    else:
        even = "{slot focus}"

    return f"odd plays: {odd}, even plays: {even}"
