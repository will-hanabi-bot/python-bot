"""Action ADT and PerformAction ADT.

Port of scala-bot/src/scala_bot/basics/Action.scala.

Action represents an event sent FROM hanab.live describing the game's progression
(e.g. a clue happened, a card was drawn, a strike occurred). PerformAction
represents an action sent BY a player TO hanab.live (e.g. play this card,
clue this player).

The Scala source defines methods like `fmt(state, ...)` and `to_action(state, ...)`
which depend on the State type. Those are deferred to Stage 2.

InterpAction wraps a ClueInterp object; ClueInterp lives in the conventions
layer (Stage 4). For Stage 1, InterpAction holds an opaque `interp: object`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from .clue import BaseClue, ClueKind

# --- Inbound game-state events (server -> bot) ---


@dataclass(frozen=True, slots=True)
class StatusAction:
    """Status update: current clue count, score, and max remaining possible score."""

    clues: int
    score: int
    max_score: int

    @property
    def player_index(self) -> int:
        return -1

    @property
    def requires_draw(self) -> bool:
        return False

    @property
    def is_player_action(self) -> bool:
        return False

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> StatusAction:
        return cls(int(obj["clues"]), int(obj["score"]), int(obj["maxScore"]))


@dataclass(frozen=True, slots=True)
class TurnAction:
    """Turn changed. `num` is 1-indexed."""

    num: int
    current_player_index: int

    @property
    def player_index(self) -> int:
        return self.current_player_index

    @property
    def requires_draw(self) -> bool:
        return False

    @property
    def is_player_action(self) -> bool:
        return False

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> TurnAction:
        return cls(int(obj["num"]), int(obj["currentPlayerIndex"]))


@dataclass(frozen=True, slots=True)
class ClueAction:
    """A clue was given.

    :param list_: Card orders touched by the clue.
    """

    giver: int
    target: int
    list_: tuple[int, ...]
    clue: BaseClue

    @property
    def player_index(self) -> int:
        return self.giver

    @property
    def requires_draw(self) -> bool:
        return False

    @property
    def is_player_action(self) -> bool:
        return True

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> ClueAction:
        clue_obj = obj["clue"]
        kind = ClueKind.COLOUR if int(clue_obj["type"]) == 0 else ClueKind.RANK
        return cls(
            giver=int(obj["giver"]),
            target=int(obj["target"]),
            list_=tuple(int(x) for x in obj["list"]),
            clue=BaseClue(kind, int(clue_obj["value"])),
        )


@dataclass(frozen=True, slots=True)
class DrawAction:
    """A card was drawn. suit_index/rank are -1 if hidden from the bot's perspective."""

    player_index: int
    order: int
    suit_index: int
    rank: int

    @property
    def requires_draw(self) -> bool:
        return False

    @property
    def is_player_action(self) -> bool:
        return False

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> DrawAction:
        return cls(
            player_index=int(obj["playerIndex"]),
            order=int(obj["order"]),
            suit_index=int(obj["suitIndex"]),
            rank=int(obj["rank"]),
        )


@dataclass(frozen=True, slots=True)
class PlayAction:
    """A card was successfully played."""

    player_index: int
    order: int
    suit_index: int
    rank: int

    @property
    def requires_draw(self) -> bool:
        return True

    @property
    def is_player_action(self) -> bool:
        return True

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> PlayAction:
        return cls(
            player_index=int(obj["playerIndex"]),
            order=int(obj["order"]),
            suit_index=int(obj["suitIndex"]),
            rank=int(obj["rank"]),
        )


@dataclass(frozen=True, slots=True)
class DiscardAction:
    """A card was discarded. `failed` is True if the discard was due to a strike (bombed play)."""

    player_index: int
    order: int
    suit_index: int
    rank: int
    failed: bool = False

    @property
    def requires_draw(self) -> bool:
        return True

    @property
    def is_player_action(self) -> bool:
        return True

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> DiscardAction:
        return cls(
            player_index=int(obj["playerIndex"]),
            order=int(obj["order"]),
            suit_index=int(obj["suitIndex"]),
            rank=int(obj["rank"]),
            failed=bool(obj["failed"]),
        )


@dataclass(frozen=True, slots=True)
class StrikeAction:
    """A strike was incurred. `num` is the new strike count."""

    num: int
    turn: int
    order: int

    @property
    def player_index(self) -> int:
        return -1

    @property
    def requires_draw(self) -> bool:
        return False

    @property
    def is_player_action(self) -> bool:
        return False

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> StrikeAction:
        return cls(num=int(obj["num"]), turn=int(obj["turn"]), order=int(obj["order"]))


class EndCondition(enum.Enum):
    IN_PROGRESS = 0
    NORMAL = 1
    STRIKEOUT = 2
    TIMEOUT = 3
    TERMINATED = 4
    SPEEDRUN_FAIL = 5
    IDLE_TIMEOUT = 6
    CHARACTER_SOFTLOCK = 7
    ALL_OR_NOTHING_FAIL = 8
    ALL_OR_NOTHING_SOFTLOCK = 9
    TERMINATED_BY_VOTE = 10


@dataclass(frozen=True, slots=True)
class GameOverAction:
    """The game ended. end_condition is the ordinal of EndCondition."""

    end_condition: int
    player_index: int

    @property
    def requires_draw(self) -> bool:
        return False

    @property
    def is_player_action(self) -> bool:
        return False

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> GameOverAction:
        return cls(end_condition=int(obj["endCondition"]), player_index=int(obj["playerIndex"]))


@dataclass(frozen=True, slots=True)
class InterpAction:
    """Synthetic action used internally to attach a clue interpretation after rewinding.

    The `interp` field holds a ClueInterp, but ClueInterp lives in the conventions
    layer (Stage 4). For now, it's an opaque object.
    """

    interp: Any  # ClueInterp once Stage 4 lands

    @property
    def player_index(self) -> int:
        return -1

    @property
    def requires_draw(self) -> bool:
        return False

    @property
    def is_player_action(self) -> bool:
        return False


Action = (
    StatusAction
    | TurnAction
    | ClueAction
    | DrawAction
    | PlayAction
    | DiscardAction
    | StrikeAction
    | GameOverAction
    | InterpAction
)


def action_from_json(obj: dict[str, Any]) -> Action | None:
    """Parse a single game action from a server JSON message.

    Returns None for unknown action types (matches Scala's Action.fromJSON).
    """
    match obj.get("type"):
        case "clue":
            return ClueAction.from_json(obj)
        case "discard":
            return DiscardAction.from_json(obj)
        case "play":
            return PlayAction.from_json(obj)
        case "draw":
            return DrawAction.from_json(obj)
        case "status":
            return StatusAction.from_json(obj)
        case "turn":
            return TurnAction.from_json(obj)
        case "strike":
            return StrikeAction.from_json(obj)
        case "gameOver":
            return GameOverAction.from_json(obj)
        case _:
            return None


# --- Outbound actions (bot -> server) ---


@dataclass(frozen=True, slots=True)
class PerformPlay:
    target: int  # card order

    @property
    def is_clue(self) -> bool:
        return False

    @property
    def hash_int(self) -> int:
        return self.target

    def to_json(self, table_id: int) -> dict[str, int]:
        return {"tableID": table_id, "type": 0, "target": self.target}


@dataclass(frozen=True, slots=True)
class PerformDiscard:
    target: int  # card order

    @property
    def is_clue(self) -> bool:
        return False

    @property
    def hash_int(self) -> int:
        return 10 + self.target

    def to_json(self, table_id: int) -> dict[str, int]:
        return {"tableID": table_id, "type": 1, "target": self.target}


@dataclass(frozen=True, slots=True)
class PerformColour:
    """Colour clue. `target` = player index, `value` = colourable-suit index."""

    target: int
    value: int

    @property
    def is_clue(self) -> bool:
        return True

    @property
    def hash_int(self) -> int:
        return 20 + self.target + self.value * 100

    def to_json(self, table_id: int) -> dict[str, int]:
        return {"tableID": table_id, "type": 2, "target": self.target, "value": self.value}


@dataclass(frozen=True, slots=True)
class PerformRank:
    """Rank clue. `target` = player index, `value` = rank (1..5)."""

    target: int
    value: int

    @property
    def is_clue(self) -> bool:
        return True

    @property
    def hash_int(self) -> int:
        return 30 + self.target + self.value * 100

    def to_json(self, table_id: int) -> dict[str, int]:
        return {"tableID": table_id, "type": 3, "target": self.target, "value": self.value}


@dataclass(frozen=True, slots=True)
class PerformTerminate:
    """Terminate the game (e.g. via /terminate command)."""

    target: int
    value: int

    @property
    def is_clue(self) -> bool:
        return False

    @property
    def hash_int(self) -> int:
        return -1

    def to_json(self, table_id: int) -> dict[str, int]:
        return {"tableID": table_id, "type": 4, "target": self.target, "value": self.value}


PerformAction = PerformPlay | PerformDiscard | PerformColour | PerformRank | PerformTerminate


def perform_action_from_json(obj: dict[str, Any]) -> PerformAction:
    """Parse a PerformAction from a server-bound JSON object.

    Port of PerformAction.fromJSON.
    """
    action_type = int(obj["type"])
    target = int(obj["target"])
    value = int(obj.get("value", 0))
    match action_type:
        case 0:
            return PerformPlay(target)
        case 1:
            return PerformDiscard(target)
        case 2:
            return PerformColour(target, value)
        case 3:
            return PerformRank(target, value)
        case 4:
            return PerformTerminate(target, value)
        case _:
            raise ValueError(f"Unknown PerformAction type: {action_type}")
