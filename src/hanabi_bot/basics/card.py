"""Card, Thought, ConvData, CardStatus.

Port of scala-bot/src/scala_bot/basics/Card.scala (lines 65-230).

The Scala Identifiable trait is not modelled as a Protocol here — each of Card
and Thought defines its own `id()` method directly (duck typing suffices).

IdentitySetOpt in Scala (a -1L sentinel Long) is modelled as `IdentitySet | None`
in Python — simpler than the sentinel approach.
"""

from __future__ import annotations

import dataclasses
import enum
from dataclasses import dataclass

from .clue import CardClue
from .identity import Identity, IdentitySet


class CardStatus(enum.Enum):
    """The conventional status of a card (e.g. chop-moved, called to discard)."""

    NONE = "none"
    CHOP_MOVED = "chop moved"
    CALLED_TO_PLAY = "called to play"
    CALLED_TO_DISCARD = "called to discard"
    PERMISSION_TO_DISCARD = "permission to discard"
    FINESSED = "finessed"
    SARCASTIC = "sarcastic"
    GENTLEMANS_DISCARD = "gentleman's discard"
    # Finessed but definitely blind-playing (possibly bluffed).
    F_MAYBE_BLUFFED = "finessed, maybe bluffed"
    MAYBE_BLUFFED = "maybe bluffed"
    BLUFFED = "bluffed"


@dataclass(frozen=True, slots=True)
class Card:
    """A physical card in the game.

    Suit_index and rank are -1 if unknown to the observer.
    """

    suit_index: int
    rank: int
    order: int          # position in the deck (0 = topmost)
    turn_drawn: int
    clued: bool = False
    clues: tuple[CardClue, ...] = ()

    def id(self) -> Identity | None:
        """Returns the contained identity, or None if unknown."""
        if self.suit_index == -1 or self.rank == -1:
            return None
        return Identity(self.suit_index, self.rank)

    def matches(
        self,
        other: Card | Identity | Thought,
        *,
        infer: bool = False,
        symmetric: bool = False,
        assume: bool = False,
    ) -> bool:
        """True iff this card has the same identity as `other`.

        If this card's identity is unknown, returns `assume`.
        If `other`'s identity is unknown, returns False.

        `infer` and `symmetric` are accepted for API compatibility with Thought.matches
        but have no effect on Card (Card.id is unconditional).
        """
        a = self.id()
        if a is None:
            return assume
        if isinstance(other, Identity):
            return a == other
        b = other.id(infer=infer, symmetric=symmetric) if isinstance(other, Thought) else other.id()
        return b is not None and a == b


@dataclass(frozen=True, slots=True)
class Thought:
    """What a player thinks about a card.

    The four "possible identity" notions:
    - possible: ids consistent with clues only (empathy).
    - inferred: ids consistent with clues PLUS conventional info. Subset of possible.
    - old_inferred: previous inferences (kept around in case a finesse is disproved).
    - old_possible: previous possibilities before the card was fully revealed via play/discard.
    - info_lock: PROMISED ids from sarcastic discard / fix clues. Cannot widen without rewind.
    """

    suit_index: int
    rank: int
    order: int
    possible: IdentitySet
    inferred: IdentitySet
    old_inferred: IdentitySet | None = None
    old_possible: IdentitySet | None = None
    info_lock: IdentitySet | None = None
    rewinded: bool = False
    reset: bool = False

    @classmethod
    def initial(cls, suit_index: int, rank: int, order: int, possible: IdentitySet) -> Thought:
        """Build a fresh Thought where inferred == possible. Port of Scala's Thought.apply 3-arg."""
        return cls(suit_index=suit_index, rank=rank, order=order, possible=possible, inferred=possible)

    def id(self, infer: bool = False, symmetric: bool = False, partial: bool = False) -> Identity | None:
        """Returns the identity, or None if unknown.

        :param infer: If True and there's exactly 1 inference, return it.
        :param symmetric: If True, ignore info from "looking" at the card.
        :param partial: If True, return partial info (-1 for unknown half).
        """
        if self.possible.length == 1:
            return self.possible.head
        if not symmetric and self.suit_index != -1:
            return Identity(self.suit_index, self.rank)
        if infer and self.inferred.length == 1:
            return self.inferred.head
        if partial:
            head = self.possible.head
            if self.possible.forall(lambda i: i.suit_index == head.suit_index):
                return Identity(head.suit_index, -1)
            if self.possible.forall(lambda i: i.rank == head.rank):
                return Identity(-1, head.rank)
            return None
        return None

    @property
    def possibilities(self) -> IdentitySet:
        """Inferences if any, otherwise possible ids."""
        return self.possible if self.inferred.is_empty else self.inferred

    def matches(
        self,
        other: Card | Identity | Thought,
        *,
        infer: bool = False,
        symmetric: bool = False,
        assume: bool = False,
    ) -> bool:
        """True iff this thought has the same identity as `other` (under the given infer/symmetric flags).

        Port of Identifiable.matches (Card.scala lines 27-30).
        """
        a = self.id(infer=infer, symmetric=symmetric)
        if a is None:
            return assume
        if isinstance(other, Identity):
            return a == other
        b = other.id(infer=infer, symmetric=symmetric) if isinstance(other, Thought) else other.id()
        return b is not None and a == b

    def reset_inferences(self) -> Thought:
        """Reset inferences based on possible ids and info lock.

        Idempotent: returns self if already reset.
        """
        if self.reset:
            return self

        if self.info_lock is None:
            new_lock: IdentitySet | None = None
        else:
            ids = self.info_lock.intersect(self.possible)
            new_lock = None if ids.is_empty else ids

        new_inferred = self.possible.intersect(new_lock) if new_lock is not None else self.possible

        return dataclasses.replace(self, reset=True, inferred=new_inferred, info_lock=new_lock)


@dataclass(frozen=True, slots=True)
class ConvData:
    """The conventional information on a card, shared across all observers.

    Distinct from Thought (per-perspective belief about identity) — ConvData
    represents what conventions have *said* about the card, agreed by all.
    """

    order: int
    focused: bool = False
    urgent: bool = False
    trash: bool = False  # promised trash, even if non-trash possibilities exist
    status: CardStatus = CardStatus.NONE
    hidden: bool = False  # unknown to the holder (e.g. in a hidden finesse)
    # List of turns where new info was discovered about this card. Stored newest-first.
    reasoning: tuple[int, ...] = ()
    signal_turn: int | None = None
    by: int | None = None

    @property
    def cm(self) -> bool:
        """True iff this card has been chop-moved."""
        return self.status == CardStatus.CHOP_MOVED

    @property
    def bluffed(self) -> bool:
        return self.status in (CardStatus.BLUFFED, CardStatus.F_MAYBE_BLUFFED, CardStatus.MAYBE_BLUFFED)

    def cleared(self) -> ConvData:
        """Return a copy with transient flags cleared. Preserves chop-move status."""
        return dataclasses.replace(
            self,
            focused=False,
            urgent=False,
            trash=False,
            status=self.status if self.status == CardStatus.CHOP_MOVED else CardStatus.NONE,
            signal_turn=None,
            by=None,
        )

    def reason(self, turn_count: int) -> ConvData:
        """Add an entry to the reasoning list, deduplicating against the tail entry.

        Port of ConvData.reason; uses `reasoning.last != turnCount` semantics for the dedupe.
        """
        if self.reasoning and self.reasoning[-1] == turn_count:
            return self
        return dataclasses.replace(self, reasoning=(turn_count, *self.reasoning))

    def signal(self, turn_count: int) -> ConvData:
        """Record the turn when this card was signalled. No-op if already set."""
        if self.signal_turn is not None:
            return self
        return dataclasses.replace(self, signal_turn=turn_count)
