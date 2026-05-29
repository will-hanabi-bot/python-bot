"""Game-state evaluator for reactor.

Port of scala-bot/src/scala_bot/reactor/stateEval.scala.

Used to score candidate actions during take_action.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hanabi_bot.basics.action import (
    Action,
    ClueAction,
    DiscardAction,
    PlayAction,
)
from hanabi_bot.basics.card import CardStatus
from hanabi_bot.basics.clue_result import (
    bad_touch_result,
    elim_result,
    playables_result,
)
from hanabi_bot.basics.eval import force_clue
from hanabi_bot.basics.identity import Identity
from hanabi_bot.basics.interp import ClueInterp, DiscardInterp
from hanabi_bot.basics.player import visible_find
from hanabi_bot.basics.state import State

if TYPE_CHECKING:
    from .reactor import Reactor


def get_result(game: Reactor, hypo: Reactor, action: ClueAction) -> float:
    """Score a clue based on its effect on the hypothetical game."""
    state = game.state
    common = game.common
    meta = game.meta

    new_touched, fill, elim = elim_result(
        game, hypo, hypo.state.hands[action.target], action.list_
    )
    bad_touch, trash, _ = bad_touch_result(game, hypo, action)
    _, playables = playables_result(game, hypo)

    revealed_trash = sum(
        1 for o in hypo.common.thinks_trash(hypo, action.target)
        if hypo.state.deck[o].clued
        and o not in common.thinks_trash(game, action.target)
    )

    new_playables = [
        o for hand in state.hands for o in hand
        if meta[o].status != CardStatus.CALLED_TO_PLAY
        and hypo.meta[o].status == CardStatus.CALLED_TO_PLAY
    ]
    bad_playable = next(
        (
            o for o in new_playables
            if not (
                o in hypo.me.hypo_plays
                or (game.in_endgame and (id_ := state.deck[o].id()) is not None and state.is_playable(id_))
            )
        ),
        None,
    )
    if bad_playable is not None:
        return -100

    move = hypo.last_move
    if move == ClueInterp.PLAY and not playables and not game.in_endgame:
        return -100
    if (
        move == ClueInterp.REVEAL
        and not playables
        and trash
        and all(state.deck[o].clued for o in trash)
    ):
        return -100
    if move != ClueInterp.REACTIVE and bad_touch and all(o in bad_touch for o in new_touched) and not playables:
        return -100

    duped_playables = sum(
        1 for p in hypo.me.hypo_plays
        if not state.deck[p].clued
        and any(
            o != p and game.is_touched(o) and state.deck[o].matches(state.deck[p])
            for hand in state.hands for o in hand
        )
    )

    new_touched_count = len(new_touched)
    bad_count = len(bad_touch)
    if bad_count > new_touched_count:
        good_touch = float(-bad_count)
    else:
        delta = new_touched_count - bad_count
        good_touch = (0.0, 0.125, 0.25, 0.35, 0.45, 0.55)[min(delta, 5)]

    untouched_plays = sum(1 for o in playables if not hypo.state.deck[o].clued)

    value = (
        good_touch
        + (len(playables) - 2.0 * duped_playables)
        + 0.2 * untouched_plays
        + (0.01 if game.in_endgame else 0.05) * revealed_trash
        + (0.1 if game.in_endgame else 0.05) * len(fill)
        + (0.05 if game.in_endgame else 0.02) * len(elim)
        + -0.1 * bad_count
    )

    if move == ClueInterp.MISTAKE:
        return value - 10
    if move == ClueInterp.FIX:
        return value + 1
    return value


def _force_clue_inner(orig: Reactor, game: Reactor, offset: int) -> float:
    state = game.state
    giver = (state.our_player_index + offset) % state.num_players
    bob = state.next_player_index(giver)

    if bob == state.our_player_index:
        import dataclasses
        next_game = game.with_state(
            lambda s: dataclasses.replace(s, clue_tokens=s.clue_tokens - 1)
        )
        return advance(orig, next_game, offset + 1) + 1.0

    def advance_fn(g):  # type: ignore[no-untyped-def]
        return advance(orig, g, offset + 1)

    return force_clue(game, giver, advance_fn, only=bob) + 0.5


def advance(orig: Reactor, game: Reactor, offset: int) -> float:
    """Recursively advance the game tree by simulating each player's choice."""
    state = game.state
    common = game.common
    meta = game.meta
    player_index = (state.our_player_index + offset) % state.num_players
    player = game.players[player_index]

    bob = state.next_player_index(player_index)
    bob_chop = game.chop(bob) if state.num_players != 2 else None

    trash = player.thinks_trash(game, player_index)
    urgent_dc = next((o for o in trash if meta[o].urgent), None)
    all_playables = player.obvious_playables(game, player_index)

    if player_index == state.our_player_index or state.endgame_turns == 0:
        return eval_game(orig, game)

    if urgent_dc is None and all_playables:
        urgent_play = next((o for o in all_playables if meta[o].urgent), None)
        if urgent_play is not None:
            playables = [urgent_play]
        else:
            playables = [
                o for o in all_playables
                if not any(
                    p > o and common.thoughts[p].possible == common.thoughts[o].possible
                    for p in all_playables
                )
            ]

        strike = False
        play_values: list[float] = []
        for order in playables:
            id_ = state.deck[order].id()
            if id_ is None:
                action: Action = PlayAction(player_index, order, -1, -1)
            elif state.is_playable(id_):
                action = PlayAction(player_index, order, id_.suit_index, id_.rank)
            else:
                action = DiscardAction(
                    player_index, order, id_.suit_index, id_.rank, True
                )
            advanced = game.simulate(action)
            if advanced.state.strikes > game.state.strikes:
                strike = True
            play_values.append(advance(orig, advanced, offset + 1))

        if strike:
            return min(play_values)
        return max(max(play_values), _force_clue_inner(orig, game, offset))

    if player.obvious_locked(game, player_index):
        if not state.can_clue:
            locked_dc = player.locked_discard(state, player_index)
            id_ = state.deck[locked_dc].id()
            if id_ is None:
                action = DiscardAction(player_index, locked_dc, -1, -1, False)
            else:
                action = DiscardAction(
                    player_index, locked_dc, id_.suit_index, id_.rank, False
                )
            return advance(orig, game.simulate(action), offset + 1)
        return _force_clue_inner(orig, game, offset)

    if state.clue_tokens == 8:
        return _force_clue_inner(orig, game, offset)

    if urgent_dc is not None:
        id_ = state.deck[urgent_dc].id()
        if id_ is None:
            action = DiscardAction(player_index, urgent_dc, -1, -1, False)
        else:
            action = DiscardAction(
                player_index, urgent_dc, id_.suit_index, id_.rank, False
            )
        return advance(orig, game.simulate(action), offset + 1)

    def try_discard(order: int) -> float:
        id_ = state.deck[order].id()
        if id_ is None:
            action = DiscardAction(player_index, order, -1, -1, False)
        else:
            action = DiscardAction(
                player_index, order, id_.suit_index, id_.rank, False
            )
        dc_value = advance(orig, game.simulate(action), offset + 1)
        if state.clue_tokens < 2:
            return dc_value
        clue_value = _force_clue_inner(orig, game, offset)
        if offset == 1:
            if common.obvious_loaded(game, bob):
                clue_prob = 0.2
            elif bob_chop is not None:
                bob_chop_id = state.deck[bob_chop].id()
                clue_prob = (
                    0.2
                    if bob_chop_id is not None and state.is_basic_trash(bob_chop_id)
                    else 0.7
                )
            else:
                clue_prob = 0.5
        else:
            clue_prob = 0.8
        if clue_value < dc_value:
            return dc_value
        return clue_prob * clue_value + (1.0 - clue_prob) * dc_value

    if urgent_dc is not None:
        order = urgent_dc
    elif trash:
        order = trash[0]
    else:
        import dataclasses
        check_game = game.copy_with(
            state=dataclasses.replace(state, current_player_index=player_index)
        )
        if offset == 1 and not check_game.has_ptd:
            return _force_clue_inner(orig, game, offset)
        chop = game.chop(player_index)
        if chop is None:
            if game.zcs_turn is not None:
                chop = player.locked_discard(state, player_index)
            else:
                # Defensive: fall back to locked discard.
                chop = player.locked_discard(state, player_index)
        order = chop
    return try_discard(order)


def eval_action(game: Reactor, action: Action) -> float:
    """Score an action via game-tree simulation."""
    state = game.state
    hypo_game = game.simulate(action)

    mistake = False
    if (
        isinstance(action, ClueAction)
        and hypo_game.last_move == ClueInterp.MISTAKE
    ):
        mistake = True
    if (
        isinstance(action, DiscardAction)
        and hypo_game.last_move == DiscardInterp.MISTAKE
    ):
        mistake = True

    if mistake:
        return -100

    value: float = 0
    if isinstance(action, ClueAction):
        playables_us = game.me.obvious_playables(game, state.our_player_index)
        mult = (0.1 if game.in_endgame else 0.25) if playables_us else 0.5
        result = get_result(game, hypo_game, action)
        value = result * (mult if result > 0 else 1.0) - 0.5
    elif isinstance(action, PlayAction):
        id_ = Identity(action.suit_index, action.rank) if action.suit_index != -1 else None
        unknown_dupe = False
        if id_ is not None and not game.in_endgame:
            for o in visible_find(state, game.me, id_, exclude_order=action.order):
                if game.is_touched(o) and not hypo_game.common.order_trash(hypo_game, o):
                    unknown_dupe = True
                    break
        if unknown_dupe:
            value = -0.25
        elif id_ is None:
            value = 1.5
        else:
            value = 0.02 * (5 - id_.rank)
    elif isinstance(action, DiscardAction):
        id_ = Identity(action.suit_index, action.rank) if action.suit_index != -1 else None
        is_trash = game.me.order_kt(game, action.order) or game.meta[action.order].status == CardStatus.CALLED_TO_DISCARD
        chop = game.chop(state.holder_of(action.order))
        if game.in_endgame:
            value = -1.0
        elif is_trash:
            value = 0
        elif chop == action.order:
            value = -0.25
        elif id_ is None:
            value = -1.5
        else:
            value = -0.5

    if value == -100:
        return -100
    return value + advance(game, hypo_game, 1)


def eval_state(state: State, in_endgame: bool) -> float:
    """Pure state-based evaluation (score, clues, strikes, scoreloss)."""
    score_val = min(state.score, 2 * len(state.variant.suits)) * 0.5 + state.score
    if in_endgame or state.clue_tokens == 0 or not state.can_clue:
        clue_val = 0.0
    elif state.clue_tokens > 6:
        clue_val = 3 + (state.clue_tokens - 6) * 0.25
    else:
        clue_val = state.clue_tokens / 2.0

    score_loss = len(state.variant.suits) * 5 - state.max_score
    dc_crit_val = -20 * score_loss

    if state.strikes == 1:
        strikes_val = -1.5
    elif state.strikes == 2:
        strikes_val = -3.5
    elif state.strikes == 3:
        strikes_val = -100.0
    else:
        strikes_val = 0.0
    return score_val + clue_val + dc_crit_val + strikes_val


def eval_game(orig: Reactor, game: Reactor) -> float:
    """Full game-tree leaf evaluation (state + future + bdr + locks)."""
    state = game.state
    if state.score == orig.state.max_score:
        return 100

    state_val = eval_state(
        state,
        in_endgame=orig.in_endgame or orig.state.rem_score < len(state.variant.suits),
    )

    future_val = 0.0
    for order in (o for hand in state.hands for o in hand):
        status = game.meta[order].status
        if status == CardStatus.CALLED_TO_PLAY:
            id_ = game.me.thoughts[order].id(infer=True)
            if id_ is None:
                future_val += 0.4
            elif state.is_basic_trash(id_):
                future_val -= 1.5
            elif id_.rank == 5:
                future_val += 0.8
            else:
                future_val += 0.4
        elif status == CardStatus.CALLED_TO_DISCARD:
            by = game.meta[order].by
            if by is None:
                continue
            id_ = state.deck[order].id()
            if id_ is None:
                if by != state.our_player_index:
                    future_val += 0
                else:
                    future_val += 0.3
            elif state.is_basic_trash(id_):
                future_val += 0.3
            elif game.me.is_sieved(game, id_, order):
                future_val += 0.2
            elif state.is_critical(id_):
                future_val -= (5 - state.playable_away(id_)) * 10.0
            elif by != state.our_player_index:
                future_val += 0
            else:
                future_val -= (5 - state.playable_away(id_)) * 0.5

    bdr_val = 0.0
    for id_ in state.variant.all_ids():
        discarded = state.discard_stacks[id_.suit_index][id_.rank - 1]
        if state.is_basic_trash(id_) or id_.rank == 5 or not discarded:
            continue
        duplicate = next(
            (
                o for hand in state.hands for o in hand
                if state.deck[o].matches(id_)
                or (game.me.thoughts[o].matches(id_, infer=True) and game.meta[o].focused)
            ),
            None,
        )
        duplicated = duplicate is not None or all(
            game.meta[o].status == CardStatus.CALLED_TO_DISCARD
            and game.meta[o].by is not None
            and game.meta[o].by != state.our_player_index
            and any(id_ in game.me.thoughts[o2].possible for o2 in orig.state.our_hand)
            for o in discarded
        )
        if duplicated:
            continue
        if id_.rank == 1:
            bdr_val -= len(discarded) ** 2
        elif id_.rank == 2:
            bdr_val -= 3
        elif id_.rank == 3:
            bdr_val -= 1.5
        else:
            bdr_val -= 0.5
    bdr_val *= 2.5

    locked_count = sum(
        1 for i in range(state.num_players) if game.common.thinks_locked(game, i)
    )
    if locked_count == 0:
        lock_penalty = 0.0
    elif locked_count == 1:
        lock_penalty = -1.0
    elif locked_count == 2:
        lock_penalty = -3.0
    else:
        lock_penalty = -10.0

    endgame_penalty = 0.0
    if orig.state.endgame_turns is not None:
        turns = orig.state.endgame_turns
        stacks = list(orig.state.play_stacks)
        for i in range(turns):
            player_index = (orig.state.current_player_index + i + 1) % state.num_players
            for o in orig.state.hands[player_index]:
                pid = orig.state.deck[o].id()
                if pid is None:
                    continue
                if orig.state.is_playable(pid):
                    stacks[pid.suit_index] = pid.rank
                    break
        endgame_penalty = (sum(stacks) - state.max_score) * 5

    return state_val + future_val + bdr_val + lock_penalty + endgame_penalty
