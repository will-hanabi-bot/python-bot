"""Clue types: ClueKind, BaseClue, CardClue, Clue."""

from __future__ import annotations

from hanabi_bot.basics.clue import BaseClue, CardClue, Clue, ClueKind


def test_clue_kind_values() -> None:
    assert ClueKind.COLOUR.value == 0
    assert ClueKind.RANK.value == 1


def test_base_clue_hash_int_distinct_per_kind_and_value() -> None:
    # Hash space: 0..9 = colour 0..9, 10..14 = rank 0..4 (rank values 1..5)
    assert BaseClue(ClueKind.COLOUR, 0).hash_int == 0
    assert BaseClue(ClueKind.COLOUR, 4).hash_int == 4
    assert BaseClue(ClueKind.RANK, 0).hash_int == 10
    assert BaseClue(ClueKind.RANK, 5).hash_int == 15
    # Different (kind, value) -> different hash
    hashes = {
        BaseClue(ClueKind.COLOUR, v).hash_int for v in range(6)
    } | {BaseClue(ClueKind.RANK, v).hash_int for v in range(1, 6)}
    assert len(hashes) == 11


def test_base_clue_to_clue() -> None:
    bc = BaseClue(ClueKind.COLOUR, 2)
    c = bc.to_clue(target=3)
    assert c == Clue(ClueKind.COLOUR, 2, 3)


def test_card_clue_base() -> None:
    cc = CardClue(ClueKind.RANK, 3, giver=1, turn=5)
    assert cc.base == BaseClue(ClueKind.RANK, 3)


def test_clue_base() -> None:
    c = Clue(ClueKind.COLOUR, 1, target=0)
    assert c.base == BaseClue(ClueKind.COLOUR, 1)


def test_frozen_dataclasses() -> None:
    import pytest

    bc = BaseClue(ClueKind.COLOUR, 0)
    with pytest.raises((AttributeError, TypeError)):
        bc.value = 1  # type: ignore[misc]
