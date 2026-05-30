"""Faithful reconstruction of hanab.live replay 1874799 turn 23.

Replay: https://hanab.live/shared-replay/1874799#23
Variant: Pink-Ones & Null (3 Suits) — suits=[Red, Blue, Null], special_rank=1 (pink_s).
Real players (in deal order): bot67 (orig P0), bot69 (orig P1, observer), yagami (orig P2).

Since `tests/conftest.py` hardcodes `our_player_index=0`, we relabel players so the
bot's perspective (bot69) is our Player.ALICE (P0). Turn order then becomes:
Cathy (=bot67) → Alice (=bot69) → Bob (=yagami) → Cathy, matching the original cycle.

The replay's `actions` and `deck` arrays use the ORIGINAL ordering. We remap order
indices and player indices when applying them to the test's perspective.

State at the start of turn 23 (after 22 prior actions):
- score 12 / max 15; cards_left=1; clue_tokens=2.
- play_stacks=(4, 4, 4).
- Bot (bot69 / Alice) holds [b4, r3, r1, b1, null-5] in slots 1..5, where slot 5
  (null-5) is convention-inferred from yagami's turn-21 rank-5 clue + elimination.
- yagami (Bob) holds [r1, b2, b5, null-3, r5]; b5 and r5 are both called-to-play.
- bot67 (Cathy) holds [null-2, null-1, null-1, r4, b3] — all dead.
- The deck's last card is null-4.

The user-reported bug: at turn 23 bot69 plays null-5, drawing null-4 (now stuck in
bot's hand on its endgame turn). Final score 14 — one point short of max.

Winning line: stall (any clue). yagami plays r5, drawing null-4 into yagami's hand;
bot67 discards; bot69 plays null-5; yagami plays b5 → score 15.

Pre-fix the endgame solver claimed winrate=1 for the play because its omniscient leaf
read ground-truth identities from `state.deck` for every hand — so it "assumed" each
player would play the right card, regardless of whether they actually knew which one.
Post-fix `winnable_simpler` uses per-player thoughts/meta, so the play-immediately
line correctly fails to reach max.
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

# Hanab.live export deck (https://hanab.live/export/1874799): one (suit, rank) tuple
# per ORIGINAL draw order 0..29.
_DECK: tuple[tuple[int, int], ...] = (
    (2, 4), (1, 3), (0, 4), (2, 3), (0, 4),
    (2, 5), (0, 3), (1, 1), (1, 3), (1, 1),
    (0, 2), (0, 5), (2, 2), (0, 1), (2, 1),
    (1, 2), (2, 3), (0, 1), (1, 5), (0, 2),
    (1, 4), (0, 3), (1, 2), (1, 1), (2, 1),
    (0, 1), (2, 1), (1, 4), (2, 2), (2, 4),
)


# The first 22 actions of the original replay, leading up to (but excluding) bot69's
# turn-23 decision. types: 0=Play, 1=Discard, 2=Color clue, 3=Rank clue.
_ORIG_ACTIONS: tuple[dict, ...] = (
    {"type": 3, "target": 2, "value": 2},
    {"type": 0, "target": 9, "value": 0},
    {"type": 0, "target": 14, "value": 0},
    {"type": 3, "target": 2, "value": 3},
    {"type": 0, "target": 15, "value": 0},
    {"type": 0, "target": 13, "value": 0},
    {"type": 3, "target": 2, "value": 5},
    {"type": 0, "target": 8, "value": 0},
    {"type": 0, "target": 12, "value": 0},
    {"type": 2, "target": 2, "value": 1},
    {"type": 1, "target": 19, "value": 0},
    {"type": 0, "target": 10, "value": 0},
    {"type": 3, "target": 2, "value": 2},
    {"type": 0, "target": 6, "value": 0},
    {"type": 2, "target": 0, "value": 0},
    {"type": 0, "target": 3, "value": 0},
    {"type": 2, "target": 2, "value": 1},
    {"type": 0, "target": 20, "value": 0},
    {"type": 0, "target": 2, "value": 0},
    {"type": 1, "target": 23, "value": 0},
    {"type": 3, "target": 1, "value": 5},
    {"type": 0, "target": 0, "value": 0},
)


# Map original deal-order → my deal-order. conftest assigns orders 0..4 to my P0
# (=bot69), 5..9 to my P1 (=yagami), 10..14 to my P2 (=bot67). Later draws keep
# the same chronological order as the original.
_ORIG_TO_MY_ORDER: dict[int, int] = {}
for _o in range(0, 5):
    _ORIG_TO_MY_ORDER[_o] = _o + 10   # bot67 cards
for _o in range(5, 10):
    _ORIG_TO_MY_ORDER[_o] = _o - 5    # bot69 cards
for _o in range(10, 15):
    _ORIG_TO_MY_ORDER[_o] = _o - 5    # yagami cards
for _o in range(15, 30):
    _ORIG_TO_MY_ORDER[_o] = _o        # later draws unchanged

# Map original player-index → my player-index.
_ORIG_TO_MY_PLAYER: dict[int, int] = {0: 2, 1: 0, 2: 1}


# my-order → (suit, rank) ground-truth identity. Initialized with the deal positions
# (Alice = bot69's hidden hand still maps to real ids — we need this for computing
# clue-touched lists, since `state.deck` has (-1, -1) for unrevealed alice cards).
_MY_ORDER_TO_ID: dict[int, tuple[int, int]] = {}
for _i in range(5):
    _MY_ORDER_TO_ID[_i] = _DECK[5 + _i]            # alice (= bot69)
    _MY_ORDER_TO_ID[5 + _i] = _DECK[10 + _i]       # bob   (= yagami)
    _MY_ORDER_TO_ID[10 + _i] = _DECK[_i]           # cathy (= bot67)
for _i in range(15, 30):
    _MY_ORDER_TO_ID[_i] = _DECK[_i]                # later draws keep original order


def _touched_orders(variant, hand_orders, clue_kind: int, clue_value: int) -> tuple[int, ...]:
    """Compute the touched orders using ground-truth ids (independent of state.deck)."""
    from hanabi_bot.basics.identity import Identity
    out = []
    for my_order in hand_orders:
        suit, rank = _MY_ORDER_TO_ID[my_order]
        if variant.id_touched(Identity(suit, rank), clue_kind, clue_value):
            out.append(my_order)
    return tuple(out)


def _apply_orig_action(g, orig_action: dict):
    """Apply one replay action, remapping orders and player indices to my perspective."""
    state = g.state
    pi = state.current_player_index  # my-player-index, derived from turn order
    g = g.copy_with(catchup=True)
    a_type = orig_action["type"]

    if a_type in (0, 1):  # play / discard
        orig_order = orig_action["target"]
        my_order = _ORIG_TO_MY_ORDER[orig_order]
        # The played/discarded card's identity comes from the original deck.
        suit, rank = _DECK[orig_order]
        if a_type == 0:
            g = g.handle_action(PlayAction(pi, my_order, suit, rank))
        else:
            g = g.handle_action(DiscardAction(pi, my_order, suit, rank, False))
        # Draw the next card from the original deck if any remain.
        new_my_order = g.state.next_card_order
        # new_my_order corresponds to original order (same chronological position)
        # because for orders >= 15 my mapping is identity.
        if new_my_order < len(_DECK):
            d_suit, d_rank = _DECK[new_my_order]
            if pi == g.state.our_player_index:
                # The bot doesn't see its own draws.
                g = g.handle_action(DrawAction(pi, new_my_order, -1, -1))
            else:
                g = g.handle_action(DrawAction(pi, new_my_order, d_suit, d_rank))
    elif a_type == 2:  # color clue
        target = _ORIG_TO_MY_PLAYER[orig_action["target"]]
        value = orig_action["value"]
        touched = _touched_orders(g.state.variant, g.state.hands[target], 0, value)
        g = g.handle_action(ClueAction(pi, target, touched, BaseClue(ClueKind.COLOUR, value)))
    elif a_type == 3:  # rank clue
        target = _ORIG_TO_MY_PLAYER[orig_action["target"]]
        value = orig_action["value"]
        touched = _touched_orders(g.state.variant, g.state.hands[target], 1, value)
        g = g.handle_action(ClueAction(pi, target, touched, BaseClue(ClueKind.RANK, value)))
    else:
        raise ValueError(f"Unknown action type: {a_type}")

    g = g.handle_action(TurnAction(g.state.turn_count, g.state.next_player_index(pi)))
    return g.copy_with(catchup=False)


def test_replay_1874799_turn_23_stall_over_play() -> None:
    """At turn 23 the bot must stall (clue), not play its null-5."""
    # Initial deal in remapped player order:
    # - Alice (my P0 = bot69, observer)          ← orig deck[5..9]   = null-5, r3, b1, b3, b1
    # - Bob   (my P1 = yagami)                    ← orig deck[10..14] = r2, r5, null-2, r1, null-1
    # - Cathy (my P2 = bot67)                     ← orig deck[0..4]   = null-4, b3, r4, null-3, r4
    # Hands are listed slot-1-first (newest first); conftest reverses them when issuing draws.
    g = setup(
        Reactor.create,
        hands=[
            # Alice = bot69 — hidden (the observer's hand).
            ["xx", "xx", "xx", "xx", "xx"],
            # Bob = yagami — orig orders [14,13,12,11,10] = (null-1, r1, null-2, r5, r2)
            ["u1", "r1", "u2", "r5", "r2"],
            # Cathy = bot67 — orig orders [4,3,2,1,0] = (r4, null-3, r4, b3, null-4)
            ["r4", "u3", "r4", "b3", "u4"],
        ],
        variant="Pink-Ones & Null (3 Suits)",
        starting=Player.CATHY,  # bot67 (my P2) goes first, matching original turn 1
    )

    for action in _ORIG_ACTIONS:
        g = _apply_orig_action(g, action)

    # Should now be at start of turn 23 — Alice's (= bot69's) turn.
    assert g.state.current_player_index == Player.ALICE.value, (
        f"expected Alice (bot69) to act; got player {g.state.current_player_index}"
    )
    assert g.state.score == 12, f"expected score=12, got {g.state.score}"
    assert g.state.cards_left == 1, f"expected cards_left=1, got {g.state.cards_left}"
    assert g.state.play_stacks == (4, 4, 4), (
        f"expected play_stacks=(4,4,4), got {g.state.play_stacks}"
    )

    # Sanity: bot69's null-5 is at my-order 0 (the oldest dealt to Alice), and the
    # convention should have inferred it as null-5 + marked CALLED_TO_PLAY via yagami's
    # turn-21 rank-5 clue + elimination.
    null_5_my_order = _ORIG_TO_MY_ORDER[5]  # original order 5 = null-5
    assert null_5_my_order == 0
    from hanabi_bot.basics.card import CardStatus
    from hanabi_bot.basics.identity import Identity
    assert g.players[0].thoughts[null_5_my_order].id(infer=True) == Identity(2, 5), (
        "bot69 should have inferred slot 5 = null-5 after yagami's rank-5 clue"
    )
    assert g.meta[null_5_my_order].status == CardStatus.CALLED_TO_PLAY

    perform = g.take_action()

    # Pre-fix bug: bot played null-5 from slot 5 — that line ends at score 14.
    assert not (isinstance(perform, PerformPlay) and perform.target == null_5_my_order), (
        f"bot picked the buggy line (play null-5 at order {null_5_my_order}); "
        f"expected a stall clue. perform={perform!r}"
    )
    # Winning line: stall via any clue. Two tokens available so a discard is unnecessary.
    assert isinstance(perform, (PerformColour, PerformRank)), (
        f"expected a stall clue, got {perform!r}"
    )
    assert not isinstance(perform, PerformDiscard)
