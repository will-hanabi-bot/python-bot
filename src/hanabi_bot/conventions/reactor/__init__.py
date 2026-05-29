"""Reactor convention port.

Port of scala-bot/src/scala_bot/reactor/.

Public entry point: `Reactor` — a Game subclass that overrides the convention hooks
(interpret_clue/interpret_discard/interpret_play/update_turn/take_action).
"""

from .reactor import Reactor, ReactorWC

__all__ = ["Reactor", "ReactorWC"]
