"""Stable + reactive clue interpretation entry point + ref play/discard.

Port of scala-bot/src/scala_bot/reactor/interpretClue.scala.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from hanabi_bot.basics.action import ClueAction
from hanabi_bot.basics.card import CardStatus
from hanabi_bot.basics.clue import ClueKind
from hanabi_bot.basics.fix import (
    FixResultNormal,
    check_fix,
    connectable_simple,
)
from hanabi_bot.basics.identity import Identity, IdentitySet
from hanabi_bot.basics.interp import ClueInterp
from hanabi_bot.basics.player import players_until
from hanabi_bot.basics.state import State
from hanabi_bot.basics.variant import BROWNISH, PINKISH, RAINBOWISH

if TYPE_CHECKING:
    from .reactor import Reactor


def _reactive_focus(state: State, receiver: int, action: ClueAction) -> int:
    """Compute the focus slot for a reactive clue.

    Port of reactor.scala lines 8-18 in interpretClue.scala.
    """
    list_ = action.list_
    clue = action.clue
    hand = state.hands[receiver]
    # The Scala finds the max-order touched card, but treats slot 0 as -1 (so the newest goes last in maxBy).
    touched_with_index = [
        (o, i) for i, o in enumerate(hand) if o in list_
    ]
    if not touched_with_index:
        return 1
    # In Scala: `maxBy((o, _) => if o == state.hands(receiver)(0) then -1 else o)` — newest gets demoted.
    focus_o, focus_i = max(
        touched_with_index,
        key=lambda t: -1 if t[0] == hand[0] else t[0],
    )

    if clue.kind == ClueKind.COLOUR:
        if state.includes_variant(RAINBOWISH) or state.variant.rainbow_s:
            return clue.value + 1
        return focus_i + 1
    # Rank
    if state.includes_variant(PINKISH) or state.variant.pink_s:
        return clue.value
    return focus_i + 1


def delayed_plays(
    game: Reactor, giver: int, receiver: int, stable: bool
) -> list[tuple[int, Identity]]:
    """All cards that will be playable by the receiver's turn (via observable plays).

    Returns list of (order, NEXT identity that becomes playable).
    Port of interpretClue.scala lines 320-345.
    """
    common = game.common
    state = game.state
    meta = game.meta

    result: list[tuple[int, Identity]] = []
    for player_index in players_until(state.num_players, state.next_player_index(giver), receiver):
        urgent_order: int | None = None
        for o in state.hands[player_index]:
            if meta[o].urgent:
                urgent_order = o
                break
        if urgent_order is not None:
            if meta[urgent_order].status == CardStatus.CALLED_TO_DISCARD:
                playables: list[int] = []
            else:
                playables = [urgent_order]
        else:
            obvious = common.obvious_playables(game, player_index)
            playables = [] if not stable and len(obvious) > 1 else obvious

        for o in playables:
            # Only consider the leftmost of similarly-possible cards
            if any(
                p > o and common.thoughts[p].possible == common.thoughts[o].possible
                for p in playables
            ):
                continue
            id_ = common.thoughts[o].id(infer=True)
            if id_ is not None:
                if id_.next is not None:
                    result.insert(0, (o, id_.next))
            else:
                non_trash = common.thoughts[o].inferred.difference(state.trash_set)
                for i in non_trash:
                    if i.next is not None:
                        result.insert(0, (o, i.next))
    return result


def ref_play(prev: Reactor, game: Reactor, action: ClueAction) -> tuple[ClueInterp | None, Reactor]:
    """Reference-play: target the card 'referred' (1 slot left of newly touched).

    Port of interpretClue.scala `refPlay` (lines 347-359).
    """
    hand = game.state.hands[action.target]
    newly_touched = [o for o in action.list_ if not prev.state.deck[o].clued]
    if not newly_touched:
        return None, game
    target_candidates = [game.common.refer(prev, hand, o, left=True) for o in newly_touched]
    target = max(target_candidates)

    if game.is_blind_playing(target):
        return None, game
    if game.meta[target].status == CardStatus.CALLED_TO_DISCARD:
        return None, game
    return target_play(game, action, target, urgent=False, stable=True)


def target_play(
    game: Reactor, action: ClueAction, target: int, urgent: bool = False, stable: bool = True
) -> tuple[ClueInterp | None, Reactor]:
    """Mark `target` as CalledToPlay, narrowing inferred to playable+connector ids.

    Port of interpretClue.scala `targetPlay` (lines 361-408).
    """
    state = game.state
    holder = state.holder_of(target)
    possible_conns = delayed_plays(game, action.giver, holder, stable)

    new_inferred = game.common.thoughts[target].inferred.filter(
        lambda i, _ps=state.playable_set, _pc=possible_conns: i in _ps or any(c == i for _, c in _pc)
    )

    result_game = game
    # If we have the actual id and a connector exists, mark the connector urgent.
    target_id = state.deck[target].id()
    if target_id is not None:
        for conn_order, conn_id in possible_conns:
            if conn_id == target_id:
                prev_id = target_id.prev
                if prev_id is None:
                    continue
                result_game = result_game.copy_with(
                    common=result_game.common.with_thought(
                        conn_order,
                        lambda t, _pi=prev_id: dataclasses.replace(
                            t, old_inferred=t.inferred, inferred=IdentitySet.single(_pi)
                        ),
                    )
                )
                new_meta = list(result_game.meta)
                new_meta[conn_order] = (
                    dataclasses.replace(
                        new_meta[conn_order],
                        urgent=True,
                        status=CardStatus.CALLED_TO_PLAY,
                        by=action.giver,
                    )
                    .reason(state.turn_count)
                )
                result_game = result_game.copy_with(meta=tuple(new_meta))
                break

    result_game = result_game.copy_with(
        common=result_game.common.with_thought(
            target,
            lambda t, _ni=new_inferred: dataclasses.replace(
                t,
                old_inferred=t.inferred,
                inferred=_ni,
                info_lock=_ni if _ni.non_empty else None,
            ),
        )
    )
    new_meta = list(result_game.meta)
    new_meta[target] = (
        new_meta[target].reason(state.turn_count).signal(state.turn_count)
    )
    result_game = result_game.copy_with(meta=tuple(new_meta))

    if new_inferred.is_empty or not state.has_consistent_infs(result_game.common.thoughts[target]):
        reset_game = result_game.copy_with(
            common=result_game.common.with_thought(
                target, lambda t: t.reset_inferences()
            )
        )
        if stable and reset_game.common.order_kt(reset_game, target):
            return ClueInterp.STALL, reset_game
        return None, reset_game

    new_meta = list(result_game.meta)
    new_meta[target] = dataclasses.replace(
        new_meta[target],
        status=CardStatus.CALLED_TO_PLAY,
        by=action.giver,
        focused=True,
        urgent=urgent,
    )
    return ClueInterp.PLAY, result_game.copy_with(meta=tuple(new_meta))


def target_discard(
    game: Reactor, action: ClueAction, target: int, urgent: bool = False
) -> tuple[ClueInterp | None, Reactor]:
    """Mark `target` as CalledToDiscard.

    Port of interpretClue.scala `targetDiscard` (lines 410-434).
    """
    state = game.state
    new_common = game.common.with_thought(
        target,
        lambda t, _cs=state.critical_set: dataclasses.replace(
            t,
            inferred=t.inferred.filter(lambda i, _ccs=_cs: not state.is_critical(i)),
        ),
    )
    new_meta = list(game.meta)
    new_meta[target] = (
        dataclasses.replace(
            new_meta[target],
            status=CardStatus.CALLED_TO_DISCARD,
            by=action.giver,
            urgent=urgent,
        )
        .reason(state.turn_count)
        .signal(state.turn_count)
    )
    new_game = game.copy_with(common=new_common, meta=tuple(new_meta))

    if new_game.common.thoughts[target].inferred.is_empty:
        reset_game = new_game.copy_with(
            common=new_game.common.with_thought(target, lambda t: t.reset_inferences())
        )
        return None, reset_game
    return ClueInterp.DISCARD, new_game


def ref_discard(
    prev: Reactor, game: Reactor, action: ClueAction, stall: bool
) -> tuple[ClueInterp | None, Reactor]:
    """Reference-discard: target the slot 1 position right of the focus.

    Port of interpretClue.scala `refDiscard` (lines 436-498).
    """
    state = game.state
    giver = action.giver
    receiver = action.target
    list_ = action.list_
    clue = action.clue
    hand = state.hands[receiver]
    newly_touched = [o for o in list_ if not prev.state.deck[o].clued]
    unclued_orders = [o for o in hand if not prev.state.deck[o].clued]
    lock_order = min(unclued_orders) if unclued_orders else None

    if lock_order is not None and lock_order in list_:
        if stall and state.next_player_index(receiver) == giver:
            return ClueInterp.STALL, game
        if prev.common.thinks_locked(prev, receiver):
            return ClueInterp.MISTAKE, game
        # Lock
        new_game = game
        if state.includes_variant(PINKISH):
            new_game = new_game.copy_with(
                common=new_game.common.with_thought(
                    lock_order,
                    lambda t, _r=clue.value: dataclasses.replace(
                        t, inferred=t.inferred.filter(lambda i, _rr=_r: i.rank == _rr)
                    ),
                )
            )
            new_meta = list(new_game.meta)
            new_meta[lock_order] = dataclasses.replace(new_meta[lock_order], focused=True)
            new_game = new_game.copy_with(meta=tuple(new_meta))
            lock_id = state.deck[lock_order].id()
            if lock_id is not None and lock_id.rank != clue.value:
                return None, game
        new_meta = list(new_game.meta)
        for o in hand:
            new_meta[o] = (
                dataclasses.replace(new_meta[o], status=CardStatus.CHOP_MOVED, by=giver)
                .reason(state.turn_count)
            )
        return ClueInterp.LOCK, new_game.copy_with(meta=tuple(new_meta))

    focus = max(newly_touched)
    focus_pos = hand.index(focus)
    target_index: int | None = None
    for i in range(focus_pos + 1, len(hand)):
        if not state.deck[hand[i]].clued:
            target_index = i
            break
    if target_index is None:
        return None, game

    promised_orders = [o for o in list_ if o > hand[target_index]]
    promised_order = min(promised_orders) if promised_orders else focus

    new_game = game
    if state.includes_variant(PINKISH):
        new_game = new_game.copy_with(
            common=new_game.common.with_thought(
                promised_order,
                lambda t, _r=clue.value: dataclasses.replace(
                    t, inferred=t.inferred.filter(lambda i, _rr=_r: i.rank == _rr)
                ),
            )
        )
        new_meta = list(new_game.meta)
        new_meta[promised_order] = dataclasses.replace(new_meta[promised_order], focused=True)
        new_game = new_game.copy_with(meta=tuple(new_meta))
        p_id = state.deck[promised_order].id()
        if p_id is not None and p_id.rank != clue.value:
            return None, game
    else:
        new_meta = list(new_game.meta)
        new_meta[focus] = dataclasses.replace(new_meta[focus], focused=True)
        new_game = new_game.copy_with(meta=tuple(new_meta))

    target_order = hand[target_index]
    new_meta = list(new_game.meta)
    new_meta[target_order] = (
        dataclasses.replace(new_meta[target_order], status=CardStatus.CALLED_TO_DISCARD, by=giver)
        .reason(state.turn_count)
        .signal(state.turn_count)
    )
    return ClueInterp.DISCARD, new_game.copy_with(meta=tuple(new_meta))


def try_stable(
    prev: Reactor, game: Reactor, action: ClueAction, stall: bool
) -> tuple[ClueInterp | None, Reactor]:
    """Try to interpret as a stable clue. Returns (interp, new_game).

    Port of interpretClue.scala `tryStable` (lines 38-212).
    """
    from .reactor import ReactorWC

    state = game.state
    giver = action.giver
    target = action.target
    list_ = action.list_
    clue = action.clue
    newly_touched = [o for o in list_ if not prev.state.deck[o].clued]
    next_player_index = state.next_player_index(giver)

    result_game = game
    if clue.kind == ClueKind.RANK and newly_touched:
        # Trash push: every suit's rank-clue is basic trash.
        trash_push = all(
            state.is_basic_trash(Identity(s, clue.value))
            for s in range(len(state.variant.suits))
        )
        playable_rank = all(
            state.is_basic_trash(Identity(s, clue.value))
            or state.is_playable(Identity(s, clue.value))
            for s in range(len(state.variant.suits))
        )
        if trash_push:
            focus = max(newly_touched)
            result_game = result_game.copy_with(
                common=result_game.common.with_thought(
                    focus,
                    lambda t, _ts=state.trash_set: dataclasses.replace(
                        t, inferred=t.inferred.intersect(_ts)
                    ),
                )
            )
            new_meta = list(result_game.meta)
            new_meta[focus] = dataclasses.replace(new_meta[focus], trash=True)
            result_game = result_game.copy_with(meta=tuple(new_meta))
        elif playable_rank:
            if state.includes_variant(PINKISH):
                touched_unclued = [
                    o for o in state.hands[target]
                    if not prev.state.deck[o].clued and o in list_
                ]
                focus = (
                    min(touched_unclued)
                    if touched_unclued
                    else max(newly_touched)
                )
            else:
                focus = max(newly_touched)

            unnecessary_focus = result_game.common.thoughts[focus].possible.forall(
                lambda i: state.is_basic_trash(i)
                or any(
                    result_game.common.thoughts[o].matches(i)
                    for hand in state.hands for o in hand
                )
            )
            if not unnecessary_focus:
                new_inferred = result_game.common.thoughts[focus].inferred.filter(
                    lambda i, _r=clue.value: state.is_playable(i) and i.rank == _r
                )
                result_game = result_game.copy_with(
                    common=result_game.common.with_thought(
                        focus,
                        lambda t, _ni=new_inferred: dataclasses.replace(
                            t,
                            inferred=_ni,
                            info_lock=_ni if _ni.non_empty else None,
                        ),
                    )
                )
                new_meta = list(result_game.meta)
                new_meta[focus] = dataclasses.replace(
                    new_meta[focus], focused=True, status=CardStatus.CALLED_TO_PLAY
                )
                result_game = result_game.copy_with(meta=tuple(new_meta))

    # Maybe set up a waiting connection (for response-inversion).
    if game.waiting is None and next_player_index != target:
        receiver = target
        focus_slot = _reactive_focus(state, receiver, action)
        wc = ReactorWC(
            giver=giver,
            reacter=next_player_index,
            receiver=receiver,
            receiver_hand=state.hands[receiver],
            clue=clue,
            focus_slot=focus_slot,
            inverted=True,
            turn=state.turn_count,
        )
        result_game = result_game.copy_with(waiting=wc)

    # Check fix
    fix_result = check_fix(prev, result_game, action)
    if isinstance(fix_result, FixResultNormal):
        return ClueInterp.FIX, result_game

    common = result_game.common
    prev_playables = list(
        dict.fromkeys(
            prev.common.obvious_playables(prev, target)
            + connectable_simple(prev, prev.players[giver], next_player_index, target)
        )
    )
    playables = list(
        dict.fromkeys(
            common.obvious_playables(result_game, target)
            + connectable_simple(
                result_game.with_move(ClueInterp.PLAY),
                result_game.players[giver],
                next_player_index,
                target,
            )
        )
    )

    def find_reveal() -> int | None:
        for o in playables:
            if (
                o in list_
                and o not in prev_playables
                and (clue.kind == ClueKind.RANK or prev.state.deck[o].clued)
            ):
                return o
        return None

    if not newly_touched:
        # Fill-in / hard-burn paths.
        safe_actions = playables + common.thinks_trash(result_game, target)
        old_safe_actions = prev_playables + prev.common.thinks_trash(prev, target)

        new_safe = [o for o in safe_actions if o not in old_safe_actions]
        if new_safe:
            return ClueInterp.REVEAL, result_game

        if stall:
            return ClueInterp.STALL, result_game

        connectable = connectable_simple(
            result_game.with_move(ClueInterp.REVEAL),
            result_game.common,
            state.next_player_index(giver),
            target,
        )
        connectable = [o for o in connectable if o not in old_safe_actions]
        if connectable:
            return ClueInterp.REVEAL, result_game

        # Try connecting through unknown playable
        focus_id = state.deck[max(list_)].id() if list_ else None
        if (
            focus_id is not None
            and next_player_index != target
            and state.playable_away(focus_id) == 1
        ):
            for o in prev.common.obvious_playables(prev, next_player_index):
                prev_id = focus_id.prev
                if prev_id is not None and prev_id in common.thoughts[o].inferred:
                    return ClueInterp.REVEAL, result_game.copy_with(
                        common=result_game.common.with_thought(
                            o,
                            lambda t, _pid=prev_id: dataclasses.replace(
                                t, inferred=IdentitySet.single(_pid)
                            ),
                        )
                    )
        return None, result_game

    revealed = find_reveal()
    if revealed is not None:
        return ClueInterp.REVEAL, result_game

    if common.order_kt(result_game, max(newly_touched)):
        brownish_tcm = (
            state.includes_variant(BROWNISH)
            and clue.kind == ClueKind.RANK
            and not prev.common.obvious_loaded(result_game, target)
            and any(
                s.suit_type.brownish
                and state.play_stacks[i] + 1 < state.max_ranks[i]
                and state.hands[target][0] not in newly_touched
                for i, s in enumerate(state.variant.suits)
            )
        )
        if brownish_tcm:
            return ClueInterp.REVEAL, result_game
        return ref_play(prev, result_game, action)
    if clue.kind == ClueKind.COLOUR:
        return ref_play(prev, result_game, action)
    return ref_discard(prev, result_game, action, stall)


def interpret_stable(
    prev: Reactor, game: Reactor, action: ClueAction, stall: bool
) -> tuple[ClueInterp | None, Reactor]:
    """Interpret as a stable clue. If the result looks bad, fall back to reactive.

    Port of interpretClue.scala `interpretStable` (lines 20-36).
    """
    state = game.state
    target = action.target
    bob = state.next_player_index(action.giver)
    interp, new_game = try_stable(prev, game, action, stall)

    if target != bob:
        actual_interp = interp if interp is not None else ClueInterp.MISTAKE
        if _bad_stable(prev, new_game, action, actual_interp, stall):
            # Build hypothetical game to feed into reactive interp.
            new_action_list = list(prev.state.action_list)
            # Append action to current turn
            tc = prev.state.turn_count
            while len(new_action_list) <= tc:
                new_action_list.append([])
            new_action_list[tc] = list(new_action_list[tc]) + [action]
            adjusted_state = dataclasses.replace(
                prev.state,
                action_list=tuple(tuple(t) for t in new_action_list),
            )
            hypo_game = prev.copy_with(state=adjusted_state).on_clue(action).elim()
            assert isinstance(hypo_game, type(prev))
            return interpret_reactive(prev, hypo_game, action, bob, looks_stable=True)

    return interp, new_game


def _alternative_clue(game: Reactor, clue_target: int, play_only: bool = False):
    """Find any non-bad-touching ref-play or trash ref-dc clue to clue_target.

    Port of interpretClue.scala `alternativeClue` (lines 217-245). Returns the clue or None.
    """
    if game.no_recurse:
        return None
    common = game.common
    state = game.state
    for clue in state.all_valid_clues(clue_target):
        list_ = state.clue_touched(state.hands[clue_target], clue.kind.value, clue.value)
        hand = state.hands[clue_target]
        newly_touched = [o for o in list_ if not state.deck[o].clued]
        if not newly_touched:
            continue
        if clue.kind == ClueKind.COLOUR:
            refs = [common.refer(game, hand, o, left=True) for o in newly_touched]
            play_target = max(refs)
            play_id = state.deck[play_target].id()
            if play_id is None or not state.is_playable(play_id):
                continue

            _nt = newly_touched
            _ck = clue.kind.value
            _cv = clue.value
            def all_useful(_nt=_nt) -> bool:
                return all(
                    (id_ := state.deck[o].id()) is not None and state.is_useful(id_)
                    for o in _nt
                )
            def all_basic_trash_poss(_nt=_nt, _ck=_ck, _cv=_cv) -> bool:
                from hanabi_bot.basics.identity import IdentitySet
                poss = IdentitySet.from_iter(
                    i for i in state.variant.all_ids()
                    if state.variant.id_touched(i, _ck, _cv)
                )
                return all(
                    common.thoughts[o].possible.intersect(poss).forall(state.is_basic_trash)
                    for o in _nt
                )
            if all_useful() or all_basic_trash_poss():
                return clue
        else:  # rank
            if play_only:
                continue
            unclued = [o for o in hand if not state.deck[o].clued]
            if not unclued or min(unclued) in list_:
                continue
            focus = max(newly_touched)
            focus_pos = hand.index(focus)
            target_index: int | None = None
            for i in range(focus_pos + 1, len(hand)):
                if not state.deck[hand[i]].clued:
                    target_index = i
                    break
            if target_index is None:
                continue
            id_ = state.deck[hand[target_index]].id()
            if id_ is None or not state.is_basic_trash(id_):
                continue
            if not all(
                (oid := state.deck[o].id()) is not None and state.is_useful(oid)
                for o in newly_touched
            ):
                continue
            return clue
    return None


def _bad_stable(
    prev: Reactor, game: Reactor, action: ClueAction, interp: ClueInterp, stall: bool = False
) -> bool:
    """Detect that a stable interpretation should be rejected (and reactive tried instead)."""
    state = game.state
    target = action.target

    if interp == ClueInterp.MISTAKE:
        return True
    if (
        prev.state.turn_count == 1
        and action.clue.kind == ClueKind.RANK
        and _alternative_clue(prev, target, play_only=True) is not None
    ):
        return True
    if target == state.our_player_index:
        return False

    bad_playable = None
    for hand in state.hands:
        for o in hand:
            if (
                game.meta[o].status == CardStatus.CALLED_TO_PLAY
                and not state.has_consistent_infs(game.common.thoughts[o])
                and (
                    prev.meta[o].status != CardStatus.CALLED_TO_PLAY
                    or prev.state.has_consistent_infs(prev.common.thoughts[o])
                )
            ):
                bad_playable = o
                break
        if bad_playable is not None:
            break
    if bad_playable is not None:
        return True

    bad_discard = None
    for o in state.hands[target]:
        if game.meta[o].status != CardStatus.CALLED_TO_DISCARD:
            continue
        if prev.meta[o].status == CardStatus.CALLED_TO_DISCARD:
            continue
        oid = state.deck[o].id()
        if oid is None:
            continue
        if state.is_critical(oid):
            bad_discard = o
            break
        if stall and state.is_useful(oid) and _alternative_clue(game, target) is not None:
            bad_discard = o
            break
    if bad_discard is not None:
        return True

    if interp == ClueInterp.LOCK and _alternative_clue(game, target) is not None:
        return True
    if not stall:
        return False
    return bool(interp == ClueInterp.STALL and _alternative_clue(game, target) is not None)


def interpret_reactive(
    prev: Reactor, game: Reactor, action: ClueAction, reacter: int, looks_stable: bool
) -> tuple[ClueInterp | None, Reactor]:
    """Top-level reactive clue interpretation. Dispatches to colour or rank impl.

    Port of interpretClue.scala `interpretReactive` (lines 291-318).
    """
    from .interpret_reactive import interpret_reactive_colour, interpret_reactive_rank
    from .reactor import ReactorWC

    state = game.state
    giver = action.giver
    receiver = action.target
    clue = action.clue

    focus_slot = _reactive_focus(state, receiver, action)
    wc = ReactorWC(
        giver=giver,
        reacter=reacter,
        receiver=receiver,
        receiver_hand=state.hands[receiver],
        clue=clue,
        focus_slot=focus_slot,
        inverted=False,
        turn=state.turn_count,
    )
    new_game = game.copy_with(waiting=wc)

    if receiver == state.our_player_index:
        return ClueInterp.REACTIVE, new_game
    if clue.kind == ClueKind.COLOUR:
        return interpret_reactive_colour(prev, new_game, action, focus_slot, reacter, looks_stable)
    return interpret_reactive_rank(prev, new_game, action, focus_slot, reacter)
