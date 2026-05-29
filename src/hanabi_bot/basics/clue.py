"""Clue types: ClueKind, BaseClue, CardClue, Clue.

Port of scala-bot/src/scala_bot/basics/clue.scala.

ClueLike (the Scala trait) is not modelled as a Protocol here — duck typing
suffices since all three concrete types expose the same `kind` and `value`
fields. The `fmt(state, target)` helper from the Scala trait is deferred to
Stage 2 (it requires State, which lands then).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class ClueKind(enum.Enum):
    COLOUR = 0
    RANK = 1


@dataclass(frozen=True, slots=True)
class BaseClue:
    """A clue kind + value pair (no giver, target, or turn info).

    :param kind: ClueKind.COLOUR or ClueKind.RANK
    :param value: For colour, an index into Variant.colourable_suits.
                  For rank, the rank value (1..5).
    """

    kind: ClueKind
    value: int

    @property
    def hash_int(self) -> int:
        """Stable small-int identifier. Port of BaseClue.hash."""
        return (0 if self.kind == ClueKind.COLOUR else 10) + self.value

    def to_clue(self, target: int) -> Clue:
        return Clue(self.kind, self.value, target)


@dataclass(frozen=True, slots=True)
class CardClue:
    """A clue that has touched a specific card, recorded on the card.

    :param giver: Index of the player who gave the clue.
    :param turn: Turn on which the clue was given.
    """

    kind: ClueKind
    value: int
    giver: int
    turn: int

    @property
    def base(self) -> BaseClue:
        return BaseClue(self.kind, self.value)


@dataclass(frozen=True, slots=True)
class Clue:
    """A clue ready to be given.

    :param target: Index of the player to receive the clue.
    """

    kind: ClueKind
    value: int
    target: int

    @property
    def base(self) -> BaseClue:
        return BaseClue(self.kind, self.value)
