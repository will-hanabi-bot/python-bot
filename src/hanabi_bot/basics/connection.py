"""Connection ADT: how a card connects into a play stack during a finesse/prompt analysis.

Port of scala-bot/src/scala_bot/basics/Connection.scala.

The Scala `Connection` sealed trait is modelled here as a `Union` of frozen
dataclasses. `is_bluff` / `is_possibly_bluff` properties are defined on each
class directly (only FinesseConn can ever be bluffed; the others return False).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .identity import Identity
from .interp import Interp

if TYPE_CHECKING:
    from .state import State


class FinesseKind(enum.Enum):
    TRUE = "True"
    HIDDEN = "Hidden"
    CERTAIN = "Certain"
    POSSIBLY_BLUFF = "PossiblyBluff"
    BLUFF = "Bluff"


@dataclass(frozen=True, slots=True)
class KnownConn:
    """Reacting player already knew this card's identity before the clue."""

    reacting: int
    order: int
    id: Identity

    @property
    def ids(self) -> list[Identity]:
        return [self.id]

    @property
    def kind(self) -> str:
        return "known"

    @property
    def hidden(self) -> bool:
        return False

    @property
    def is_bluff(self) -> bool:
        return False

    @property
    def is_possibly_bluff(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class PlayableConn:
    """Reacting player knew this card was playable but not its identity."""

    reacting: int
    order: int
    id: Identity
    linked: tuple[int, ...] = ()
    hidden: bool = False
    # Orders of connecting cards this card is being layered in front of (Layered Finesse).
    inserting_into: tuple[int, ...] | None = None

    @property
    def ids(self) -> list[Identity]:
        return [self.id]

    @property
    def kind(self) -> str:
        return "playable"

    @property
    def is_bluff(self) -> bool:
        return False

    @property
    def is_possibly_bluff(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class PromptConn:
    """Reacting player is prompted (from a prior clue) for this identity."""

    reacting: int
    order: int
    id: Identity
    hidden: bool = False

    @property
    def ids(self) -> list[Identity]:
        return [self.id]

    @property
    def kind(self) -> str:
        return "prompt"

    @property
    def is_bluff(self) -> bool:
        return False

    @property
    def is_possibly_bluff(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class FinesseConn:
    """Reacting player is finessed (blind-plays) for this identity."""

    reacting: int
    order: int
    ids: tuple[Identity, ...]
    f_kind: FinesseKind
    ambiguous_passback: bool = False

    @property
    def kind(self) -> str:
        if self.f_kind == FinesseKind.POSSIBLY_BLUFF:
            return "bluff?"
        if self.f_kind == FinesseKind.BLUFF:
            return "bluff"
        return "finesse"

    @property
    def hidden(self) -> bool:
        return self.f_kind == FinesseKind.HIDDEN

    @property
    def possibly_bluff(self) -> bool:
        return self.f_kind == FinesseKind.POSSIBLY_BLUFF

    @property
    def bluff(self) -> bool:
        return self.f_kind == FinesseKind.BLUFF

    @property
    def certain(self) -> bool:
        return self.f_kind == FinesseKind.CERTAIN

    @property
    def is_bluff(self) -> bool:
        return self.f_kind == FinesseKind.BLUFF

    @property
    def is_possibly_bluff(self) -> bool:
        return self.f_kind in (FinesseKind.POSSIBLY_BLUFF, FinesseKind.BLUFF)


@dataclass(frozen=True, slots=True)
class PositionalConn:
    """Reacting player is clued positionally (slot-based play without being touched)."""

    reacting: int
    order: int
    ids: tuple[Identity, ...]
    # If our involvement is ambiguous, the (target_order, playable_possibilities) for the "us instead" branch.
    ambiguous_own: tuple[int, tuple[Identity, ...]] | None = None

    @property
    def kind(self) -> str:
        return "positional"

    @property
    def hidden(self) -> bool:
        return False

    @property
    def is_bluff(self) -> bool:
        return False

    @property
    def is_possibly_bluff(self) -> bool:
        return False


Connection = KnownConn | PlayableConn | PromptConn | FinesseConn | PositionalConn


@dataclass(frozen=True, slots=True)
class FocusPossibility:
    """A candidate interpretation of a focused clue.

    :param id: The identity the focus card would have under this interpretation.
    :param connections: The connections required for the interpretation to hold.
    :param interp: How the clue is being interpreted (Play, Save, Fix, ...).
    :param symmetric: True if the receiver must entertain this but it's actually false.
    :param ambiguous: True if our involvement is ambiguous (we see another possibility not involving us).
    :param save: True if this is a save clue.
    :param complicated: True if extra plays are required from us (revealing a different suit than expected).
    """

    id: Identity
    connections: tuple[Connection, ...]
    interp: Interp
    symmetric: bool = False
    ambiguous: bool = False
    save: bool = False
    complicated: bool = False

    @property
    def is_bluff(self) -> bool:
        return bool(self.connections) and self.connections[0].is_bluff


@dataclass(frozen=True, slots=True)
class WaitingConnection:
    """A potential set of connections pending more information.

    Tracked until the reacter acts and disambiguates the meaning.

    :param connections: The connections involved if this interpretation is true.
    :param giver: Index of the player who gave the clue.
    :param target: Index of the player who received the clue.
    :param turn: Turn the clue was given.
    :param focus: Order of the focused card.
    :param inference: Identity of the focused card if this interpretation is true.
    """

    connections: tuple[Connection, ...]
    giver: int
    target: int
    turn: int
    focus: int
    inference: Identity
    ambiguous_passback: bool = False
    self_passback: bool = False
    symmetric: bool = False
    ambiguous_self: bool = False

    @property
    def curr_conn(self) -> Connection:
        return self.connections[0]

    def get_next_index(self, state: State) -> int | None:
        """Index of the next connection whose card is still in play, or None if none."""
        for i, conn in enumerate(self.connections):
            if conn.order in state.hands[conn.reacting]:
                return i
        return None
