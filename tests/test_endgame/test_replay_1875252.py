"""Endgame test for hanab.live replay 1875252 turn 25.

Scenario (paraphrased): 3p, variant Omni-Ones & Light Pink (3 Suits). It's turn 24
in the bot's count (turn 25 in the replay UI's 1-indexed scheme). Score 14, max 15,
deck depleted, one card left to play — Light Pink 5 in yagami_black's (player 2's) hand.
yagami doesn't know which of his slots holds it. bot69 (player 0) must give a clue
that *communicates* yagami's Light-Pink-5 slot, otherwise yagami can't play it.

Pre-fix bug: the endgame solver's omniscient leaf check claimed `winrate=1` for
`PerformRank(target=1, value=3)` (rank-3 to will-bot67) — but that clue doesn't mark
Light Pink 5 as CALLED_TO_PLAY for yagami, so in reality the team strands the last
point.

Post-fix: `winnable_simpler` consults each player's own knowledge, so a clue that
doesn't communicate the must-play card is no longer treated as winnable. The solver
returns a clue that *does* mark yagami's Light-Pink-5 slot as CALLED_TO_PLAY.
"""

from __future__ import annotations

from fractions import Fraction

from hanabi_bot.basics.action import (
    PerformColour,
    PerformDiscard,
    PerformPlay,
    PerformRank,
)
from hanabi_bot.basics.card import CardStatus
from hanabi_bot.conventions.reactor import Reactor
from hanabi_bot.endgame.solver import EndgameSolver

from ..conftest import Player, setup


def _yagami_i5_order(game) -> int:
    """The deck order of Light Pink 5 (i5) in yagami's hand."""
    yagami_hand = game.state.hands[Player.CATHY.value]
    for o in yagami_hand:
        id_ = game.state.deck[o].id()
        if id_ is not None and id_.suit_index == 2 and id_.rank == 5:
            return o
    raise AssertionError("Light Pink 5 not found in yagami's hand")


def test_solver_picks_clue_that_communicates_must_play_card() -> None:
    """The chosen clue must, after simulation, mark yagami's Light-Pink-5 slot CALLED_TO_PLAY."""
    g = setup(
        Reactor.create,
        hands=[
            # bot69 (P0): hidden. Will hold the 5 unseen cards (r1, b1, b1, i1, i2) per
            # the variant's remaining-card budget after the play stacks + discard + others.
            ["xx", "xx", "xx", "xx", "xx"],
            # bot67 (P1): all dead cards (already-played duplicates / trash).
            ["r2", "b3", "b4", "i1", "i3"],
            # yagami (P2): contains i5 (Light Pink 5) at slot 1. Other slots dead.
            ["i5", "i4", "b2", "r3", "r4"],
        ],
        play_stacks=[5, 5, 4],
        discarded=["r1"],
        variant="Omni-Ones & Light Pink (3 Suits)",
        starting=Player.ALICE,
    )
    assert g.state.score == 14
    assert g.state.max_score == 15
    assert g.state.cards_left == 0

    result = EndgameSolver(timeout=10.0).solve(g)
    assert isinstance(result, tuple), (
        f"expected (action, winrate), got {result!r} — solver should find a winning clue"
    )
    perform, winrate = result
    assert winrate == Fraction(1, 1), f"expected winrate 1, got {winrate}"

    # The chosen action must be a clue — there's no immediately-playable card in bot69's
    # hand (everything they hold is trash given play_stacks = [5, 5, 4]).
    assert isinstance(perform, (PerformColour, PerformRank)), (
        f"expected a clue, got {perform!r}"
    )
    assert not isinstance(perform, (PerformPlay, PerformDiscard))

    # The bug action was PerformRank(target=1, value=3) (rank-3 to will-bot67); explicitly
    # rule it out. The fix's winrate model should never let it pass.
    assert not (isinstance(perform, PerformRank) and perform.target == Player.BOB.value and perform.value == 3), (
        f"solver picked the bug action {perform!r}"
    )

    # The clue must be directed at the player who actually holds the must-play card
    # (yagami / Player.CATHY) — either a stable clue that marks i5 directly, or a
    # reactive clue whose interpretation routes the play to yagami.
    assert perform.target == Player.CATHY.value, (
        f"clue should target yagami (player {Player.CATHY.value}), got target={perform.target}"
    )

    # If the chosen clue is a stable clue (a clue whose focus is i5 in yagami's hand),
    # the simulation already marks i5 as CALLED_TO_PLAY. For reactive clues that mark
    # happens only after the reacter acts, so we don't require it here. Either way the
    # clue must be a legal interpretation that the convention doesn't drop as a mistake.
    i5_order = _yagami_i5_order(g)
    action = EndgameSolver._perform_to_action(perform, g, Player.ALICE.value)
    new_game = g.simulate_action(action)
    i5_status = new_game.meta[i5_order].status
    is_stable_mark = i5_status == CardStatus.CALLED_TO_PLAY
    is_reactive_clue = new_game.waiting is not None
    assert is_stable_mark or is_reactive_clue, (
        f"clue {perform!r} neither marks i5 as CALLED_TO_PLAY directly nor sets up a "
        f"reactive waiting connection (status={i5_status!r}, waiting={new_game.waiting!r})"
    )
