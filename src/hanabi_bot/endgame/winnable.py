"""Pure-tree-search winnability checks.

Port of scala-bot/src/scala_bot/endgame/winnable.scala, with the leaf check
upgraded to consult per-player knowledge instead of ground-truth deck identities.

`clueless_winnable` keeps its omniscient `State`-based form (its job is "can the
team in theory still win if everyone identified every card"). The
`winnable_simpler` / `winnable_if` pair takes `Game` so each player's plays and
discards are chosen from their own knowledge (`Player.thinks_playables`,
`Thought.id(infer=True)`), and clue actions are advanced through
`Game.simulate_action` so the convention's CALLED_TO_PLAY markings actually
propagate.
"""

from __future__ import annotations

import dataclasses
import enum
import time
from typing import TYPE_CHECKING

from hanabi_bot.basics.action import (
    PerformAction,
    PerformColour,
    PerformDiscard,
    PerformPlay,
    PerformRank,
)
from hanabi_bot.basics.card import Card
from hanabi_bot.basics.identity import Identity

from .helper import RemainingMap, remaining_remove, unwinnable_state

if TYPE_CHECKING:
    from hanabi_bot.basics.game import Game
    from hanabi_bot.basics.state import State


class SimpleResult(enum.Enum):
    ALWAYS_WINNABLE = "AlwaysWinnable"
    UNWINNABLE = "Unwinnable"


@dataclasses.dataclass(frozen=True, slots=True)
class WinnableWithDraws:
    draws: list[Identity]


SimpleResultT = SimpleResult | WinnableWithDraws


def _past_deadline(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() > deadline


def _is_dummy_clue(perform: PerformAction) -> bool:
    return isinstance(perform, PerformRank) and perform.target == 0 and perform.value == 0


def advance_state(
    state: State, perform: PerformAction, player_index: int, draw: Card | None
) -> State:
    """Advance State by one PerformAction. Used by `clueless_winnable` (omniscient path).

    Port of advanceState in winnable.scala. Lower-level than `Game.simulate_action`:
    skips elim, convention interpretation, and per-player Thought updates. Only
    deck/hands/cards_left/endgame_turns/play_stacks/discard_stacks/strikes/clue_tokens
    are touched.
    """
    def _remove_and_draw(s: State, order: int) -> State:
        new_order = s.next_card_order
        new_hand = (new_order, *(o for o in s.hands[player_index] if o != order))
        new_hands = (*s.hands[:player_index], new_hand, *s.hands[player_index + 1:])
        new_cards_left = max(0, s.cards_left - 1)
        new_next_order = new_order + (1 if s.cards_left > 0 else 0)
        if s.endgame_turns is not None:
            new_endgame_turns: int | None = s.endgame_turns - 1
        elif s.cards_left == 1:
            new_endgame_turns = s.num_players
        else:
            new_endgame_turns = None
        s2 = dataclasses.replace(
            s,
            hands=new_hands,
            next_card_order=new_next_order,
            cards_left=new_cards_left,
            endgame_turns=new_endgame_turns,
        )
        deck = s2.deck
        if new_order < len(deck) and deck[new_order].id() is not None:
            return s2
        new_card = draw if draw is not None else Card(
            suit_index=-1, rank=-1, order=new_order, turn_drawn=s2.turn_count
        )
        if len(deck) == new_order:
            return dataclasses.replace(s2, deck=(*deck, new_card))
        new_deck = (*deck[:new_order], new_card, *deck[new_order + 1:])
        return dataclasses.replace(s2, deck=new_deck)

    if isinstance(perform, PerformPlay):
        target = perform.target
        played_id = state.deck[target].id()
        if played_id is None:
            after = dataclasses.replace(state, strikes=state.strikes + 1)
        elif state.is_playable(played_id):
            after = state.with_play(played_id)
        else:
            after = dataclasses.replace(state, strikes=state.strikes + 1).with_discard(played_id, target)
        return _remove_and_draw(after, target)
    if isinstance(perform, PerformDiscard):
        target = perform.target
        d_id = state.deck[target].id()
        after = state.with_discard(d_id, target) if d_id is not None else state
        after = after.regain_clue()
        return _remove_and_draw(after, target)
    # Clue: spend a token, decrement endgame timer.
    new_endgame = state.endgame_turns - 1 if state.endgame_turns is not None else None
    return dataclasses.replace(
        state, clue_tokens=state.clue_tokens - 1, endgame_turns=new_endgame
    )


def advance_game(
    game: Game, perform: PerformAction, player_index: int, draw: Identity | None
) -> Game:
    """Advance Game by one PerformAction, keeping per-player knowledge in sync.

    Real plays / discards / clues run through `Game.simulate_action` so the
    convention pipeline executes. The dummy stall clue `PerformRank(0, 0)` is a
    sentinel that doesn't correspond to any real clue — handle it directly as
    "spend a token, decrement endgame_turns" without invoking the convention.
    """
    if _is_dummy_clue(perform):
        return game.copy_with(state=advance_state(game.state, perform, player_index, draw=None))

    # Local import avoids a top-level cycle (solver -> winnable -> solver).
    from .solver import EndgameSolver

    action = EndgameSolver._perform_to_action(perform, game, player_index)
    return game.simulate_action(action, draw=draw)


def clueless_winnable(
    state: State, player_turn: int, deadline: float | None, depth: int
) -> PerformAction | None:
    """Recursively check if the position is winnable by everyone playing what they know.

    Operates on omniscient `State` (the caller fills in every card identity from common
    knowledge before invoking). No convention interpretation; just plays + a single
    default clue + the first discardable card. Returns the first winning action found,
    or None.
    """
    if state.score == state.max_score:
        return PerformPlay(99)
    if _past_deadline(deadline):
        return None
    if unwinnable_state(state, player_turn, depth):
        return None

    def action_winnable(perform: PerformAction) -> bool:
        new_state = advance_state(state, perform, player_turn, draw=None)
        return clueless_winnable(new_state, state.next_player_index(player_turn), deadline, depth + 1) is not None

    for order in state.hands[player_turn]:
        id_ = state.deck[order].id()
        if id_ is not None and state.is_playable(id_):
            perform = PerformPlay(order)
            if action_winnable(perform):
                return perform

    if state.can_clue:
        default_clue = PerformRank(0, 0)
        if action_winnable(default_clue):
            return default_clue

    for order in state.hands[player_turn]:
        if state.deck[order].id() is None:
            perform = PerformDiscard(order)
            if action_winnable(perform):
                return perform

    return None


def _player_known_plays(game: Game, player_turn: int) -> list[int]:
    """Orders the player knows are playable, in newest-slot-first order
    (mirroring winnable_simpler's reversed-hand iteration).
    """
    state = game.state
    plays: list[int] = []
    seen: set[int] = set()
    # Convention-aware (called-to-play, finesses, prompts).
    for o in game.players[player_turn].thinks_playables(game, player_turn, exclude_trash=True):
        if o not in seen:
            plays.append(o)
            seen.add(o)
    # Also include cards whose per-player inferred is a single playable identity
    # (covers fully-known own cards that thinks_playables might skip).
    for o in reversed(state.hands[player_turn]):
        if o in seen:
            continue
        inferred = game.players[player_turn].thoughts[o].id(infer=True)
        if inferred is not None and state.is_playable(inferred):
            plays.append(o)
            seen.add(o)
    # Reactive WaitingConnection handshake: when the convention has just queued a
    # reactive play+play (rank clue), the reacter is expected to blind-play their
    # newest unclued card so the receiver's target_slot resolves via calc_slot.
    # The per-player `thinks_playables` won't include this (no called-to-play),
    # so offer it explicitly. We only do this for rank-clue WCs (play+play
    # semantics) — color-clue reactives are dc+play, not play+play.
    wc = getattr(game, "waiting", None)
    if (
        wc is not None
        and wc.reacter == player_turn
        and not wc.inverted
        and wc.clue.kind.value == 1  # ClueKind.RANK
    ):
        for o in state.hands[player_turn]:  # slot-1-first iteration (newest first)
            if o in seen:
                continue
            if state.deck[o].clued:
                continue  # already touched by a clue — not a blind candidate
            plays.append(o)
            seen.add(o)
            break  # only the newest unclued
    return plays


def winnable_simpler(
    game: Game,
    player_turn: int,
    remaining: RemainingMap,
    deadline: float | None,
    depth: int,
) -> bool:
    """Simpler recursive winnability over draw possibilities. Returns True if any
    convention-feasible action sequence reaches max score.
    """
    state = game.state
    if state.score == state.max_score:
        return True
    if unwinnable_state(state, player_turn, depth):
        return False

    plays: list[PerformAction] = []
    discards: list[PerformAction] = []
    found_dc = False

    if state.can_clue:
        discards.append(PerformRank(0, 0))
        found_dc = True

    # Plays: only what the player believes is playable.
    for order in _player_known_plays(game, player_turn):
        plays.insert(0, PerformPlay(order))

    # Discards: known trash from this player's perspective, else first unknown card.
    for order in reversed(state.hands[player_turn]):
        if found_dc:
            break
        inferred = game.players[player_turn].thoughts[order].id(infer=True)
        if inferred is None or state.is_basic_trash(inferred):
            discards.insert(0, PerformDiscard(order))
            found_dc = True

    actions = plays + discards
    for action in actions:
        res = winnable_if(game, player_turn, action, remaining, deadline, depth)
        if res != SimpleResult.UNWINNABLE:
            return True
    return False


def winnable_if(
    game: Game,
    player_turn: int,
    perform: PerformAction,
    remaining: RemainingMap,
    deadline: float | None,
    depth: int,
) -> SimpleResultT:
    """Is the position winnable if we take this action?

    Returns SimpleResult.UNWINNABLE, .ALWAYS_WINNABLE, or WinnableWithDraws(draws).
    """
    if _past_deadline(deadline):
        return SimpleResult.UNWINNABLE

    state = game.state
    is_clue = isinstance(perform, (PerformColour, PerformRank))

    if state.cards_left == 0 or is_clue:
        new_game = advance_game(game, perform, player_turn, draw=None)
        if winnable_simpler(new_game, state.next_player_index(player_turn), remaining, deadline, depth + 1):
            return SimpleResult.ALWAYS_WINNABLE
        return SimpleResult.UNWINNABLE

    trash_ids = [i for i in remaining if state.is_basic_trash(i)]
    other_ids = [i for i in remaining if not state.is_basic_trash(i)]

    def is_winnable(draw_id: Identity) -> bool:
        new_game = advance_game(game, perform, player_turn, draw=draw_id)
        new_remaining = remaining_remove(remaining, draw_id)
        return winnable_simpler(
            new_game, state.next_player_index(player_turn), new_remaining, deadline, depth + 1
        )

    trash_winnable = not trash_ids or is_winnable(trash_ids[0])
    other_winnable = [i for i in other_ids if is_winnable(i)]

    if not trash_winnable and not other_winnable:
        return SimpleResult.UNWINNABLE
    if trash_winnable:
        return WinnableWithDraws(draws=[*trash_ids, *other_winnable])
    return WinnableWithDraws(draws=other_winnable)
