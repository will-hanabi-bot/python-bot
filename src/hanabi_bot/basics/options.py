"""Table options sent by hanab.live when a game starts.

Port of scala-bot/src/scala_bot/command.scala lines 56-66 (TableOptions case class).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TableOptions:
    """Per-table rule modifiers passed at game start."""

    num_players: int
    variant_name: str
    starting_player: int = 0
    deck_plays: bool = False
    detrimental_characters: bool = False
    empty_clues: bool = False
    one_extra_card: bool = False
    one_less_card: bool = False
    speedrun: bool = False

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> TableOptions:
        return cls(
            num_players=int(obj["numPlayers"]),
            variant_name=str(obj["variantName"]),
            starting_player=int(obj.get("startingPlayer", 0)),
            deck_plays=bool(obj.get("deckPlays", False)),
            detrimental_characters=bool(obj.get("detrimentalCharacters", False)),
            empty_clues=bool(obj.get("emptyClues", False)),
            one_extra_card=bool(obj.get("oneExtraCard", False)),
            one_less_card=bool(obj.get("oneLessCard", False)),
            speedrun=bool(obj.get("speedrun", False)),
        )
