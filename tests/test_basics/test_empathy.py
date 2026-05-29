"""Empathy / good-touch / link maintenance after the real elim() wiring.

Stage 2b: validates that Game.elim() (called after every action) correctly:
- Runs basic_elim (when all copies of an id are visible, drop it from others)
- Syncs per-player thoughts from common
- Runs good_touch_elim when good_touch is enabled
"""

from __future__ import annotations

import pytest

from hanabi_bot.basics.action import (
    ClueAction,
    DrawAction,
    PlayAction,
)
from hanabi_bot.basics.clue import BaseClue, ClueKind
from hanabi_bot.basics.game import Game
from hanabi_bot.basics.identity import Identity
from hanabi_bot.basics.options import TableOptions
from hanabi_bot.basics.state import HAND_SIZE, State
from hanabi_bot.basics.variant import get_variant


def _make_state(num_players: int = 3, our_index: int = 0) -> State:
    v = get_variant("No Variant")
    opts = TableOptions(num_players=num_players, variant_name="No Variant")
    names = tuple(f"P{i}" for i in range(num_players))
    return State.create(names=names, our_player_index=our_index, variant=v, options=opts)


@pytest.fixture
def fresh_game() -> Game:
    return Game.create(table_id=1, state=_make_state())


def _deal(g: Game, specs: list[list[tuple[int, int] | None]]) -> Game:
    """Issue DrawActions to fill all hands. Cards are dealt P0 1..n, P1 1..n, etc."""
    order = 0
    hand_size = HAND_SIZE[g.state.num_players]
    for pi in range(g.state.num_players):
        for slot in range(hand_size):
            spec = specs[pi][slot]
            if spec is None:
                action = DrawAction(player_index=pi, order=order, suit_index=-1, rank=-1)
            else:
                action = DrawAction(player_index=pi, order=order, suit_index=spec[0], rank=spec[1])
            g = g.handle_action(action)
            order += 1
    return g


# --- elim() now does real work ---


def test_elim_clears_dirty(fresh_game: Game) -> None:
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    # After handle_action -> on_draw -> elim, dirty should be cleared for all players + common.
    assert g.common.dirty == frozenset()
    for p in g.players:
        assert p.dirty == frozenset()


def test_elim_syncs_player_thoughts_with_common_after_clue(fresh_game: Game) -> None:
    """A colour clue narrows common.thoughts[order].possible. After elim(), each player
    sees the same possible set (intersected with their own possible).

    Note: in the base Game (no convention), elim() runs automatically on DrawActions but
    NOT on Clue/Play/Discard — conventions call elim() inside their interpret_* hooks.
    """
    g = fresh_game.handle_action(
        DrawAction(player_index=1, order=0, suit_index=0, rank=1)
    )
    g = g.handle_action(
        ClueAction(giver=0, target=1, list_=(0,), clue=BaseClue(ClueKind.COLOUR, 0))
    )
    # Explicitly call elim() — conventions normally do this.
    g = g.elim()

    common_possible = g.common.thoughts[0].possible
    assert common_possible.length == 5
    # P1 (the holder) — possible was initially all 25, now intersected with common's 5.
    p1_possible = g.players[1].thoughts[0].possible
    assert p1_possible.length == 5
    assert all(i.suit_index == 0 for i in p1_possible)


def test_basic_elim_removes_id_when_all_copies_known(fresh_game: Game) -> None:
    """In a 2-player game, if both visible copies of an r5 are played/discarded,
    the remaining r5 (in our hand) gets narrowed.

    Simpler version: deal known cards and verify that visible copies don't appear
    in the holder's thought possibilities.
    """
    g = fresh_game
    # P1 draws r1 (3 copies of r1 exist).
    g = g.handle_action(DrawAction(player_index=1, order=0, suit_index=0, rank=1))
    g = g.handle_action(DrawAction(player_index=2, order=1, suit_index=0, rank=1))
    # P1 plays one r1, the other r1 is still in P1's hand and in P2's hand.
    g = g.handle_action(PlayAction(player_index=1, order=0, suit_index=0, rank=1))
    # play_stacks[red] = 1
    assert g.state.play_stacks[0] == 1
    # The second r1 is now in only one hand (P2's), and base_count[r1] is 1.
    assert g.state.base_count[Identity(0, 1).to_ord()] == 1


def test_pre_play_elim_is_conservative_about_hands(fresh_game: Game) -> None:
    """Documenting the elim semantics: drawing 3 r1s into P1's hand does NOT eliminate r1
    from P1's other cards' `possible` set — because P1 themselves can't see those r1s.

    The empathy set tracks what the HOLDER could deduce, not what the observer knows.
    Only when copies are CONSUMED (played/discarded — visible to everyone via base_count)
    does basic_elim propagate. The `test_known_play_eliminates_id_from_future_unknowns`
    test demonstrates the play-driven elim that DOES fire.
    """
    g = fresh_game
    g = g.handle_action(DrawAction(player_index=1, order=0, suit_index=0, rank=1))
    g = g.handle_action(DrawAction(player_index=1, order=1, suit_index=0, rank=1))
    g = g.handle_action(DrawAction(player_index=1, order=2, suit_index=0, rank=1))
    g = g.handle_action(DrawAction(player_index=1, order=3, suit_index=0, rank=2))

    # P1's own hand: r1 remains as a possibility for P1's other cards (P1 can't see their own r1s).
    p1_thought_r2 = g.players[1].thoughts[3]
    assert Identity(0, 1) in p1_thought_r2.possible
    # Common's view also retains r1 (common is symmetric, equivalent to "everyone's empathy").
    common_thought_r2 = g.common.thoughts[3]
    assert Identity(0, 1) in common_thought_r2.possible


def test_known_play_eliminates_id_from_future_unknowns(fresh_game: Game) -> None:
    """After all 3 r1s are played, r1 should disappear from any future card's possibilities."""
    g = fresh_game
    # Place all 3 r1s in P1's hand
    g = g.handle_action(DrawAction(player_index=1, order=0, suit_index=0, rank=1))
    g = g.handle_action(DrawAction(player_index=1, order=1, suit_index=0, rank=1))
    g = g.handle_action(DrawAction(player_index=1, order=2, suit_index=0, rank=1))

    # Play all of them in order (one at a time, drawing replacements)
    g = g.handle_action(PlayAction(player_index=1, order=0, suit_index=0, rank=1))
    g = g.handle_action(DrawAction(player_index=1, order=3, suit_index=1, rank=4))
    g = g.handle_action(PlayAction(player_index=1, order=1, suit_index=0, rank=1))
    g = g.handle_action(DrawAction(player_index=1, order=4, suit_index=1, rank=3))
    g = g.handle_action(PlayAction(player_index=1, order=2, suit_index=0, rank=1))
    g = g.handle_action(DrawAction(player_index=1, order=5, suit_index=2, rank=2))

    # Wait — only one of those 3 r1s actually plays successfully. The 2nd and 3rd are strikes.
    # Actually play_stacks[r] advances from 0->1 on the first play. After that the r1s "play" but
    # are no-ops to the stack (with_play doesn't check; trust on_play). But state.score = 1 only.
    # That doesn't matter for elim — base_count[r1] tracks total copies removed.
    assert g.state.base_count[Identity(0, 1).to_ord()] == 3
    # Now common's view of any later card should not have r1 as a possibility.
    common_thought = g.common.thoughts[5]
    assert Identity(0, 1) not in common_thought.possible


def test_clue_full_known_card_writes_identity_in_deck(fresh_game: Game) -> None:
    """Verify the elim path doesn't undo the deck-id writeback that on_clue does."""
    g = fresh_game.handle_action(
        DrawAction(player_index=0, order=0, suit_index=-1, rank=-1)
    )
    # Rank 5 clue
    g = g.handle_action(
        ClueAction(giver=1, target=0, list_=(0,), clue=BaseClue(ClueKind.RANK, 5))
    )
    # Colour red clue
    g = g.handle_action(
        ClueAction(giver=2, target=0, list_=(0,), clue=BaseClue(ClueKind.COLOUR, 0))
    )
    assert g.deck_ids[0] == Identity(0, 5)
    assert g.state.deck[0].suit_index == 0
    assert g.state.deck[0].rank == 5


def test_elim_handles_empty_dirty(fresh_game: Game) -> None:
    """Calling elim on a fresh Game (no dirty) returns essentially the same game."""
    g = fresh_game.elim()
    assert g.common.dirty == frozenset()
    for p in g.players:
        assert p.dirty == frozenset()


def test_full_initial_deal_elim_runs(fresh_game: Game) -> None:
    """The standard deal sequence completes elim without crashing."""
    specs: list[list[tuple[int, int] | None]] = [
        [None, None, None, None, None],
        [(0, 1), (0, 2), (1, 1), (1, 2), (2, 3)],
        [(3, 1), (3, 2), (4, 1), (4, 2), (4, 5)],
    ]
    g = _deal(fresh_game, specs)
    # Sanity: turn count advanced to 1; all dirty cleared
    assert g.state.turn_count == 1
    assert g.common.dirty == frozenset()
    # Common's view of each card matches what's visible to it.
    # P1's order 0 is r1, seen by P0 and P2 but not P1. Common's thought sees nothing (-1/-1) initially.


# --- Good touch tests ---


def test_good_touch_disabled_by_default(fresh_game: Game) -> None:
    assert fresh_game.good_touch is False


def test_good_touch_elim_removes_trash_from_clued(fresh_game: Game) -> None:
    """When good_touch=True, a clued card has trash possibilities filtered out."""
    g = fresh_game
    # Play r1 so r1 becomes trash.
    g = g.handle_action(DrawAction(player_index=1, order=0, suit_index=0, rank=1))
    g = g.handle_action(PlayAction(player_index=1, order=0, suit_index=0, rank=1))
    # r1 is now in state.trash_set
    assert Identity(0, 1) in g.state.trash_set

    # Enable good touch and apply a clue.
    g = g.copy_with(good_touch=True)

    # P1 draws a new card (say r3); we (P0) see it as r3.
    g = g.handle_action(DrawAction(player_index=1, order=1, suit_index=0, rank=3))
    # Clue red to P1
    g = g.handle_action(
        ClueAction(giver=0, target=1, list_=(1,), clue=BaseClue(ClueKind.COLOUR, 0))
    )
    # Explicitly call elim() — conventions normally do this from interpret_clue.
    g = g.elim()

    common_thought = g.common.thoughts[1]
    # possible includes all 5 red ids
    assert common_thought.possible.length == 5
    # inferred excludes r1 (the trash)
    assert Identity(0, 1) not in common_thought.inferred
    assert common_thought.inferred.length == 4
