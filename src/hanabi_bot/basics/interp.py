"""Interpretation enums attached to game actions.

Port of scala-bot/src/scala_bot/basics/Game.scala lines 30-42 (Interp, ClueInterp,
PlayInterp, DiscardInterp).

The Scala source declares ClueInterp/PlayInterp/DiscardInterp as enums that
extend a sealed `Interp` trait. In Python we don't need the trait; we use a
plain `Interp` Union type alias for places that accept any of the three.
"""

from __future__ import annotations

import enum


class ClueInterp(enum.Enum):
    MISTAKE = "Mistake"
    REACTIVE = "Reactive"
    PLAY = "Play"
    SAVE = "Save"
    DISCARD = "Discard"
    LOCK = "Lock"
    REVEAL = "Reveal"
    FIX = "Fix"
    STALL = "Stall"
    DISTRIBUTION = "Distribution"
    USELESS = "Useless"


class PlayInterp(enum.Enum):
    NONE = "None"
    MISTAKE = "Mistake"
    ORDER_CM = "OrderCM"


class DiscardInterp(enum.Enum):
    NONE = "None"
    MISTAKE = "Mistake"
    SARCASTIC = "Sarcastic"
    GENTLEMANS_DISCARD = "GentlemansDiscard"
    EMERGENCY = "Emergency"
    POSITIONAL = "Positional"
    SACRIFICE = "Sacrifice"


Interp = ClueInterp | PlayInterp | DiscardInterp
