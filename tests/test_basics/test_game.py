"""Game action handlers and handle_action dispatch.

Tests verify the state-machine layer only (no convention interpretation, no elim).
elim() is a stub in Stage 2a; the real empathy/good-touch elim is verified in 2b.
"""

from __future__ import annotations

import dataclasses

import pytest

from hanabi_bot.basics.action import (
    ClueAction,
    DiscardAction,
    DrawAction,
    GameOverAction,
    PlayAction,
    TurnAction,
)
from hanabi_bot.basics.card import CardStatus
from hanabi_bot.basics.clue import BaseClue, ClueKind
from hanabi_bot.basics.game import Game, _add_action
from hanabi_bot.basics.identity import Identity
from hanabi_bot.basics.options import TableOptions
from hanabi_bot.basics.state import HAND_SIZE, State
from hanabi_bot.basics.variant import get_variant

# --- Fixtures ---


def _make_state(num_players: int = 3, our_index: int = 0) -> State:
    v = get_variant("No Variant")
    opts = TableOptions(num_players=num_players, variant_name="No Variant")
    names = tuple(f"P{i}" for i in range(num_players))
    return State.create(names=names, our_player_index=our_index, variant=v, options=opts)


@pytest.fixture
def fresh_game() -> Game:
    return Game.create(table_id=1, state=_make_state())


def _deal_initial(game: Game, hand_specs: list[list[tuple[int, int] | None]]) -> Game:
    """Issue DrawActions to fill all hands.

    hand_specs[player_index] is the list of (suit_index, rank) tuples for that player's
    cards in deal order; use None for the bot's own (hidden) cards.

    Each player gets HAND_SIZE[num_players] cards. Cards are dealt one per player in turn,
    matching hanab.live's deal order: P0 gets first 5, P1 next 5, P2 last 5.
    """
    g = game
    num_players = game.state.num_players
    hand_size = HAND_SIZE[num_players]
    order = 0
    for player_index in range(num_players):
        for slot in range(hand_size):
            spec = hand_specs[player_index][slot]
            if spec is None:
                action = DrawAction(player_index=player_index, order=order, suit_index=-1, rank=-1)
            else:
                action = DrawAction(player_index=player_index, order=order, suit_index=spec[0], rank=spec[1])
            g = g.handle_action(action)
            order += 1
    return g


# --- _add_action helper ---


def test_add_action_new_turn() -> None:
    al: tuple[tuple, ...] = ()
    new = _add_action(al, TurnAction(1, 0), turn=0)
    assert new == ((TurnAction(1, 0),),)


def test_add_action_appends_to_existing_turn() -> None:
    from hanabi_bot.basics.action import StatusAction

    al = ((TurnAction(1, 0),),)
    new = _add_action(al, StatusAction(5, 0, 25), turn=0)
    assert len(new[0]) == 2


def test_add_action_dedupes() -> None:
    al = ((TurnAction(1, 0),),)
    new = _add_action(al, TurnAction(1, 0), turn=0)
    assert new == al  # unchanged


def test_add_action_out_of_range_raises() -> None:
    with pytest.raises(IndexError):
        _add_action((), TurnAction(1, 0), turn=5)


# --- Game.create ---


def test_game_create_initial_state(fresh_game: Game) -> None:
    g = fresh_game
    assert g.table_id == 1
    assert g.state.turn_count == 0
    assert len(g.players) == 3
    assert g.common.player_index == -1
    assert g.common.is_common is True
    assert all(not p.is_common for p in g.players)
    assert g.meta == ()
    assert g.deck_ids == ()
    assert g.in_progress is True


def test_game_create_players_have_correct_names(fresh_game: Game) -> None:
    g = fresh_game
    for i, p in enumerate(g.players):
        assert p.name == f"P{i}"
        assert p.player_index == i


def test_game_create_last_actions_empty(fresh_game: Game) -> None:
    assert fresh_game.last_actions == (None, None, None)


# --- on_draw / DrawAction handling ---


def test_draw_appends_to_deck_and_hand(fresh_game: Game) -> None:
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    assert len(g.state.deck) == 1
    assert g.state.deck[0].suit_index == 0
    assert g.state.deck[0].rank == 1
    assert g.state.hands[1] == (0,)
    assert g.state.holders == (1,)
    assert g.state.next_card_order == 1
    assert g.state.cards_left == 49


def test_draw_self_hides_identity(fresh_game: Game) -> None:
    """When the bot (player 0) draws, the server sends -1/-1; our deck reflects that."""
    g = fresh_game.handle_action(
        DrawAction(player_index=0, order=0, suit_index=-1, rank=-1)
    )
    assert g.state.deck[0].suit_index == -1
    assert g.state.deck[0].rank == -1
    # But our own Thought also has -1/-1 (we can't see our card)
    assert g.players[0].thoughts[0].suit_index == -1
    # Other players see... -1/-1 too in this case, since the actual id is unknown.
    # The Thought's suit/rank is set from what THEY would observe (which is -1 since the server didn't send it).


def test_draw_other_visible_identity(fresh_game: Game) -> None:
    """When P1 draws, P0 (us) and P2 see it but P1 doesn't."""
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=2, rank=3)
    )
    # We observe player 1's card; our Thought has visible identity
    assert g.players[0].thoughts[0].suit_index == 2
    assert g.players[0].thoughts[0].rank == 3
    # P2 also observes it
    assert g.players[2].thoughts[0].suit_index == 2
    # P1 themselves cannot see it (suit/rank = -1 from their POV)
    assert g.players[1].thoughts[0].suit_index == -1
    assert g.players[1].thoughts[0].rank == -1
    # Common perspective: nobody-knows = -1/-1
    assert g.common.thoughts[0].suit_index == -1
    assert g.common.thoughts[0].rank == -1


def test_draw_creates_thought_and_meta(fresh_game: Game) -> None:
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    assert len(g.meta) == 1
    assert g.meta[0].order == 0
    assert g.meta[0].status == CardStatus.NONE
    # Each player has one thought now
    assert len(g.common.thoughts) == 1
    for p in g.players:
        assert len(p.thoughts) == 1


def test_draw_full_hand_raises(fresh_game: Game) -> None:
    g = fresh_game
    # Fill P1's hand (5 cards in 3-player game)
    for slot in range(5):
        g = g.handle_action(
            DrawAction(player_index=1, order=slot, suit_index=0, rank=1)
        )
    # A 6th draw to P1 should raise
    with pytest.raises(RuntimeError):
        g.handle_action(DrawAction(player_index=1, order=5, suit_index=0, rank=2))


def test_initial_deal_advances_turn(fresh_game: Game) -> None:
    """After every hand is full, turn_count advances from 0 to 1."""
    specs: list[list[tuple[int, int] | None]] = [
        [None] * 5,  # P0 (us): hidden
        [(0, 1), (0, 1), (0, 2), (1, 1), (1, 2)],  # P1
        [(2, 1), (2, 2), (3, 1), (3, 2), (4, 5)],  # P2
    ]
    g = _deal_initial(fresh_game, specs)
    assert g.state.turn_count == 1
    assert all(len(h) == 5 for h in g.state.hands)


# --- on_clue / ClueAction handling ---


def test_clue_marks_touched_cards_clued(fresh_game: Game) -> None:
    # P1 draws a red 1 at order 0
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    # ... fill the rest of the hands (cheap version: just enough to send a clue)
    # Actually we can clue without full hands; on_clue iterates state.hands[target].
    action = ClueAction(
        giver=0, target=1, list_=(0,), clue=BaseClue(ClueKind.COLOUR, 0)
    )
    g2 = g.handle_action(action)
    assert g2.state.deck[0].clued is True
    assert len(g2.state.deck[0].clues) == 1
    assert g2.state.deck[0].clues[0].kind == ClueKind.COLOUR
    assert g2.state.deck[0].clues[0].value == 0
    assert g2.state.deck[0].clues[0].giver == 0


def test_clue_decrements_clue_tokens(fresh_game: Game) -> None:
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    action = ClueAction(
        giver=0, target=1, list_=(0,), clue=BaseClue(ClueKind.COLOUR, 0)
    )
    g2 = g.handle_action(action)
    assert g2.state.clue_tokens == 7


def test_clue_intersects_possible_for_touched_card(fresh_game: Game) -> None:
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    # P1 can see their own card as -1/-1, so their thought starts as all 25 possibilities
    assert g.players[1].thoughts[0].possible.length == 25

    action = ClueAction(
        giver=0, target=1, list_=(0,), clue=BaseClue(ClueKind.COLOUR, 0)  # red
    )
    g2 = g.handle_action(action)
    # The common perspective shrinks possible to red identities (5 of them)
    common_possible = g2.common.thoughts[0].possible
    assert common_possible.length == 5
    assert all(i.suit_index == 0 for i in common_possible)


def test_clue_differences_possible_for_untouched_card(fresh_game: Game) -> None:
    # P1 draws TWO cards: order 0 (red 1) and order 1 (blue 2)
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    g = g.handle_action(
        DrawAction(player_index=1, order=1, suit_index=3, rank=2)
    )
    # P0 clues red to P1, only touching order 0
    action = ClueAction(
        giver=0, target=1, list_=(0,), clue=BaseClue(ClueKind.COLOUR, 0)
    )
    g2 = g.handle_action(action)
    # Order 1 (untouched) has its possible NARROWED by removing red ids
    untouched_common = g2.common.thoughts[1].possible
    assert untouched_common.length == 20  # 25 - 5 red ids
    assert not any(i.suit_index == 0 for i in untouched_common)


def test_clue_fully_known_card_writes_identity_to_deck(fresh_game: Game) -> None:
    """If a clue narrows possible to length 1, the deck card's identity is filled in."""
    # P1 (P0 = us) drew their own card with -1/-1 — so our deck shows it as unknown.
    g = fresh_game.handle_action(
        DrawAction(player_index=0, order=0, suit_index=-1, rank=-1)
    )
    assert g.state.deck[0].id() is None

    # Hypothetical: imagine receiving enough clues to narrow possible to one identity.
    # Easier path: directly clue with rank 5, then colour red, etc. Use a small variant.
    # Simpler test: clue red 1-only to a "Critical Fives" or do two clues.
    # Here we just clue rank=5 first, then colour red. The intersection narrows to (red, 5).
    g2 = g.handle_action(
        ClueAction(giver=1, target=0, list_=(0,), clue=BaseClue(ClueKind.RANK, 5))
    )
    g3 = g2.handle_action(
        ClueAction(giver=2, target=0, list_=(0,), clue=BaseClue(ClueKind.COLOUR, 0))
    )
    # Now order 0 must be red 5
    assert g3.state.deck[0].suit_index == 0
    assert g3.state.deck[0].rank == 5
    assert g3.deck_ids[0] == Identity(0, 5)


def test_clue_records_last_actions(fresh_game: Game) -> None:
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    action = ClueAction(giver=0, target=1, list_=(0,), clue=BaseClue(ClueKind.COLOUR, 0))
    g2 = g.handle_action(action)
    assert g2.last_actions[0] == action


# --- on_play / PlayAction handling ---


def test_play_advances_play_stack(fresh_game: Game) -> None:
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    action = PlayAction(player_index=1, order=0, suit_index=0, rank=1)
    g2 = g.handle_action(action)
    assert g2.state.play_stacks[0] == 1
    assert g2.state.score == 1


def test_play_removes_from_hand(fresh_game: Game) -> None:
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    assert 0 in g.state.hands[1]
    action = PlayAction(player_index=1, order=0, suit_index=0, rank=1)
    g2 = g.handle_action(action)
    assert 0 not in g2.state.hands[1]


def test_play_resolves_thought_identity(fresh_game: Game) -> None:
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    action = PlayAction(player_index=1, order=0, suit_index=0, rank=1)
    g2 = g.handle_action(action)
    # Common's thought now has possible = single(r1)
    assert g2.common.thoughts[0].possible.is_exactly(Identity(0, 1))
    assert g2.common.thoughts[0].inferred.is_exactly(Identity(0, 1))


def test_play_records_last_actions(fresh_game: Game) -> None:
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    action = PlayAction(player_index=1, order=0, suit_index=0, rank=1)
    g2 = g.handle_action(action)
    assert g2.last_actions[1] == action


# --- on_discard / DiscardAction handling ---


def test_discard_adds_to_pile(fresh_game: Game) -> None:
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    action = DiscardAction(player_index=1, order=0, suit_index=0, rank=1, failed=False)
    g2 = g.handle_action(action)
    assert g2.state.discard_stacks[0][0] == (0,)


def test_discard_regains_clue(fresh_game: Game) -> None:
    import dataclasses
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    # Burn some clue tokens so discard can actually regain one
    g = g.copy_with(state=dataclasses.replace(g.state, clue_tokens=3))
    action = DiscardAction(player_index=1, order=0, suit_index=0, rank=1, failed=False)
    g2 = g.handle_action(action)
    assert g2.state.clue_tokens == 4


def test_failed_discard_strikes_no_clue_regained(fresh_game: Game) -> None:
    import dataclasses
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    g = g.copy_with(state=dataclasses.replace(g.state, clue_tokens=3))
    action = DiscardAction(player_index=1, order=0, suit_index=0, rank=1, failed=True)
    g2 = g.handle_action(action)
    assert g2.state.strikes == 1
    assert g2.state.clue_tokens == 3  # no regain


def test_discard_removes_from_hand(fresh_game: Game) -> None:
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    action = DiscardAction(player_index=1, order=0, suit_index=0, rank=1, failed=False)
    g2 = g.handle_action(action)
    assert 0 not in g2.state.hands[1]


# --- TurnAction ---


def test_turn_action_updates_current_player(fresh_game: Game) -> None:
    g = fresh_game.handle_action(TurnAction(num=1, current_player_index=1))
    assert g.state.current_player_index == 1
    assert g.state.turn_count == 2  # turn_count = num + 1


# --- GameOverAction ---


def test_game_over_sets_in_progress_false(fresh_game: Game) -> None:
    g = fresh_game.handle_action(GameOverAction(end_condition=1, player_index=0))
    assert g.in_progress is False


# --- action_list recording ---


def test_action_list_records_each_action(fresh_game: Game) -> None:
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    g = g.handle_action(TurnAction(num=1, current_player_index=0))
    # turn_count is now 2; action_list should have records on turn 0 and turn 1
    al = g.state.action_list
    # Turn 0 had the draw (and any post-draw bumps). Turn 1 had the TurnAction.
    assert len(al) >= 1
    drawn_orders = [a.order for a in al[0] if isinstance(a, DrawAction)]
    assert 0 in drawn_orders


# --- Game.copy_with preserves subclass ---


def test_copy_with_preserves_type(fresh_game: Game) -> None:
    g2 = fresh_game.copy_with(table_id=42)
    assert type(g2) is Game
    assert g2.table_id == 42


# --- is_touched / is_blind_playing / is_saved ---


def test_is_touched_clued(fresh_game: Game) -> None:
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    assert not g.is_touched(0)
    # Apply a clue
    g2 = g.handle_action(
        ClueAction(giver=0, target=1, list_=(0,), clue=BaseClue(ClueKind.COLOUR, 0))
    )
    assert g2.is_touched(0)


def test_is_blind_playing_requires_unclued_and_status() -> None:
    """Blind play status reads: not clued AND (CalledToPlay OR Finessed OR bluffed)."""
    state = _make_state()
    g = Game.create(table_id=1, state=state)
    g = g.handle_action(DrawAction(player_index=1, order=0, suit_index=0, rank=1))
    # Initially unclued and status=NONE -> not blind playing
    assert not g.is_blind_playing(0)
    # Mark as CalledToPlay via with_meta
    g2 = g.with_meta(0, lambda m: dataclasses.replace(m, status=CardStatus.CALLED_TO_PLAY))
    assert g2.is_blind_playing(0)
    # Once clued, not blind playing anymore
    g3 = g2.handle_action(
        ClueAction(giver=0, target=1, list_=(0,), clue=BaseClue(ClueKind.COLOUR, 0))
    )
    assert not g3.is_blind_playing(0)
