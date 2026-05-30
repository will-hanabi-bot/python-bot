"""Faithful reconstruction of hanab.live replay 1875305 turn 22.

Replay: https://hanab.live/shared-replay/1875305#22
Variant: Muddy-Rainbow-Ones (3 Suits) — suits=[Red, Green, Blue]; rainbow_s + brown_s on rank 1.
Players: will-bot67 (P0, observer), will-bot69 (P1), yagami_black (P2).

State at turn 22 (after 21 prior actions):
- score 12 / max 15; rem_score 3; cards_left=2; clue_tokens=3.
- play_stacks=(2, 5, 5). Need r3, r4, r5 to reach max.

The user-reported scenario: bot67 cannot safely discard. The solution is for bot67
to give a color clue to yagami, which lets bot69 stall on turn 23 (clue red-3
directly OR rank-2 to bot67 as reactive play clue). The bot in the actual replay
discarded — which the user says was wrong.

The recent fix (real clues at depth <= 1) should let the solver explore this line
since bot67's clue at depth 0 plus bot69's response at depth 1 both need real clue
enumeration.
"""

from __future__ import annotations

from hanabi_bot.basics.action import (
    ClueAction,
    DiscardAction,
    DrawAction,
    PerformColour,
    PerformRank,
    PlayAction,
    TurnAction,
)
from hanabi_bot.basics.clue import BaseClue, ClueKind
from hanabi_bot.conventions.reactor import Reactor

from ..conftest import Player, setup

# Hanab.live export deck (https://hanab.live/export/1875305).
# Suit indices: 0=Red, 1=Green, 2=Blue.
_DECK: tuple[tuple[int, int], ...] = (
    (1, 3), (2, 5), (1, 4), (2, 2), (0, 5),
    (2, 3), (0, 1), (1, 3), (1, 4), (1, 1),
    (2, 4), (2, 3), (1, 2), (2, 1), (1, 5),
    (0, 1), (1, 2), (2, 1), (0, 4), (0, 3),
    (2, 2), (0, 2), (1, 1), (2, 1), (2, 4),
    (0, 2), (0, 1), (0, 4), (1, 1), (0, 3),
)


# 21 game actions leading up to (but excluding) bot67's turn 22 decision.
_ORIG_ACTIONS: tuple[dict, ...] = (
    {"type": 3, "target": 2, "value": 2},   # T1 bot67 rank-2 -> yagami
    {"type": 0, "target": 9, "value": 0},   # T2 bot69 plays order 9 (g1)
    {"type": 0, "target": 13, "value": 0},  # T3 yagami plays order 13 (b1)
    {"type": 3, "target": 2, "value": 3},   # T4 bot67 rank-3 -> yagami
    {"type": 0, "target": 15, "value": 0},  # T5 bot69 plays order 15 (r1)
    {"type": 2, "target": 0, "value": 1},   # T6 yagami green -> bot67
    {"type": 0, "target": 3, "value": 0},   # T7 bot67 plays order 3 (b2)
    {"type": 3, "target": 0, "value": 3},   # T8 bot69 rank-3 -> bot67
    {"type": 0, "target": 12, "value": 0},  # T9 yagami plays order 12 (g2)
    {"type": 0, "target": 0, "value": 0},   # T10 bot67 plays order 0 (g3)
    {"type": 3, "target": 0, "value": 5},   # T11 bot69 rank-5 -> bot67
    {"type": 0, "target": 11, "value": 0},  # T12 yagami plays order 11 (b3)
    {"type": 0, "target": 2, "value": 0},   # T13 bot67 plays order 2 (g4)
    {"type": 2, "target": 0, "value": 0},   # T14 bot69 red -> bot67
    {"type": 0, "target": 10, "value": 0},  # T15 yagami plays order 10 (b4)
    {"type": 0, "target": 1, "value": 0},   # T16 bot67 plays order 1 (b5)
    {"type": 2, "target": 0, "value": 0},   # T17 bot69 red -> bot67
    {"type": 0, "target": 14, "value": 0},  # T18 yagami plays order 14 (g5)
    {"type": 1, "target": 24, "value": 0},  # T19 bot67 discards order 24
    {"type": 2, "target": 0, "value": 2},   # T20 bot69 blue -> bot67
    {"type": 0, "target": 21, "value": 0},  # T21 yagami plays order 21 (r2)
)


# bot67 = orig P0 == my P0 (Alice, observer). Identity remappings.
_ORIG_TO_MY_ORDER: dict[int, int] = {o: o for o in range(30)}
_ORIG_TO_MY_PLAYER: dict[int, int] = {0: 0, 1: 1, 2: 2}

# my-order -> ground-truth identity (for computing clue-touched lists when state.deck has -1/-1).
_MY_ORDER_TO_ID: dict[int, tuple[int, int]] = {o: _DECK[o] for o in range(30)}


def _touched_orders(variant, hand_orders, clue_kind: int, clue_value: int) -> tuple[int, ...]:
    from hanabi_bot.basics.identity import Identity
    out = []
    for my_order in hand_orders:
        suit, rank = _MY_ORDER_TO_ID[my_order]
        if variant.id_touched(Identity(suit, rank), clue_kind, clue_value):
            out.append(my_order)
    return tuple(out)


def _apply_orig_action(g, orig_action: dict):
    state = g.state
    pi = state.current_player_index
    g = g.copy_with(catchup=True)
    a_type = orig_action["type"]

    if a_type in (0, 1):
        orig_order = orig_action["target"]
        my_order = _ORIG_TO_MY_ORDER[orig_order]
        suit, rank = _DECK[orig_order]
        if a_type == 0:
            g = g.handle_action(PlayAction(pi, my_order, suit, rank))
        else:
            g = g.handle_action(DiscardAction(pi, my_order, suit, rank, False))
        new_my_order = g.state.next_card_order
        if new_my_order < len(_DECK):
            d_suit, d_rank = _DECK[new_my_order]
            if pi == g.state.our_player_index:
                g = g.handle_action(DrawAction(pi, new_my_order, -1, -1))
            else:
                g = g.handle_action(DrawAction(pi, new_my_order, d_suit, d_rank))
    elif a_type == 2:
        target = _ORIG_TO_MY_PLAYER[orig_action["target"]]
        value = orig_action["value"]
        touched = _touched_orders(g.state.variant, g.state.hands[target], 0, value)
        g = g.handle_action(ClueAction(pi, target, touched, BaseClue(ClueKind.COLOUR, value)))
    elif a_type == 3:
        target = _ORIG_TO_MY_PLAYER[orig_action["target"]]
        value = orig_action["value"]
        touched = _touched_orders(g.state.variant, g.state.hands[target], 1, value)
        g = g.handle_action(ClueAction(pi, target, touched, BaseClue(ClueKind.RANK, value)))
    else:
        raise ValueError(f"Unknown action type: {a_type}")

    g = g.handle_action(TurnAction(g.state.turn_count, g.state.next_player_index(pi)))
    return g.copy_with(catchup=False)


def test_replay_1875305_solver_finds_winning_clue() -> None:
    """At turn 22 (bot67), with `winnable_simpler` offering the reacter's newest
    unclued card as a play candidate when a reactive WC is active, the solver
    finds a winning clue line (`PerformRank(target=1, value=3)` with winrate=1).

    The fix path: depth 0 enumerates clues; depth 1 enumerates clues (the
    earlier depth<=1 fix); depth 2 enumerates the reactive-WC blind-play
    candidate when the reacter's turn lands on an active rank-clue WC. Uses
    the default 30s `EndgameSolver` timeout.
    """
    g = setup(
        Reactor.create,
        hands=[
            # Alice = bot67 (observer): orders 0..4 = (g3, b5, g4, b2, r5)
            # slot-1-first: [4, 3, 2, 1, 0] -> hidden.
            ["xx", "xx", "xx", "xx", "xx"],
            # Bob = bot69: orders 5..9 = (b3, r1, g3, g4, g1)
            # slot-1-first: [9 (g1), 8 (g4), 7 (g3), 6 (r1), 5 (b3)]
            ["g1", "g4", "g3", "r1", "b3"],
            # Cathy = yagami: orders 10..14 = (b4, b3, g2, b1, g5)
            # slot-1-first: [14 (g5), 13 (b1), 12 (g2), 11 (b3), 10 (b4)]
            ["g5", "b1", "g2", "b3", "b4"],
        ],
        variant="Muddy-Rainbow-Ones (3 Suits)",
        starting=Player.ALICE,
    )

    for action in _ORIG_ACTIONS:
        g = _apply_orig_action(g, action)

    # Now at start of turn 22 — bot67's (Alice's) turn.
    assert g.state.current_player_index == Player.ALICE.value
    assert g.state.score == 12
    assert g.state.cards_left == 2
    assert g.state.play_stacks == (2, 5, 5)

    from fractions import Fraction

    from hanabi_bot.endgame.solver import EndgameSolver

    result = EndgameSolver().solve(g)
    assert isinstance(result, tuple), (
        f"solver should find a winning action; got {result!r}"
    )
    perform, winrate = result
    assert winrate == Fraction(1, 1), (
        f"expected winrate 1; got {winrate} (action={perform!r})"
    )
    assert isinstance(perform, (PerformColour, PerformRank)), (
        f"expected a clue (any winning clue); got {perform!r}"
    )
