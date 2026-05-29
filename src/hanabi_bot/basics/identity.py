"""Identity (suit_index, rank) pair and IdentitySet (bit-packed set of identities).

Port of scala-bot/src/scala_bot/basics/Card.scala (Identity portion, lines 33-63)
and scala-bot/src/scala_bot/basics/IdentitySet.scala.

The Scala version uses a 64-bit Long as an opaque type wrapping a bitset.
We subclass Python's arbitrary-precision int, which supports the same bitwise
operators (& | ^ ~), plus int.bit_count() for popcount.

IdentitySetOpt in Scala (-1L sentinel) is modelled here as IdentitySet | None.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

# Max ordinal: 6 suits x 5 ranks = 30 identities. Matches Scala's bound.
_MAX_ORD = 30


@dataclass(frozen=True, slots=True)
class Identity:
    """The pairing of a suit_index and rank (1..5)."""

    suit_index: int
    rank: int

    def to_ord(self) -> int:
        """Encode as a single ordinal int. ord = suit_index * 5 + (rank - 1)."""
        return self.suit_index * 5 + (self.rank - 1)

    @classmethod
    def from_ord(cls, ord_: int) -> Identity:
        """Decode an ordinal back to an Identity. Raises ValueError if out of range."""
        if ord_ < 0 or ord_ >= _MAX_ORD:
            raise ValueError(f"Couldn't convert ordinal {ord_} to identity")
        return cls(ord_ // 5, (ord_ % 5) + 1)

    @property
    def prev(self) -> Identity | None:
        """The previous identity in the same suit, if any."""
        if self.rank > 1:
            return Identity(self.suit_index, self.rank - 1)
        return None

    @property
    def next(self) -> Identity | None:
        """The next identity in the same suit, if any."""
        if self.rank < 5:
            return Identity(self.suit_index, self.rank + 1)
        return None

    def played_before(self, other: Identity) -> bool:
        """True iff this card is in the same suit as `other` and lower-ranked."""
        return self.suit_index == other.suit_index and self.rank < other.rank


class IdentitySet(int):
    """A bit-packed set of Identity values.

    Each set bit corresponds to an Identity ordinal (see Identity.to_ord).
    Subclasses int so that bitwise ops work natively; binary ops are overridden
    to return IdentitySet rather than plain int, preserving the subclass.

    Use the classmethod constructors (empty, single, from_iter, create) rather
    than the int(...) constructor unless you have a raw bitmask in hand.
    """

    __slots__ = ()

    @classmethod
    def empty(cls) -> IdentitySet:
        return cls(0)

    @classmethod
    def single(cls, id_: Identity) -> IdentitySet:
        return cls(1 << id_.to_ord())

    @classmethod
    def from_iter(cls, ids: Iterable[Identity]) -> IdentitySet:
        bits = 0
        for id_ in ids:
            bits |= 1 << id_.to_ord()
        return cls(bits)

    @classmethod
    def create(cls, cond: Callable[[Identity], bool], max_ids: int = _MAX_ORD) -> IdentitySet:
        """Build a set containing every Identity (up to max_ids ordinals) for which cond is True."""
        bits = 0
        for i in range(max_ids):
            if cond(Identity.from_ord(i)):
                bits |= 1 << i
        return cls(bits)

    # --- Cardinality / emptiness ---

    @property
    def length(self) -> int:
        """Number of identities contained (popcount)."""
        return int.bit_count(self)

    @property
    def is_empty(self) -> bool:
        return int(self) == 0

    @property
    def non_empty(self) -> bool:
        return int(self) != 0

    # --- Element access ---

    @property
    def head(self) -> Identity:
        """The lowest-ordinal identity in the set. Raises IndexError if empty."""
        n = int(self)
        if n == 0:
            raise IndexError("head of empty IdentitySet")
        # lowest set bit: (n & -n).bit_length() - 1
        return Identity.from_ord((n & -n).bit_length() - 1)

    def is_exactly(self, id_: Identity) -> bool:
        """True iff the set is of size 1, containing exactly this identity."""
        return int(self) == (1 << id_.to_ord())

    def __contains__(self, id_: object) -> bool:
        if not isinstance(id_, Identity):
            return False
        return (int(self) & (1 << id_.to_ord())) != 0

    def __iter__(self) -> Iterator[Identity]:
        remaining = int(self)
        while remaining != 0:
            lsb = (remaining & -remaining).bit_length() - 1
            yield Identity.from_ord(lsb)
            remaining &= remaining - 1

    def to_list(self) -> list[Identity]:
        return list(self)

    # --- Bitwise operators (overridden to preserve subclass) ---

    def __and__(self, other: int) -> IdentitySet:  # type: ignore[override]
        return IdentitySet(int(self) & int(other))

    def __rand__(self, other: int) -> IdentitySet:  # type: ignore[override]
        return IdentitySet(int(other) & int(self))

    def __or__(self, other: int) -> IdentitySet:  # type: ignore[override]
        return IdentitySet(int(self) | int(other))

    def __ror__(self, other: int) -> IdentitySet:  # type: ignore[override]
        return IdentitySet(int(other) | int(self))

    def __xor__(self, other: int) -> IdentitySet:  # type: ignore[override]
        return IdentitySet(int(self) ^ int(other))

    def __rxor__(self, other: int) -> IdentitySet:  # type: ignore[override]
        return IdentitySet(int(other) ^ int(self))

    def __invert__(self) -> int:  # type: ignore[override]
        """Returns a plain (negative) int. Only meaningful when AND'd with another IdentitySet."""
        return ~int(self)

    def __sub__(self, other: int) -> IdentitySet:  # type: ignore[override]
        """Set difference: identities in self but not in other."""
        return IdentitySet(int(self) & ~int(other))

    # --- High-level set operations (Scala-style names) ---

    def union(self, other: IdentitySet | Identity | Iterable[Identity]) -> IdentitySet:
        if isinstance(other, IdentitySet):
            return self | other
        if isinstance(other, Identity):
            return self | IdentitySet.single(other)
        return self | IdentitySet.from_iter(other)

    def intersect(self, other: IdentitySet | Iterable[Identity]) -> IdentitySet:
        if isinstance(other, IdentitySet):
            return self & other
        return self & IdentitySet.from_iter(other)

    def difference(self, other: IdentitySet | Identity | Iterable[Identity]) -> IdentitySet:
        if isinstance(other, IdentitySet):
            return self - other
        if isinstance(other, Identity):
            return self - IdentitySet.single(other)
        return self - IdentitySet.from_iter(other)

    def add(self, id_: Identity) -> IdentitySet:
        return IdentitySet(int(self) | (1 << id_.to_ord()))

    def remove(self, id_: Identity) -> IdentitySet:
        return IdentitySet(int(self) & ~(1 << id_.to_ord()))

    # --- Functional predicates / transforms ---

    def filter(self, cond: Callable[[Identity], bool]) -> IdentitySet:
        bits = int(self)
        scan = int(self)
        while scan != 0:
            lsb = (scan & -scan).bit_length() - 1
            scan &= scan - 1
            if not cond(Identity.from_ord(lsb)):
                bits &= ~(1 << lsb)
        return IdentitySet(bits)

    def forall(self, cond: Callable[[Identity], bool]) -> bool:
        return all(cond(id_) for id_ in self)

    def exists(self, cond: Callable[[Identity], bool]) -> bool:
        return any(cond(id_) for id_ in self)

    def find(self, cond: Callable[[Identity], bool]) -> Identity | None:
        for id_ in self:
            if cond(id_):
                return id_
        return None

    def count(self, cond: Callable[[Identity], bool]) -> int:
        return sum(1 for id_ in self if cond(id_))

    def when_empty(self, alternative: IdentitySet) -> IdentitySet:
        """Returns alternative if this set is empty, otherwise returns self."""
        return alternative if self.is_empty else self

    def __repr__(self) -> str:
        return f"IdentitySet({{{', '.join(repr(id_) for id_ in self)}}})"
