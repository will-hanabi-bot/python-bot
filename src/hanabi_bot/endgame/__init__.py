"""Monte Carlo endgame solver.

Port of scala-bot/src/scala_bot/endgame/. Entered from `Reactor.take_action`
when `state.rem_score <= len(state.variant.suits) + 1`.
"""

from .solver import EndgameSolver

__all__ = ["EndgameSolver"]
