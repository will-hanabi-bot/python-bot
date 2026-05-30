"""Faithful reconstruction of hanab.live replay 1875304 turn 22.

Replay: https://hanab.live/shared-replay/1875304#22
Variant: Muddy-Rainbow-Ones (3 Suits) — suits=[Red, Green, Blue]; special_rank=1 with
both rainbow_s and brown_s (rank-1 cards are touched by every color clue and by NO
rank clue).
Players (in deal order): will-bot69 (P0, observer), yagami_black (P1), will-bot67 (P2).

Because the observer is the FIRST-dealt player here, no order/player remapping is
needed (unlike `test_replay_1874799.py`). Conftest's order assignment matches the
replay's: orders 0-4 → bot69, 5-9 → yagami, 10-14 → bot67.

State at the start of turn 22 (after 21 prior actions):
- score 11 / max 15; cards_left=1; clue_tokens=5.
- play_stacks=(3, 3, 5).
- bot69's slot 3 is convention-inferred as g4 and marked `CALLED_TO_PLAY` + `urgent`
  by the convention after bot67's turn-21 rank-2 reactive clue to yagami.
- yagami slot 2 holds the OTHER r4 (alongside one in bot67's slot 5); the deck's last
  card is b3 (trash).

The bug: bot69 returns `PerformPlay(slot 3)` via `take_action`'s urgent-action
shortcut WITHOUT consulting the endgame solver. The play leads to a final score of
14 — the game ends one turn too early to fit the remaining r4/r5/g4/g5 plays.

The winning line is a *stall*: clue rank-4 to yagami. The deck doesn't deplete this
turn, buying one extra endgame turn — enough for yagami to play r4 (revealed by the
clue + elim), bot67 to play r5 (now uniquely identifiable since r2/g2/g5 are all
visibly accounted), bot69 to play g4, and yagami to play g5. Final score 15.

Post-fix:
1. `take_action` runs the endgame solver BEFORE the urgent-action return when
   `rem_score <= len(suits)+1`.
2. `possible_actions` falls through when the urgent action is unwinnable, so the
   solver can enumerate alternative actions (the winning clue).
"""

from __future__ import annotations

from hanabi_bot.basics.action import (
    ClueAction,
    DiscardAction,
    DrawAction,
    PerformColour,
    PerformDiscard,
    PerformPlay,
    PerformRank,
    PlayAction,
    TurnAction,
)
from hanabi_bot.basics.clue import BaseClue, ClueKind
from hanabi_bot.conventions.reactor import Reactor

from ..conftest import Player, setup

# Hanab.live export deck (https://hanab.live/export/1875304): one (suit, rank) tuple
# per draw order 0..29. Suit indices: 0=Red, 1=Green, 2=Blue.
_DECK: tuple[tuple[int, int], ...] = (
    (2, 4), (0, 2), (2, 3), (1, 4), (2, 5),
    (0, 3), (2, 1), (1, 1), (1, 3), (1, 2),
    (0, 4), (2, 4), (0, 5), (2, 2), (0, 3),
    (1, 5), (1, 3), (1, 2), (1, 4), (1, 1),
    (0, 1), (0, 1), (2, 1), (1, 1), (2, 2),
    (0, 2), (0, 4), (0, 1), (2, 1), (2, 3),
)


# First 21 actions of the original replay, leading up to (but excluding) bot69's
# turn-22 decision. types: 0=Play, 1=Discard, 2=Color clue, 3=Rank clue.
_ORIG_ACTIONS: tuple[dict, ...] = (
    {"type": 3, "target": 2, "value": 3},   # bot69 rank-3 -> bot67
    {"type": 0, "target": 6, "value": 0},   # yagami plays order 6 (b1)
    {"type": 0, "target": 13, "value": 0},  # bot67 plays order 13 (b2)
    {"type": 2, "target": 2, "value": 2},   # bot69 blue (suit-2 / Blue) -> bot67
    {"type": 0, "target": 7, "value": 0},   # yagami plays order 7 (g1)
    {"type": 3, "target": 1, "value": 3},   # bot67 rank-3 -> yagami
    {"type": 0, "target": 2, "value": 0},   # bot69 plays order 2 (b3)
    {"type": 0, "target": 17, "value": 0},  # yagami plays order 17 (g2)
    {"type": 0, "target": 11, "value": 0},  # bot67 plays order 11 (b4)
    {"type": 3, "target": 2, "value": 4},   # bot69 rank-4 -> bot67
    {"type": 0, "target": 8, "value": 0},   # yagami plays order 8 (g3)
    {"type": 3, "target": 1, "value": 5},   # bot67 rank-5 -> yagami
    {"type": 0, "target": 4, "value": 0},   # bot69 plays order 4 (b5)
    {"type": 0, "target": 21, "value": 0},  # yagami plays order 21 (r1)
    {"type": 1, "target": 20, "value": 0},  # bot67 discards order 20 (r1)
    {"type": 1, "target": 22, "value": 0},  # bot69 discards order 22 (b1)
    {"type": 1, "target": 23, "value": 0},  # yagami discards order 23 (g1)
    {"type": 2, "target": 1, "value": 0},   # bot67 red -> yagami
    {"type": 0, "target": 1, "value": 0},   # bot69 plays order 1 (r2)
    {"type": 0, "target": 5, "value": 0},   # yagami plays order 5 (r3)
    {"type": 3, "target": 1, "value": 2},   # bot67 rank-2 -> yagami  (THE REACTIVE CLUE)
)


# bot69 is orig P0 == our P0 (Alice), so identity remappings for both orders and players.
_ORIG_TO_MY_ORDER: dict[int, int] = {o: o for o in range(30)}
_ORIG_TO_MY_PLAYER: dict[int, int] = {0: 0, 1: 1, 2: 2}


# my-order → ground-truth (suit, rank). For Alice's (= bot69's) cards, state.deck has
# (-1, -1) since the bot can't see its own draws — but the convention's clue-touched
# checks need real ids, so we keep a side-table.
_MY_ORDER_TO_ID: dict[int, tuple[int, int]] = {o: _DECK[o] for o in range(30)}


def _touched_orders(variant, hand_orders, clue_kind: int, clue_value: int) -> tuple[int, ...]:
    """Compute the touched orders using ground-truth ids (independent of state.deck).

    Necessary because `state.clue_touched` skips orders whose state.deck entry is
    (-1, -1) — which is the case for every card in the observer's (Alice's) hand.
    """
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


def test_replay_1875304_setup_invokes_endgame_solver() -> None:
    """At turn 22, the bot's urgent g4 play must NOT shortcut past the endgame solver.

    Validates the `take_action` reorder: in late game, the solver runs even when
    `meta[order].urgent` is set on a called-to-play card. The behavioral assertion
    (the solver finds the winning clue) lives in the test below.
    """
    # Initial deal — no remapping needed (bot69 == my P0 == Alice).
    # slot-1-first orderings:
    # - Alice (= bot69, observer): orders [4, 3, 2, 1, 0] = (b5, g4, b3, r2, b4) → hidden.
    # - Bob   (= yagami):           orders [9, 8, 7, 6, 5] = (g2, g3, g1, b1, r3).
    # - Cathy (= bot67):            orders [14, 13, 12, 11, 10] = (r3, b2, r5, b4, r4).
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["g2", "g3", "g1", "b1", "r3"],
            ["r3", "b2", "r5", "b4", "r4"],
        ],
        variant="Muddy-Rainbow-Ones (3 Suits)",
        starting=Player.ALICE,
    )

    for action in _ORIG_ACTIONS:
        g = _apply_orig_action(g, action)

    # Now at start of turn 22 — bot69's turn.
    assert g.state.current_player_index == Player.ALICE.value, (
        f"expected bot69 (Alice) to act; got player {g.state.current_player_index}"
    )
    assert g.state.score == 11, f"expected score=11, got {g.state.score}"
    assert g.state.cards_left == 1, f"expected cards_left=1, got {g.state.cards_left}"
    assert g.state.play_stacks == (3, 3, 5), (
        f"expected play_stacks=(3,3,5), got {g.state.play_stacks}"
    )

    # Sanity: bot69's slot 3 is the called-to-play g4 (= order 18 in this game).
    from hanabi_bot.basics.card import CardStatus
    g4_my_order = _ORIG_TO_MY_ORDER[18]
    assert g4_my_order == 18
    alice_slot3 = g.state.hands[Player.ALICE.value][2]
    assert alice_slot3 == g4_my_order, (
        f"slot 3 should be order {g4_my_order}; got {alice_slot3}"
    )
    assert g.meta[g4_my_order].status == CardStatus.CALLED_TO_PLAY
    assert g.meta[g4_my_order].urgent, (
        "expected the called-to-play g4 to be marked urgent (otherwise this test "
        "isn't exercising the urgent-action shortcut bug)"
    )

    # Verify the endgame solver IS invoked by injecting a spy onto the solver class.
    # Pre-fix: the urgent-action shortcut would return PerformPlay(g4) before the
    # solver block ran. Post-fix: the solver block runs first.
    from hanabi_bot.endgame import solver as solver_mod
    invoked = {"count": 0}
    original_solve = solver_mod.EndgameSolver.solve

    def spy(self, game, only_action=None):
        invoked["count"] += 1
        return original_solve(self, game, only_action)

    solver_mod.EndgameSolver.solve = spy
    try:
        g.take_action()
    finally:
        solver_mod.EndgameSolver.solve = original_solve

    assert invoked["count"] >= 1, (
        f"endgame solver wasn't invoked (urgent-action shortcut bypassed it). "
        f"invocations: {invoked['count']}"
    )


def test_replay_1875304_solver_finds_winning_clue() -> None:
    """With real-clue enumeration at depth <= 1, the solver explores both
    bot69's clue AND the responding clue/play yagami can make. It then picks a
    winning stall clue rather than the losing g4 play. We don't pin the EXACT
    clue (rank-4 → yagami via yagami's rank-5 → bot67; alternatively yagami's
    play-r4-after-rank-4-clue + bot67-plays-r5-via-visible-elim; etc. — any
    winning clue suffices).
    """
    g = setup(
        Reactor.create,
        hands=[
            ["xx", "xx", "xx", "xx", "xx"],
            ["g2", "g3", "g1", "b1", "r3"],
            ["r3", "b2", "r5", "b4", "r4"],
        ],
        variant="Muddy-Rainbow-Ones (3 Suits)",
        starting=Player.ALICE,
    )
    for action in _ORIG_ACTIONS:
        g = _apply_orig_action(g, action)
    perform = g.take_action()
    g4_my_order = _ORIG_TO_MY_ORDER[18]
    assert not (isinstance(perform, PerformPlay) and perform.target == g4_my_order), (
        f"bot picked the buggy line (play g4); expected a clue. perform={perform!r}"
    )
    assert isinstance(perform, (PerformColour, PerformRank)), (
        f"expected a clue, got {perform!r}"
    )
    assert not isinstance(perform, PerformDiscard)
