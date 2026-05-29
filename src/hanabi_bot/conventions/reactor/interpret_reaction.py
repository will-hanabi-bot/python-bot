"""React to plays/discards after a reactive clue was given.

Port of scala-bot/src/scala_bot/reactor/interpretReaction.scala.

The receiver waits for the reacter's response; when the reacter acts, we resolve
the WaitingConnection to figure out what the receiver's card meant.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from hanabi_bot.basics.action import InterpAction
from hanabi_bot.basics.card import CardStatus, ConvData
from hanabi_bot.basics.clue import ClueKind
from hanabi_bot.basics.interp import ClueInterp, DiscardInterp
from hanabi_bot.basics.player import Player
from hanabi_bot.basics.state import State

if TYPE_CHECKING:
    from .reactor import Reactor, ReactorWC


def calc_slot(focus_slot: int, slot: int) -> int:
    """Reactor's slot-arithmetic: target/react slot mapping. Both inputs/output are 1-indexed."""
    other = (focus_slot + 5 - slot) % 5
    return 5 if other == 0 else other


def _calc_target_slot(prev: Reactor, game: Reactor, order: int, wc: ReactorWC) -> tuple[int, int] | None:
    """Given the reacter's played/discarded order, compute (react_slot, target_slot)."""
    react_slot = prev.state.hands[wc.reacter].index(order) + 1 if order in prev.state.hands[wc.reacter] else None
    if react_slot is None:
        return None
    target_slot = calc_slot(wc.focus_slot, react_slot)
    if target_slot < 1 or target_slot > len(wc.receiver_hand):
        return None
    receive_order = wc.receiver_hand[target_slot - 1]
    if receive_order not in game.state.hands[wc.receiver]:
        return None
    return (react_slot, target_slot)


def _update_meta(
    meta: tuple[ConvData, ...], common: Player, receive_order: int
) -> tuple[ConvData, ...]:
    """Mark receive_order as trash if its inferred set is empty."""
    if common.thoughts[receive_order].inferred.is_empty:
        new_meta = list(meta)
        new_meta[receive_order] = dataclasses.replace(new_meta[receive_order], trash=True)
        return tuple(new_meta)
    return meta


def elim_dc_dc(
    state: State,
    common: Player,
    meta: tuple[ConvData, ...],
    reacter: int,
    receiver_hand: tuple[int, ...],
    focus_slot: int,
    target_slot: int,
) -> tuple[Player, tuple[ConvData, ...]]:
    """After a 'discard-discard' reactive interpretation, eliminate trash from earlier slots."""
    # First, ensure earlier slots have no playables (they were already not played).
    new_common, new_meta = elim_play_play(
        state, common, meta, reacter, receiver_hand, focus_slot, len(receiver_hand) + 1
    )
    for i in range(target_slot - 1):
        receive_order = receiver_hand[i]
        status = new_meta[receive_order].status
        react_slot = calc_slot(focus_slot, i + 1)
        target_card = receiver_hand[target_slot - 1] if target_slot - 1 < len(receiver_hand) else None
        skip = (
            status == CardStatus.CALLED_TO_PLAY
            or status == CardStatus.CALLED_TO_DISCARD
            or (
                target_card is not None
                and state.deck[target_card].clued
                and not state.deck[receive_order].clued
            )
        )
        if skip:
            continue
        if react_slot < 1 or react_slot > len(state.hands[reacter]):
            continue
        react_order = state.hands[reacter][react_slot - 1]
        if new_common.thoughts[react_order].possible.forall(state.is_critical):
            continue
        new_common = new_common.with_thought(
            receive_order,
            lambda t, _ts=state.trash_set: dataclasses.replace(
                t, inferred=t.inferred.difference(_ts)
            ),
        )
    return new_common, new_meta


def elim_play_dc(
    state: State,
    common: Player,
    meta: tuple[ConvData, ...],
    reacter: int,
    receiver_hand: tuple[int, ...],
    focus_slot: int,
    target_slot: int,
) -> tuple[Player, tuple[ConvData, ...]]:
    """After a 'play-discard' reactive interpretation, eliminate trash from earlier slots."""
    new_common, new_meta = elim_play_play(
        state, common, meta, reacter, receiver_hand, focus_slot, len(receiver_hand) + 1
    )
    for i in range(target_slot - 1):
        receive_order = receiver_hand[i]
        status = new_meta[receive_order].status
        react_slot = calc_slot(focus_slot, i + 1)
        target_card = receiver_hand[target_slot - 1] if target_slot - 1 < len(receiver_hand) else None
        skip = (
            status == CardStatus.CALLED_TO_PLAY
            or status == CardStatus.CALLED_TO_DISCARD
            or (
                target_card is not None
                and state.deck[target_card].clued
                and not state.deck[receive_order].clued
            )
        )
        if skip:
            continue
        if react_slot < 1 or react_slot > len(state.hands[reacter]):
            continue
        react_order = state.hands[reacter][react_slot - 1]
        if (
            new_meta[react_order].status != CardStatus.CALLED_TO_PLAY
            and new_common.thoughts[react_order].possible.exists(state.is_playable)
        ):
            new_common = new_common.with_thought(
                receive_order,
                lambda t, _ts=state.trash_set: dataclasses.replace(
                    t, inferred=t.inferred.difference(_ts)
                ),
            )
    return new_common, new_meta


def elim_dc_play(
    state: State,
    common: Player,
    meta: tuple[ConvData, ...],
    reacter: int,
    receiver_hand: tuple[int, ...],
    focus_slot: int,
    target_slot: int,
) -> tuple[Player, tuple[ConvData, ...]]:
    """After a 'discard-play' reactive interpretation, eliminate playables from earlier slots."""
    new_meta = meta
    new_common = common
    for i in range(target_slot - 1):
        receive_order = receiver_hand[i]
        status = new_meta[receive_order].status
        react_slot = calc_slot(focus_slot, i + 1)
        if status == CardStatus.CALLED_TO_PLAY or status == CardStatus.CALLED_TO_DISCARD:
            continue
        if react_slot < 1 or react_slot > len(state.hands[reacter]):
            continue
        react_order = state.hands[reacter][react_slot - 1]
        if not new_common.thoughts[react_order].possible.forall(state.is_critical):
            new_common = new_common.with_thought(
                receive_order,
                lambda t, _ps=state.playable_set: dataclasses.replace(
                    t, inferred=t.inferred.difference(_ps)
                ),
            )
            new_meta = _update_meta(new_meta, new_common, receive_order)
    return new_common, new_meta


def elim_play_play(
    state: State,
    common: Player,
    meta: tuple[ConvData, ...],
    reacter: int,
    receiver_hand: tuple[int, ...],
    focus_slot: int,
    target_slot: int,
) -> tuple[Player, tuple[ConvData, ...]]:
    """For each earlier-slot card whose reacter-card matches exactly one playable id,
    pin the receiver card to that single id; else eliminate all playables.
    """
    new_meta = meta
    new_common = common
    for i in range(target_slot - 1):
        receive_order = receiver_hand[i]
        status = new_meta[receive_order].status
        react_slot = calc_slot(focus_slot, i + 1)
        if status == CardStatus.CALLED_TO_PLAY or status == CardStatus.CALLED_TO_DISCARD:
            continue
        if react_slot < 1 or react_slot > len(state.hands[reacter]):
            continue
        react_order = state.hands[reacter][react_slot - 1]
        intersect = new_common.thoughts[react_order].possible.intersect(state.playable_set)
        if intersect.length == 0:
            continue
        if intersect.length == 1:
            id_ = intersect.head
            new_common = new_common.with_thought(
                receive_order,
                lambda t, _id=id_, _ps=state.playable_set: dataclasses.replace(
                    t,
                    inferred=t.inferred.filter(
                        lambda iid, __ps=_ps, __id=_id: (iid not in __ps) or iid == __id
                    ),
                ),
            )
        else:
            new_common = new_common.with_thought(
                receive_order,
                lambda t, _ps=state.playable_set: dataclasses.replace(
                    t, inferred=t.inferred.difference(_ps)
                ),
            )
        new_meta = _update_meta(new_meta, new_common, receive_order)
    return new_common, new_meta


def target_i_discard(
    prev: Reactor, game: Reactor, wc: ReactorWC, target_slot: int
) -> tuple[Player, tuple[ConvData, ...]]:
    """Mark the receiver-slot as CalledToDiscard, removing criticals from inferred."""
    common = game.common
    order = wc.receiver_hand[target_slot - 1]
    new_inferred = common.thoughts[order].inferred.filter(
        lambda i, _c=prev.state.critical_set: i not in _c
    )
    new_common = common.with_thought(
        order,
        lambda t, _ni=new_inferred: dataclasses.replace(
            t, old_inferred=t.inferred, inferred=_ni
        ),
    )
    new_meta = list(game.meta)
    new_meta[order] = (
        dataclasses.replace(
            new_meta[order],
            status=CardStatus.CALLED_TO_DISCARD,
            by=wc.giver,
            trash=new_inferred.is_empty,
        )
        .reason(game.state.turn_count)
        .signal(game.state.turn_count)
    )
    return new_common, tuple(new_meta)


def target_i_play(
    _prev: Reactor, game: Reactor, wc: ReactorWC, target_slot: int
) -> tuple[Player, tuple[ConvData, ...]]:
    """Mark the receiver-slot as CalledToPlay, intersected with playable set + connectors."""
    state = game.state
    order = wc.receiver_hand[target_slot - 1]
    self_playables = state.playable_set
    for o in game.common.obvious_playables(game, state.holder_of(order)):
        for inf in game.common.thoughts[o].inferred:
            nxt = inf.next
            if nxt is not None:
                self_playables = self_playables.add(nxt)
    new_inferred = game.common.thoughts[order].inferred.intersect(self_playables)
    new_common = game.common.with_thought(
        order,
        lambda t, _ni=new_inferred: dataclasses.replace(
            t, old_inferred=t.inferred, inferred=_ni, info_lock=_ni if _ni.non_empty else None
        ),
    )
    new_meta = list(game.meta)
    new_meta[order] = (
        dataclasses.replace(
            new_meta[order],
            status=CardStatus.CALLED_TO_PLAY,
            by=wc.giver,
            focused=True,
        )
        .reason(state.turn_count)
        .signal(state.turn_count)
    )
    return new_common, tuple(new_meta)


def react_discard(
    prev: Reactor, game: Reactor, player_index: int, order: int, wc: ReactorWC
) -> Reactor:
    """Handle a discard while there's an active waiting reactive connection."""
    if player_index != wc.reacter:
        return game.with_move(DiscardInterp.NONE)

    if wc.inverted:
        # Response inversion: were we expecting them to play, but they discarded?
        prev_obvious_playables = prev.common.obvious_playables(game, wc.reacter)
        if prev_obvious_playables:
            unnatural = True
        else:
            known_trash = prev.common.thinks_trash(prev, wc.reacter)
            if not known_trash:
                chop = prev.chop(wc.reacter)
                unnatural = chop is None or chop != order
            else:
                unnatural = order not in known_trash
        if unnatural:
            try:
                new_game = game.rewind(wc.turn, InterpAction(ClueInterp.REACTIVE))
                return new_game  # type: ignore[return-value]
            except (ValueError, RuntimeError):
                return game.with_move(DiscardInterp.NONE)
        return game.with_move(DiscardInterp.NONE)

    slots = _calc_target_slot(prev, game, order, wc)
    if slots is None:
        return game.with_move(DiscardInterp.NONE)
    react_slot, target_slot = slots
    if wc.clue.kind == ClueKind.COLOUR:
        nc, nm = target_i_play(prev, game, wc, target_slot)
        nc, nm = elim_dc_play(
            prev.state, nc, nm, wc.reacter, wc.receiver_hand, wc.focus_slot, target_slot
        )
    else:
        nc, nm = target_i_discard(prev, game, wc, target_slot)
        nc, nm = elim_dc_dc(
            prev.state, nc, nm, wc.reacter, wc.receiver_hand, wc.focus_slot, target_slot
        )
    return game.copy_with(common=nc, meta=nm).with_move(DiscardInterp.NONE)


def react_play(
    prev: Reactor, game: Reactor, player_index: int, order: int, wc: ReactorWC
) -> Reactor:
    """Handle a play while there's an active waiting reactive connection."""
    if player_index != wc.reacter:
        return game

    if wc.inverted:
        # In an inverted (response-inversion) waiting state, we expected them to react.
        known_playables = prev.common.obvious_playables(prev, wc.reacter)
        if not known_playables:
            known_playables = prev.players[wc.reacter].thinks_playables(prev, wc.reacter)
        if order not in known_playables:
            try:
                new_game = game.rewind(wc.turn, InterpAction(ClueInterp.REACTIVE))
                return new_game  # type: ignore[return-value]
            except (ValueError, RuntimeError):
                return game
        return game

    slots = _calc_target_slot(prev, game, order, wc)
    if slots is None:
        return game
    react_slot, target_slot = slots
    if wc.clue.kind == ClueKind.RANK:
        nc, nm = target_i_play(prev, game, wc, target_slot)
        nc, nm = elim_play_play(
            prev.state, nc, nm, wc.reacter, wc.receiver_hand, wc.focus_slot, target_slot
        )
    else:
        nc, nm = target_i_discard(prev, game, wc, target_slot)
        nc, nm = elim_play_dc(
            prev.state, nc, nm, wc.reacter, wc.receiver_hand, wc.focus_slot, target_slot
        )
    return game.copy_with(common=nc, meta=nm)
