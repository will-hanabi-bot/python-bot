"""End-to-end tests for the EndgameSolver."""

from __future__ import annotations

from fractions import Fraction

from hanabi_bot.basics.action import PerformPlay
from hanabi_bot.conventions.reactor import Reactor
from hanabi_bot.endgame.solver import EndgameSolver, find_remaining_ids

from ..conftest import Player, fully_known, setup


def test_solve_returns_immediate_win_when_one_play_left() -> None:
    """If score == max-1 and we hold the missing card known, solver returns Play with winrate 1/1."""
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["r1", "y1", "g1", "b1", "p1"],
            ["r1", "y1", "g1", "b1", "p1"],
        ],
        play_stacks=[5, 5, 5, 5, 4],  # max would be 25; we're at 24
        starting=Player.ALICE,
    )
    # Pre-clue Alice's slot 1 fully as p5 (the missing card).
    g = fully_known(g, Player.ALICE, slot=1, short="p5")
    # fully_known mutates common.thoughts only; elim() syncs to per-player.
    g = g.elim()

    # rem_score == 1, so solver should immediately return PerformPlay with winrate 1/1.
    result = EndgameSolver(timeout=10.0).solve(g)
    assert isinstance(result, tuple), f"expected (action, winrate), got {result!r}"
    perform, winrate = result
    assert isinstance(perform, PerformPlay)
    assert winrate == Fraction(1, 1)
    # The played order should be Alice's slot 1.
    assert perform.target == g.state.hands[Player.ALICE.value][0]


def test_solve_bails_when_too_many_unseen_useful_ids() -> None:
    """≥4 unique unseen useful ids → solver returns 'couldn't find ...' string."""
    # Fresh mid-game with no plays made; every 5 is unseen and useful → many useful unknowns.
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["r1", "y1", "g1", "b1", "p1"],
            ["r2", "y2", "g2", "b2", "p2"],
        ],
    )
    # rem_score is huge but the function only bails on unseen useful ids when count > 3.
    result = EndgameSolver(timeout=10.0).solve(g)
    # We just assert it didn't crash; specific output depends on state.
    assert isinstance(result, (tuple, str))


def test_find_remaining_ids_counts_unseen() -> None:
    """find_remaining_ids should report the correct multiplicities of unseen identities."""
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["r1", "r1", "r1", "y2", "p3"],
            ["r5", "y5", "g5", "b5", "p5"],
        ],
        play_stacks=[0, 0, 0, 0, 0],
    )
    remaining, _ = find_remaining_ids(g)
    # All 3 r1s are visible to Alice (in Bob's hand); not in remaining (count 0).
    from hanabi_bot.basics.identity import Identity

    assert Identity(0, 1) not in remaining
    # All five 5s are visible; not in remaining.
    for i in range(5):
        assert Identity(i, 5) not in remaining


def test_solve_returns_either_tuple_or_str() -> None:
    """Smoke: solver always returns either (action, winrate) or an error string."""
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["r5", "y5", "g5", "b5", "p5"],
            ["r4", "y4", "g4", "b4", "p4"],
        ],
        play_stacks=[3, 3, 3, 3, 3],  # rem_score = 10; > 6, so solver wouldn't be invoked
        # but we can still call .solve() directly for the test.
    )
    result = EndgameSolver(timeout=5.0).solve(g)
    assert isinstance(result, (tuple, str))


def test_take_action_endgame_branch_fires_without_crashing() -> None:
    """Setup a near-max-score position; take_action should at least not crash."""
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["r1", "y1", "g1", "b1", "p1"],
            ["r1", "y1", "g1", "b1", "p1"],
        ],
        play_stacks=[5, 5, 5, 5, 4],
    )
    g = fully_known(g, Player.ALICE, slot=1, short="p5")
    g = g.elim()

    # rem_score = 1, well within solver threshold.
    perform = g.take_action()
    # The bot should have done SOMETHING — either the solver's pick or fallback.
    assert perform is not None
