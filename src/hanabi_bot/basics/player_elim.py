"""Empathy elimination algorithm (the hot path).

Port of scala-bot/src/scala_bot/basics/playerElim.scala.

Runs after every game action to propagate the consequences of new information
through every player's belief state.

Three layers of elimination:
- basic_elim: if all N copies of identity X are known to be at specific cards,
  remove X from every other card's possibilities.
- cross_elim ("sudoku" elim): find sets of cards whose joint possibilities lock
  in identities so those identities can be removed from cards outside the set.
- good_touch_elim: remove trash identities from clued cards (Good Touch
  Principle — assumes a teammate would not deliberately clue trash).

After elim:
- refresh_links / refresh_play_links resolve or simplify Link/PlayLink entries.
- find_links creates new Unpromised links from matching inferences.

The Scala source uses Scala's mutable BitSet + Array inside each function and
returns a new (immutable) Player at the end. We do the same in Python: collect
into mutable list/set/dict locally, then build a single replace() at the end.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .card import CardStatus
from .identity import Identity, IdentitySet
from .player import (
    MatchEntry,
    Player,
    PlayLink,
    PromisedLink,
    SarcasticLink,
    UnpromisedLink,
)

if TYPE_CHECKING:
    from .game import Game
    from .state import State


@dataclass(frozen=True, slots=True)
class CardElimResult:
    """Bundle of outputs from one elim pass.

    :param player: The post-pass Player (always present).
    :param changed: True if any thought was modified.
    :param removals: Orders that became fully known (single-possibility) — used by
                     cross_elim to drop them from the candidate set.
    :param resets: Orders whose inferred set became empty and got reset.
    :param recursive_ids: Identities to recurse on (newly-singleton possibilities).
    """

    player: Player
    changed: bool = False
    removals: frozenset[int] = field(default_factory=frozenset)
    resets: frozenset[int] = field(default_factory=frozenset)
    recursive_ids: IdentitySet = field(default_factory=IdentitySet.empty)

    def merge(self, other: CardElimResult) -> CardElimResult:
        return CardElimResult(
            player=other.player,
            changed=self.changed or other.changed,
            removals=self.removals | other.removals,
            resets=self.resets | other.resets,
            recursive_ids=self.recursive_ids.union(other.recursive_ids),
        )


# --- Low-level helpers ---


def _replace_or_prepend_entry(
    entries: tuple[MatchEntry, ...], order: int, new_entry: MatchEntry
) -> tuple[MatchEntry, ...]:
    """Replace the MatchEntry for `order` if present, else prepend `new_entry`."""
    for i, e in enumerate(entries):
        if e.order == order:
            return tuple(new_entry if j == i else entries[j] for j in range(len(entries)))
    return (new_entry, *entries)


def update_map(
    p: Player,
    state: State,
    id_: Identity,
    exclude: frozenset[int] = frozenset(),
    exclude_own: frozenset[int] = frozenset(),
) -> CardElimResult:
    """Remove `id_` from every eligible card's possibilities.

    A card is excluded if:
    - id_ isn't in its `possible` already
    - certain_map[id_] already accounts for this card OR this player can't see it
    - The card's order is in `exclude_own`
    `exclude` skips entire player hands (other than p's own hand).
    """
    changed = False
    recursive_ids: IdentitySet = IdentitySet.empty()
    cross_removals: set[int] = set()

    certain_map = list(p.certain_map)
    thoughts = list(p.thoughts)
    dirty = set(p.dirty)
    resets: set[int] = set()

    for player_index in range(state.num_players):
        if player_index != p.player_index and player_index in exclude:
            continue
        for order in state.hands[player_index]:
            thought = thoughts[order]
            if id_ not in thought.possible:
                continue
            if any(
                e.order == order or e.unknown_to == player_index
                for e in certain_map[id_.to_ord()]
            ):
                continue
            if order in exclude_own:
                continue

            changed = True
            new_inferred = thought.inferred.difference(id_)
            new_possible = thought.possible.difference(id_)
            reset_card = new_inferred.is_empty and not thought.reset

            if reset_card:
                # Reset re-derives `inferred` from `possible` (intersecting with info_lock if any).
                new_thought = dataclasses.replace(thought, possible=new_possible).reset_inferences()
            elif thought.info_lock is not None:
                new_lock_set = thought.info_lock.difference(id_)
                new_lock: IdentitySet | None = None if new_lock_set.is_empty else new_lock_set
                new_thought = dataclasses.replace(
                    thought, inferred=new_inferred, possible=new_possible, info_lock=new_lock
                )
            else:
                new_thought = dataclasses.replace(
                    thought, inferred=new_inferred, possible=new_possible
                )

            thoughts[order] = new_thought
            dirty.add(order)
            if reset_card:
                resets.add(order)

            # Newly singleton -> queue for recursion and add to certain_map.
            if new_possible.length == 1:
                rec_id = new_possible.head
                certain_map[rec_id.to_ord()] = _replace_or_prepend_entry(
                    certain_map[rec_id.to_ord()], order, MatchEntry(order, -1)
                )
                recursive_ids = recursive_ids.add(rec_id)
                cross_removals.add(order)

    new_player = dataclasses.replace(
        p,
        certain_map=tuple(certain_map),
        thoughts=tuple(thoughts),
        dirty=frozenset(dirty),
    )
    return CardElimResult(
        player=new_player,
        changed=changed,
        removals=frozenset(cross_removals),
        resets=frozenset(resets),
        recursive_ids=recursive_ids,
    )


def basic_elim(p: Player, state: State, ids: IdentitySet) -> CardElimResult:
    """For each id in `ids` whose copies are all known, remove id from every other card.

    Recursive: cards newly narrowed to a single possibility feed another round.
    Also shrinks `all_possible` by the identities that were eliminated.
    """
    res = CardElimResult(player=p)
    eliminated = IdentitySet.empty()

    for id_ in ids:
        known_count = len(res.player.certain_map[id_.to_ord()])
        if known_count == state.card_count[id_.to_ord()]:
            inner = update_map(res.player, state, id_)
            res = res.merge(inner)
            eliminated = eliminated.add(id_)

    if res.recursive_ids.non_empty:
        inner = basic_elim(res.player, state, res.recursive_ids)
        res = res.merge(inner)

    new_player = dataclasses.replace(
        res.player, all_possible=res.player.all_possible.difference(eliminated)
    )
    return dataclasses.replace(res, player=new_player)


def perform_cross_elim(
    p: Player,
    state: State,
    entries: frozenset[int],
    holders: frozenset[int],
    ids: IdentitySet,
) -> CardElimResult:
    """For a set of cards whose joint possibilities exactly cover `ids`, eliminate `ids` elsewhere.

    Also handles "naked pairs": if N cards all have the same identity, that identity is locked.
    """
    num_suits = len(state.variant.suits)
    groups: list[list[int]] = [[] for _ in range(num_suits * 5)]
    group_ids: IdentitySet = IdentitySet.empty()

    res = CardElimResult(player=p)

    # Group entries by known identity.
    for o in entries:
        id_ = state.deck[o].id()
        if id_ is not None:
            groups[id_.to_ord()].insert(0, o)
            group_ids = group_ids.add(id_)

    # Symmetric-info: if `group` exhausts the available copies of `id_`, eliminate id_ outside group.
    for id_ in group_ids:
        group = groups[id_.to_ord()]
        certains_outside = sum(
            1 for c in res.player.certain_map[id_.to_ord()] if c.order not in group
        )
        if len(group) == state.card_count[id_.to_ord()] - certains_outside:
            exclude_holders = frozenset(state.holder_of(o) for o in group)
            inner = update_map(res.player, state, id_, exclude=exclude_holders)
            res = res.merge(inner)

    # Naked-pair elim: remove `ids` from cards in `holders` (outside `entries`).
    own_hand_entries: frozenset[int] = frozenset()
    if not p.is_common:
        own_hand = set(state.hands[p.player_index])
        own_hand_entries = frozenset(e for e in entries if e in own_hand)

    for id_ in ids:
        inner = update_map(
            res.player, state, id_, exclude=holders, exclude_own=own_hand_entries
        )
        res = res.merge(inner)

    inner = basic_elim(res.player, state, ids)
    return res.merge(inner)


def cross_elim(
    p: Player,
    state: State,
    remaining: list[int],
    contained: frozenset[int] = frozenset(),
    holders: frozenset[int] = frozenset(),
    acc_ids: IdentitySet | None = None,
    certains: frozenset[int] = frozenset(),
) -> CardElimResult:
    """Recursively explore subsets of `remaining` for naked-pair-style elims.

    For each subset, if its joint possibilities exactly cover N identities with N cards,
    those identities can be eliminated from cards outside the subset.
    """
    if acc_ids is None:
        acc_ids = IdentitySet.empty()

    multiplicity = state.multiplicity(acc_ids)
    impossible = multiplicity - len(certains) > len(contained) + len(remaining)

    res = CardElimResult(player=p)
    if impossible:
        return res

    if len(contained) > 1 and multiplicity - len(certains) == len(contained):
        inner = perform_cross_elim(p, state, contained, holders, acc_ids)
        if inner.changed:
            return inner
        res = res.merge(inner)

    if not remaining:
        return res

    order = remaining[0]
    rest = remaining[1:]
    new_acc_ids = acc_ids.union(res.player.thoughts[order].possible)
    next_contained = contained | {order}

    # next_certains: union of certain_map entries for any newly added ids, minus next_contained itself.
    delta = res.player.thoughts[order].possible.difference(acc_ids)
    if delta.is_empty:
        all_certains = certains
    else:
        m_certains = set(certains)
        for id_ in delta:
            for c in res.player.certain_map[id_.to_ord()]:
                m_certains.add(c.order)
        all_certains = frozenset(m_certains)
    next_certains = all_certains - next_contained

    next_holders = holders | {state.holder_of(order)}
    inner = cross_elim(
        res.player, state, rest, next_contained, next_holders, new_acc_ids, next_certains
    )
    if inner.changed:
        return res.merge(inner)
    res = res.merge(inner)
    inner2 = cross_elim(res.player, state, rest, contained, holders, acc_ids, certains)
    return res.merge(inner2)


# --- Top-level entry points ---


def card_elim(p: Player, state: State) -> tuple[frozenset[int], Player]:
    """Run basic_elim + cross_elim across the player's dirty set.

    Returns (orders_that_were_reset, new_player). No-op if dirty is empty.
    """
    if not p.dirty:
        return (frozenset(), p)

    # Step 1: refresh certain_map for newly known cards in the dirty set.
    certain_map = list(p.certain_map)
    for order in p.dirty:
        thought = p.thoughts[order]
        id_ = thought.id(symmetric=p.is_common)
        if id_ is None:
            continue
        # unknown_to = -1 if everyone knows (i.e. symmetric=True still yields an id); else holder.
        unknown_to = -1 if thought.id(symmetric=True) is not None else state.holder_of(order)
        certains = certain_map[id_.to_ord()]
        idx = next((i for i, e in enumerate(certains) if e.order == order), -1)
        if idx != -1:
            # Promote to unknown_to=-1 if this card is now fully known (possible.length == 1).
            if thought.possible.length == 1 and certains[idx].unknown_to != -1:
                certain_map[id_.to_ord()] = tuple(
                    MatchEntry(order, -1) if i == idx else certains[i]
                    for i in range(len(certains))
                )
        else:
            certain_map[id_.to_ord()] = (MatchEntry(order, unknown_to), *certains)

    new_player = dataclasses.replace(p, certain_map=tuple(certain_map))

    # Step 2: basic elim across all known identities in the variant.
    basic_result = basic_elim(new_player, state, state.all_ids)
    new_player = basic_result.player
    resets = basic_result.resets

    # Step 3: collect cross-elim candidates (cards with multi-possibility, manageable multiplicity).
    cross_candidates: list[int] = []
    for player_index in range(state.num_players):
        for order in state.hands[player_index]:
            thought = new_player.thoughts[order]
            possible = thought.possible
            if possible.length > 1 and state.multiplicity(possible) <= 9:
                cross_candidates.insert(0, order)

    def keep(order: int) -> bool:
        thought = new_player.thoughts[order]
        certs: set[int] = set()
        for id_ in thought.possible:
            for c in new_player.certain_map[id_.to_ord()]:
                certs.add(c.order)
        return state.multiplicity(thought.possible) - len(certs) <= min(9, len(cross_candidates))

    candidates = [o for o in cross_candidates if keep(o)]

    # Step 4: iterate cross_elim until no more progress.
    changed = True
    while len(candidates) > 1 and changed:
        inner = cross_elim(new_player, state, candidates)
        changed = inner.changed
        candidates = [o for o in candidates if o not in inner.removals]
        resets = resets | inner.resets
        new_player = inner.player

    return (resets, new_player)


def good_touch_elim(
    p: Player, game: Game, except_: int | None = None
) -> tuple[frozenset[int], Player]:
    """Remove trash identities from clued/finessed/gd cards (Good Touch Principle).

    Skips player index `except_` (typically the giver of the current clue).
    Returns (orders_reset, new_player).
    """
    state = game.state

    def can_elim(order: int) -> bool:
        thought = p.thoughts[order]
        return (
            not game.meta[order].trash
            and game.meta[order].status != CardStatus.CALLED_TO_DISCARD
            and thought.id(symmetric=True) is None
            and not thought.inferred.is_empty
            and thought.possible.difference(state.trash_set).non_empty
            and game.is_touched(order)
        )

    dirty = set(p.dirty)
    resets: set[int] = set()
    new_thoughts = list(p.thoughts)

    for i in range(state.num_players):
        if except_ == i:
            continue
        for order in state.hands[i]:
            if not can_elim(order):
                continue
            thought = new_thoughts[order]
            new_inferred = thought.inferred.difference(state.trash_set)
            reset_card = new_inferred.is_empty and not thought.reset
            dirty.add(order)
            if reset_card:
                new_thoughts[order] = thought.reset_inferences()
                resets.add(order)
            else:
                new_thoughts[order] = dataclasses.replace(thought, inferred=new_inferred)

    new_player = dataclasses.replace(
        p, thoughts=tuple(new_thoughts), dirty=frozenset(dirty)
    )
    return (frozenset(resets), new_player)


# --- Link maintenance ---


def elim_link(
    p: Player, game: Game, matches: list[int], focus: int, id_: Identity
) -> Player:
    """Resolve a link: focus gets inferred=single(id_); others lose id_ from inferred."""
    new_thoughts = list(p.thoughts)
    for order in matches:
        thought = new_thoughts[order]
        new_inferred = (
            IdentitySet.single(id_) if order == focus else thought.inferred.difference(id_)
        )
        if new_inferred.is_empty and not thought.reset:
            new_thoughts[order] = thought.reset_inferences()
        else:
            new_thoughts[order] = dataclasses.replace(thought, inferred=new_inferred)
    return dataclasses.replace(
        p, thoughts=tuple(new_thoughts), dirty=p.dirty | frozenset(matches)
    )


def find_links(p: Player, game: Game) -> Player:
    """Scan each hand for unlinked cards with matching inferences and create Unpromised links.

    A "linkable" card has unknown symmetric identity, <=2 inferences, at least one non-trash
    inference, and isn't already in a link.
    """
    state = game.state

    def linkable(order: int) -> bool:
        thought = p.thoughts[order]
        if thought.id(symmetric=True) is not None:
            return False
        if thought.inferred.length > 2:
            return False
        if thought.inferred.difference(state.trash_set).is_empty:
            return False
        return not any(order in link.orders for link in p.links)

    new_player = p

    for hand in state.hands:
        # Group hand orders by their inferred IdentitySet.
        inf_map: dict[IdentitySet, list[int]] = {}
        for o in hand:
            if not linkable(o):
                continue
            infs = new_player.thoughts[o].inferred
            inf_map.setdefault(infs, []).insert(0, o)

        for inferred, orders in inf_map.items():
            if len(orders) <= 1:
                continue
            focused = [o for o in orders if game.meta[o].focused]
            if len(focused) == 1 and inferred.length == 1:
                new_player = elim_link(new_player, game, orders, focused[0], inferred.head)
            elif len(orders) > inferred.length:
                new_link = UnpromisedLink(orders=tuple(orders), ids=inferred)
                new_player = dataclasses.replace(
                    new_player, links=(new_link, *new_player.links)
                )

    return new_player


def refresh_links(p: Player, game: Game) -> tuple[list[int], Player]:
    """Walk every Link in p.links; resolve, simplify, or drop. Then re-run find_links.

    Returns (orders_resolved_via_sarcastic, new_player).
    """
    state = game.state
    new_player = dataclasses.replace(p, links=())
    sarcastics: list[int] = []

    # Original Scala does foldRight, so we walk in reverse and prepend to preserve order.
    for link in reversed(p.links):
        match link:
            case PromisedLink(orders=orders, id=id_, target=target):
                # Resolved if any card already matches symmetrically.
                if any(new_player.thoughts[o].matches(id_, symmetric=True) for o in orders):
                    continue
                # Target lost the relevant suit -> link no longer relevant.
                if not any(
                    i.suit_index == id_.suit_index for i in new_player.thoughts[target].possible
                ):
                    continue
                viable = [o for o in orders if id_ in new_player.thoughts[o].possible]
                if not viable:
                    continue
                if len(viable) == 1:
                    new_player = new_player.with_thought(
                        viable[0],
                        lambda t, _id=id_: dataclasses.replace(t, inferred=IdentitySet.single(_id)),
                    )
                else:
                    new_player = dataclasses.replace(
                        new_player,
                        links=(PromisedLink(tuple(viable), id_, target), *new_player.links),
                    )

            case SarcasticLink(orders=orders, id=id_):
                if any(new_player.thoughts[o].matches(id_) for o in orders):
                    continue
                viable = [o for o in orders if id_ in new_player.thoughts[o].possible]
                if not viable:
                    continue
                if len(viable) == 1:
                    o = viable[0]
                    new_player = new_player.with_thought(
                        o,
                        lambda t, _id=id_: dataclasses.replace(t, inferred=IdentitySet.single(_id)),
                    )
                    sarcastics.insert(0, o)
                else:
                    new_player = dataclasses.replace(
                        new_player,
                        links=(SarcasticLink(tuple(viable), id_), *new_player.links),
                    )

            case UnpromisedLink(orders=orders, ids=ids):
                in_play = set(o for hand in state.hands for o in hand)
                revealed = False
                for o in orders:
                    thought = new_player.thoughts[o]
                    if thought.id(symmetric=True) is not None:
                        revealed = True
                        break
                    if any(i not in thought.possible for i in ids):
                        revealed = True
                        break
                    if o not in in_play:
                        revealed = True
                        break
                if revealed:
                    continue
                focused = [o for o in orders if game.meta[o].focused]
                if len(focused) == 1 and ids.length == 1:
                    new_player = elim_link(
                        new_player, game, list(orders), focused[0], ids.head
                    )
                else:
                    lost = any(
                        any(i not in new_player.thoughts[o].inferred for o in orders)
                        for i in ids
                    )
                    if not lost:
                        new_player = dataclasses.replace(
                            new_player, links=(link, *new_player.links)
                        )

    return (sarcastics, find_links(new_player, game))


def refresh_play_links(p: Player, game: Game) -> Player:
    """For each PlayLink: if all source cards are no longer in any hand, target is playable."""
    new_player = dataclasses.replace(p, play_links=())
    in_play = set(o for hand in game.state.hands for o in hand)

    for pl in reversed(p.play_links):
        rem = tuple(o for o in pl.orders if o in in_play)
        if not rem:
            new_player = new_player.with_thought(
                pl.target,
                lambda t, _ps=game.state.playable_set: dataclasses.replace(
                    t, inferred=t.inferred.intersect(_ps)
                ),
            )
        else:
            new_player = dataclasses.replace(
                new_player,
                play_links=(PlayLink(rem, pl.prereqs, pl.target), *new_player.play_links),
            )

    return new_player
