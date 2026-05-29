"""Sarcastic-discard interpretation.

Port of scala-bot/src/scala_bot/basics/sarcastic.scala.

When a useful card is discarded, conventions can interpret it as:
- Sarcastic: someone else holds the same identity; signal where
- Gentleman's Discard (gd): the discarded card was playable; chain of plays will reveal it
- Baton: a specific signalled order
- None: no interpretation (or the discarder believes there's a dupe in their own hand)
- Mistake: unexpected, no convention applies
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .card import CardStatus
from .identity import Identity

if TYPE_CHECKING:
    from .action import DiscardAction
    from .game import Game
    from .state import State


@dataclass(frozen=True, slots=True)
class DiscardResultNone:
    """No conventional interpretation (or discarder believed they had a dupe)."""


@dataclass(frozen=True, slots=True)
class DiscardResultMistake:
    """Unexpected discard — no convention applies."""


@dataclass(frozen=True, slots=True)
class DiscardResultSarcastic:
    """Sarcastic discard: one of `orders` holds the same identity."""

    orders: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DiscardResultGentlemansDiscard:
    """Gentleman's discard: the chain `orders` will play to reveal the identity."""

    orders: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DiscardResultBaton:
    """Baton discard: `order` is signalled."""

    order: int


DiscardResult = (
    DiscardResultNone
    | DiscardResultMistake
    | DiscardResultSarcastic
    | DiscardResultGentlemansDiscard
    | DiscardResultBaton
)


def valid_transfer(game: Game, id_: Identity, order: int) -> bool:
    """Whether `order` could plausibly receive a sarcastic transfer of `id_`.

    Conditions:
    - id_ is in `order`'s possible set
    - `order` isn't a known card of a connecting (lower-rank) identity
    - info_lock doesn't exclude id_
    """
    thought = game.common.thoughts[order]
    if id_ not in thought.possible:
        return False
    looked = thought.id(infer=True, symmetric=True)
    if looked is not None and looked.rank < id_.rank:
        return False
    return thought.info_lock is None or id_ in thought.info_lock


def interpret_useful_dc(game: Game, action: DiscardAction) -> DiscardResult:
    """Interpret a useful (non-trash) discard as Sarcastic, GD, or None/Mistake.

    Port of `interpretUsefulDc` in sarcastic.scala (lines 19-116).
    """
    state = game.state
    common = game.common
    id_ = Identity(action.suit_index, action.rank)
    gd = state.is_playable(id_)

    def find_gd(holder: int, hypo_state: State, connected: frozenset[int]) -> list[int] | None:
        """Find a gentleman's-discard chain in `holder`'s hand consuming the playable id_."""
        # Scan rightmost first (`findLast` in Scala).
        for o in reversed(state.hands[holder]):
            if o in connected:
                continue
            if id_ not in common.thoughts[o].possible:
                continue
            f = o
            # Determine the finesse identity.
            future_f = game.future[f] if f < len(game.future) else None
            if future_f is not None and future_f.length == 1:
                finesse_id: Identity | None = future_f.head
            else:
                finesse_id = game.me.thoughts[f].id()

            if finesse_id is None:
                return [f]
            if finesse_id == id_:
                return [f]
            if hypo_state.is_playable(finesse_id):
                rest = find_gd(holder, hypo_state.with_play(finesse_id), connected | {f})
                if rest is None:
                    return None
                return [f, *rest]
            return None
        return None

    def try_finding(excluding: frozenset[int]) -> DiscardResult:
        dupe: int | None = None
        for hand in state.hands:
            for o in hand:
                if o in excluding:
                    continue
                if game.order_matches(o, id_):
                    dupe = o
                    break
            if dupe is not None:
                break

        if dupe is not None:
            holder = state.holder_of(dupe)
            if holder == action.player_index:
                # The discarder's own hand has the dupe — maybe they didn't see it.
                if state.card_count[id_.to_ord()] - state.base_count[id_.to_ord()] > 1:
                    return try_finding(excluding | {dupe})
                return DiscardResultNone()
            if gd:
                gd_chain = find_gd(holder, state, frozenset())
                if gd_chain is None:
                    if state.card_count[id_.to_ord()] - state.base_count[id_.to_ord()] > 1:
                        return try_finding(excluding | {dupe})
                    return DiscardResultMistake()
                return DiscardResultGentlemansDiscard(tuple(gd_chain))
            orders = tuple(o for o in state.hands[holder] if valid_transfer(game, id_, o))
            return DiscardResultSarcastic(orders)

        # No visible dupe.
        if action.player_index == state.our_player_index:
            if game.meta[action.order].status == CardStatus.CALLED_TO_DISCARD:
                return DiscardResultNone()
            return DiscardResultMistake()

        if gd:
            # The dupe must be in our hand.
            gd_chain = find_gd(state.our_player_index, state, frozenset())
            if gd_chain is None:
                return DiscardResultMistake()
            linked = (
                len(gd_chain) == 1
                and state.deck[gd_chain[0]].clued
                and game.common.order_playable(game, gd_chain[0])
            )
            if linked:
                matching = tuple(
                    o for o in state.hands[state.our_player_index]
                    if state.deck[o].clued and id_ in common.thoughts[o].possible
                )
                return DiscardResultSarcastic(matching)
            return DiscardResultGentlemansDiscard(tuple(gd_chain))

        orders = tuple(o for o in state.our_hand if valid_transfer(game, id_, o))
        return DiscardResultSarcastic(orders)

    return try_finding(frozenset())
