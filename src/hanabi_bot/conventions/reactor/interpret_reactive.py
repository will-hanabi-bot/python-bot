"""Interpret reactive clues (color and rank).

Port of scala-bot/src/scala_bot/reactor/interpretReactive.scala.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from hanabi_bot.basics.card import CardStatus
from hanabi_bot.basics.interp import ClueInterp
from hanabi_bot.basics.player import players_until
from hanabi_bot.basics.state import HAND_SIZE, State

from .interpret_reaction import calc_slot

if TYPE_CHECKING:
    from hanabi_bot.basics.action import ClueAction

    from .reactor import Reactor


def _reactive_context(
    prev: Reactor, game: Reactor, action: ClueAction, reacter: int
):
    """Compute (possible_conns, known_plays, hypo_state) for the reactive interpretation."""
    from .interpret_clue import delayed_plays

    giver = action.giver
    receiver = action.target
    state = game.state

    possible_conns = delayed_plays(game, giver, receiver, stable=False)
    old_playables = prev.common.obvious_playables(prev, receiver)
    new_playables = game.common.obvious_playables(game, receiver)
    known_plays = [o for o in old_playables if o in new_playables]

    # Hypothetically advance through other players' obvious plays.
    after_others: State = prev.state
    for i in players_until(state.num_players, state.next_player_index(giver), reacter):
        hypo_prev = prev.copy_with(state=after_others)
        ps = prev.common.obvious_playables(hypo_prev, i)
        playable_o = None
        for o in ps:
            if prev.meta[o].urgent:
                playable_o = o
                break
        if playable_o is None and ps:
            playable_o = ps[0]
        if playable_o is None:
            continue
        id_ = state.deck[playable_o].id()
        if id_ is not None:
            after_others = after_others.try_play(id_)

    hypo_state = after_others
    self_plays = prev.common.obvious_playables(prev.copy_with(state=after_others), receiver)
    for self_play in self_plays:
        id_ = state.deck[self_play].id()
        if id_ is not None:
            hypo_state = hypo_state.try_play(id_)

    return possible_conns, known_plays, hypo_state


def interpret_reactive_colour(
    prev: Reactor, game: Reactor, action: ClueAction, focus_slot: int, reacter: int, looks_stable: bool
) -> tuple[ClueInterp | None, Reactor]:
    """Reactive colour clue: try dc+play targets, fall back to play+dc."""
    from .interpret_clue import target_discard, target_play

    state = game.state
    receiver = action.target
    possible_conns, known_plays, hypo_state = _reactive_context(prev, game, action, reacter)

    # Find play targets in receiver's hand (cards visibly playable in hypo_state).
    play_targets: list[tuple[int, int]] = []
    for i, o in enumerate(state.hands[receiver]):
        if game.meta[o].status == CardStatus.CALLED_TO_DISCARD:
            continue
        if o in known_plays:
            continue
        id_ = state.deck[o].id()
        if id_ is None or not hypo_state.is_playable(id_):
            continue
        play_targets.append((o, i))

    # Sort: unclued dupe (with clued dupe ahead of it) → last.
    def _sort_key(t: tuple[int, int]) -> int:
        o, i = t
        unclued_dupe = (
            not prev.state.deck[o].clued
            and any(
                o2 < o
                and prev.state.deck[o2].clued
                and state.deck[o].matches(state.deck[o2])
                for o2 in state.hands[receiver]
            )
        )
        return 99 if unclued_dupe else i
    play_targets.sort(key=_sort_key)

    hand_size = HAND_SIZE[state.num_players]
    for _target, index in play_targets:
        target_slot = index + 1
        react_slot = calc_slot(focus_slot, target_slot, hand_size)
        if react_slot < 1 or react_slot > len(state.hands[reacter]):
            continue
        react_order = state.hands[reacter][react_slot - 1]
        if (
            looks_stable
            and react_order in prev.common.thinks_trash(prev, reacter)
            and not prev.common.obvious_playables(prev, reacter)
        ):
            continue
        if game.common.thoughts[react_order].possible.forall(state.is_critical):
            continue
        new_common = game.common.with_thought(
            react_order,
            lambda t: dataclasses.replace(t, old_inferred=t.inferred),
        )
        interp, new_game = target_discard(
            game.copy_with(common=new_common), action, react_order, urgent=True
        )
        if interp is None:
            return None, new_game
        return ClueInterp.REACTIVE, new_game

    # Didn't work — try discard targets.
    prev_kt = prev.common.thinks_trash(prev, receiver)
    unknown_trash: list[tuple[int, int]] = []
    for i, o in enumerate(state.hands[receiver]):
        if o in prev_kt:
            continue
        deck_id = state.deck[o].id()
        if deck_id is None:
            continue
        if state.is_basic_trash(deck_id) or any(
            o2 < o and state.deck[o].matches(state.deck[o2]) for o2 in state.hands[receiver]
        ):
            unknown_trash.append((o, i))
    unknown_trash.sort(key=lambda x: -1 if prev.state.deck[x[0]].clued else 1)

    unknown_dupes = [
        (o, i)
        for i, o in enumerate(state.hands[receiver])
        if o not in prev_kt
        and any(
            o2 != o and game.common.thoughts[o2].matches(state.deck[o], infer=True)
            for hand in state.hands
            for o2 in hand
        )
    ]

    known_trash = [
        (o, i) for i, o in enumerate(state.hands[receiver])
        if (id_ := state.deck[o].id()) is not None and state.is_basic_trash(id_)
    ]

    sacrifices = [
        (o, i)
        for i, o in enumerate(state.hands[receiver])
        if o not in prev_kt
        and (id_ := state.deck[o].id()) is not None
        and not state.is_critical(id_)
    ]
    sacrifices.sort(
        key=lambda x: -game.common.playable_away(state.deck[x[0]].id()) * 10 + (5 - state.deck[x[0]].id().rank)  # type: ignore[union-attr]
    )

    dc_targets = unknown_trash or known_trash or unknown_dupes or sacrifices

    if not dc_targets:
        return None, game

    for target, index in dc_targets:
        if state.next_player_index(action.giver) != reacter and game.meta[target].status == CardStatus.CALLED_TO_PLAY:
            continue
        target_slot = index + 1
        react_slot = calc_slot(focus_slot, target_slot, hand_size)
        if react_slot < 1 or react_slot > len(state.hands[reacter]):
            continue
        react_order = state.hands[reacter][react_slot - 1]
        prev_plays = prev.common.obvious_playables(prev, reacter)
        if react_order in prev_plays:
            continue
        if not game.common.thoughts[react_order].possible.exists(
            lambda i, _ps=state.playable_set, _pc=possible_conns: i in _ps or any(c == i for _, c in _pc)
        ):
            continue
        new_common = game.common.with_thought(
            react_order,
            lambda t: dataclasses.replace(t, old_inferred=t.inferred),
        )
        interp, new_game = target_play(
            game.copy_with(common=new_common), action, react_order, urgent=True, stable=False
        )
        if interp is None:
            return None, new_game
        return ClueInterp.REACTIVE, new_game

    return None, game


def interpret_reactive_rank(
    prev: Reactor, game: Reactor, action: ClueAction, focus_slot: int, reacter: int
) -> tuple[ClueInterp | None, Reactor]:
    """Reactive rank clue: play+play targets, fall back to finesse."""
    from .interpret_clue import target_play

    state = game.state
    receiver = action.target
    possible_conns, known_plays, hypo_state = _reactive_context(prev, game, action, reacter)

    play_targets: list[tuple[int, int]] = []
    for i, o in enumerate(state.hands[receiver]):
        if game.meta[o].status == CardStatus.CALLED_TO_DISCARD:
            continue
        if o in known_plays:
            continue
        id_ = state.deck[o].id()
        if id_ is None or not hypo_state.is_playable(id_):
            continue
        play_targets.append((o, i))

    def _sort_key(t: tuple[int, int]) -> int:
        o, i = t
        unclued_dupe = (
            not prev.state.deck[o].clued
            and any(
                o2 != o
                and prev.state.deck[o2].clued
                and state.deck[o].matches(state.deck[o2])
                for o2 in state.hands[receiver]
            )
        )
        return 99 if unclued_dupe else i
    play_targets.sort(key=_sort_key)

    hand_size = HAND_SIZE[state.num_players]
    for target, index in play_targets:
        target_slot = index + 1
        react_slot = calc_slot(focus_slot, target_slot, hand_size)
        if react_slot < 1 or react_slot > len(state.hands[reacter]):
            continue
        react_order = state.hands[reacter][react_slot - 1]
        prev_plays = prev.common.obvious_playables(prev, reacter)
        if react_order in prev_plays:
            continue
        if not game.common.thoughts[react_order].possible.exists(
            lambda i, _ps=state.playable_set, _pc=possible_conns: i in _ps or any(c == i for _, c in _pc)
        ):
            continue
        interp, new_game = target_play(game, action, react_order, urgent=True, stable=False)
        if interp is None:
            return None, new_game
        target_id = state.deck[target].id()
        if target_id is not None:
            new_game = new_game.copy_with(
                common=new_game.common.with_thought(
                    react_order,
                    lambda t, _id=target_id: dataclasses.replace(
                        t, inferred=t.inferred.difference(_id)
                    ),
                )
            )
        return ClueInterp.REACTIVE, new_game

    # Finesse fallback
    finesse_targets: list[tuple[int, int]] = []
    for i, o in enumerate(state.hands[receiver]):
        id_ = state.deck[o].id()
        if id_ is not None and state.playable_away(id_) == 1:
            finesse_targets.append((o, i))

    if not finesse_targets:
        return None, game

    for react_slot in (1, 5, 4, 3, 2):
        target_slot = calc_slot(focus_slot, react_slot, hand_size)
        if react_slot < 1 or react_slot > len(state.hands[reacter]):
            continue
        react_order = state.hands[reacter][react_slot - 1]
        receive: tuple[int, int] | None = None
        for o, i in finesse_targets:
            if i + 1 == target_slot:
                receive = (o, i)
                break
        if receive is None:
            continue
        receive_order = receive[0]
        prev_plays = prev.common.obvious_playables(prev, reacter)
        if react_order in prev_plays:
            continue
        deck_id = state.deck[receive_order].id()
        if deck_id is None:
            continue
        prev_id = deck_id.prev
        if prev_id is None:
            continue
        if not game.common.thoughts[react_order].possible.exists(
            lambda i, _ps=state.playable_set, _pc=possible_conns: i in _ps or any(c == i for _, c in _pc)
        ):
            continue
        if prev_id not in game.common.thoughts[react_order].possible:
            continue
        new_common = game.common.with_thought(
            react_order,
            lambda t: dataclasses.replace(t, old_inferred=t.inferred),
        )
        interp, new_game = target_play(
            game.copy_with(common=new_common), action, react_order, urgent=True, stable=False
        )
        if interp is None:
            return None, new_game
        from hanabi_bot.basics.identity import IdentitySet
        new_game = new_game.copy_with(
            common=new_game.common.with_thought(
                react_order,
                lambda t, _pi=prev_id: dataclasses.replace(t, inferred=IdentitySet.single(_pi)),
            )
        )
        return ClueInterp.REACTIVE, new_game

    return None, game
