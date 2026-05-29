"""Player perspective: the belief state of a particular observer.

Port of scala-bot/src/scala_bot/basics/Player.scala — data structure and the
methods that do not depend on `Game` or the elimination algorithms.

DEFERRED to Stage 2b:
- Elimination methods (cardElim, goodTouchElim, basicElim, crossElim, refreshLinks,
  refreshPlayLinks, findLinks, elimLink) — those land in player_elim.py.
- Game-dependent predicates (isSieved, isDuped, isTrash, orderTrash, orderKt,
  orderKp, orderPlayable, thinksPlayables, thinksLoaded, thinksLocked,
  obviousLoaded, obviousLocked, validPrompt, findPrompt, findClued, updateHypoStacks,
  discardable, refer, chopNewest, anxietyPlay, lockedDiscard).

MatchEntry lives here (not in player_elim.py) because certain_map is a Player
field whose type definition is needed at module load time.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .card import CardStatus, Thought
from .clue import ClueKind
from .identity import Identity, IdentitySet
from .variant import PINKISH

if TYPE_CHECKING:
    from .game import Game
    from .state import State


@dataclass(frozen=True, slots=True)
class MatchEntry:
    """An entry in Player.certain_map[id.to_ord()] — a card known to be a given identity.

    :param order: Card order.
    :param unknown_to: Index of the player who DOESN'T know this card's identity
                       (e.g. it's in their own hand). -1 if everyone knows.
    """

    order: int
    unknown_to: int


# --- Link ADT: relates a set of cards to one (or more) identities. ---


@dataclass(frozen=True, slots=True)
class PromisedLink:
    """One of these cards must be the promised identity (e.g. after a finesse on r2).

    :param orders: Orders that could be the identity.
    :param id: The promised identity.
    :param target: Order of the card this link enables.
    """

    orders: tuple[int, ...]
    id: Identity
    target: int

    @property
    def promise(self) -> Identity:
        return self.id


@dataclass(frozen=True, slots=True)
class SarcasticLink:
    """Created from a sarcastic discard: one of these cards must be the discarded identity."""

    orders: tuple[int, ...]
    id: Identity

    @property
    def promise(self) -> Identity:
        return self.id


@dataclass(frozen=True, slots=True)
class UnpromisedLink:
    """From good-touch: each of these cards could be one of these identities, but maybe none are.

    Example: two blue cards when the blue stack is at 4 — between them they hold r5,
    but each individually might not be.
    """

    orders: tuple[int, ...]
    ids: IdentitySet

    @property
    def promise(self) -> Identity | None:
        return None


Link = PromisedLink | SarcasticLink | UnpromisedLink


def link_orders(link: Link) -> tuple[int, ...]:
    return link.orders


def link_promise(link: Link) -> Identity | None:
    return link.promise


@dataclass(frozen=True, slots=True)
class PlayLink:
    """If all `orders` are played, `target` is known playable.

    Example: multiple clued 1s and a play-clue on a 2 in our hand.
    """

    orders: tuple[int, ...]
    prereqs: IdentitySet
    target: int


@dataclass(frozen=True, slots=True)
class Player:
    """One observer's belief state.

    player_index = -1 indicates the "common" perspective (what everyone knows
    everyone knows). Otherwise it's a real player's perspective from the bot's POV.
    """

    player_index: int
    name: str
    all_possible: IdentitySet
    hypo_stacks: tuple[int, ...]

    is_common: bool
    thoughts: tuple[Thought, ...] = ()

    links: tuple[Link, ...] = ()
    play_links: tuple[PlayLink, ...] = ()
    # Orders known to be playable, without their identities pinned.
    unknown_plays: frozenset[int] = frozenset()
    # Orders known to be (delayed) playable.
    hypo_plays: frozenset[int] = frozenset()
    # Total number of hypothetical plays from links (not attributable to a specific order).
    linked_plays: int = 0

    # Orders whose inferences have been modified since the last elim(). Cleared by elim().
    dirty: frozenset[int] = frozenset()
    # certain_map[id.to_ord()] = entries for cards known to be that identity (or empty).
    certain_map: tuple[tuple[MatchEntry, ...], ...] = ()

    @classmethod
    def create(
        cls,
        player_index: int,
        name: str,
        all_possible: IdentitySet,
        hypo_stacks: tuple[int, ...],
    ) -> Player:
        """Build a fresh Player. is_common is derived from player_index == -1.

        Port of `object Player { def apply(...) }` (Player.scala lines 487-501).
        """
        # certain_map is sized to the number of possible identities (= num_suits * 5
        # at construction, since all_possible starts as the full variant deck).
        size = all_possible.length
        return cls(
            player_index=player_index,
            name=name,
            all_possible=all_possible,
            hypo_stacks=hypo_stacks,
            is_common=(player_index == -1),
            certain_map=tuple(() for _ in range(size)),
        )

    # --- Updates ---

    def with_thought(self, order: int, f) -> Player:  # type: ignore[no-untyped-def]
        """Apply `f: Thought -> Thought` to the thought at `order`, marking it dirty."""
        new_thought = f(self.thoughts[order])
        new_thoughts = (*self.thoughts[:order], new_thought, *self.thoughts[order + 1:])
        return dataclasses.replace(self, thoughts=new_thoughts, dirty=self.dirty | {order})

    # --- Display helpers ---

    def str_infs(self, state: State, order: int) -> str:
        """Comma-separated inference string, e.g. 'r1,r4,r5'."""
        return ",".join(state.log_id(i) for i in sorted(self.thoughts[order].inferred, key=Identity.to_ord))

    def str_poss(self, state: State, order: int) -> str:
        """Comma-separated possible-ids string."""
        return ",".join(state.log_id(i) for i in sorted(self.thoughts[order].possible, key=Identity.to_ord))

    # --- Pure helpers that don't touch Game ---

    def playable_away(self, id_: Identity) -> int:
        """How many plays away the identity is, using this player's hypo stacks. 0 means playable now."""
        return id_.rank - (self.hypo_stacks[id_.suit_index] + 1)

    def unknown_ids(self, state: State, id_: Identity) -> int:
        """How many copies of `id` are unseen from this perspective."""
        visible = sum(1 for hand in state.hands for o in hand if self.thoughts[o].id() == id_)
        return state.card_count[id_.to_ord()] - state.base_count[id_.to_ord()] - visible

    def linked_orders(self, state: State) -> list[int]:
        """Orders that are part of a still-active link (more than the available unseen copies)."""
        out: list[int] = []
        for link in self.links:
            match link:
                case PromisedLink(orders=orders, id=id_):
                    if len(orders) > self.unknown_ids(state, id_):
                        out.extend(orders)
                case SarcasticLink(orders=orders, id=id_):
                    if len(orders) > self.unknown_ids(state, id_):
                        out.extend(orders)
                case UnpromisedLink(orders=orders, ids=ids):
                    total = sum(self.unknown_ids(state, i) for i in ids)
                    if len(orders) > total:
                        out.extend(orders)
        return out

    @property
    def hypo_score(self) -> int:
        """Score if all delayed playable cards were played."""
        return sum(self.hypo_stacks) + len(self.unknown_plays) - self.linked_plays

    # ----------------------------------------------------------------
    # Game-dependent methods (Stage 2b). All take a Game as the first arg.
    # ----------------------------------------------------------------

    def refer(self, game: Game, hand: tuple[int, ...], order: int, left: bool = False) -> int:
        """Return the order in `hand` "referred to" by `order`, skipping touched cards.

        Defaults to referring right (next-newer slot). Walks around the hand if needed.
        """
        offset = -1 if left else 1
        index = hand.index(order)
        target = (index + offset + len(hand)) % len(hand)
        while game.is_touched(hand[target]) and target != index:
            target = (target + offset + len(hand)) % len(hand)
        return hand[target]

    def chop_newest(self, game: Game, player_index: int) -> int | None:
        """Newest unclued card with no conventional status, or None if none."""
        for o in game.state.hands[player_index]:
            if not game.state.deck[o].clued and game.meta[o].status == CardStatus.NONE:
                return o
        return None

    def is_duped(self, game: Game, id_: Identity, exclude_order: int) -> bool:
        """True if id_ has a duplicate (excluding exclude_order).

        For non-good-touch conventions, duplicate must be in same hand.
        For good-touch, duplicate must be a touched card anywhere.
        Ignores duplicates that are already tracked by a shared link.
        """
        if game.good_touch:
            candidates: list[int] = [o for hand in game.state.hands for o in hand if game.is_touched(o)]
        else:
            candidates = list(game.state.hands[game.state.holder_of(exclude_order)])

        for o in candidates:
            if o == exclude_order:
                continue
            if not self.thoughts[o].matches(id_, infer=True):
                continue
            if not game.state.deck[o].matches(id_, assume=True):
                continue
            # If a link explains this duplicate, skip.
            shared = False
            for link in self.links:
                match link:
                    case UnpromisedLink(orders=orders, ids=ids):
                        if exclude_order in orders and o in orders and id_ in ids:
                            shared = True
                            break
                    case _:
                        if exclude_order in link.orders and o in link.orders and link.promise == id_:
                            shared = True
                            break
            if not shared:
                return True
        return False

    def is_trash(self, game: Game, id_: Identity, exclude_order: int) -> bool:
        """Either basic trash or duplicated elsewhere."""
        return game.state.is_basic_trash(id_) or self.is_duped(game, id_, exclude_order)

    def order_kt(self, game: Game, order: int) -> bool:
        """Known-trash: every possibility is either basic trash or has a same-hand duplicate.

        Also true if the conv-data marks this as trash AND no possibility is critical.
        """
        thought = self.thoughts[order]
        holder = game.state.holder_of(order)

        def same_hand_dupe(id_: Identity) -> bool:
            if game.state.is_basic_trash(id_):
                return True
            return any(o != order and self.thoughts[o].matches(id_) for o in game.state.hands[holder])

        meta_says_trash = game.meta[order].trash and thought.possible.forall(
            lambda i: not game.state.is_critical(i)
        )
        return meta_says_trash or thought.possible.forall(same_hand_dupe)

    def order_trash(self, game: Game, order: int) -> bool:
        """True if this order can safely be discarded by *this player*'s reasoning."""
        meta = game.meta[order]
        thought = self.thoughts[order]

        if self.order_kt(game, order):
            return True
        if thought.possible.forall(game.state.is_critical):
            return False
        conventional_trash = (
            thought.possible.forall(lambda i, _o=order: self.is_trash(game, i, _o))
            or (
                thought.info_lock is not None
                and thought.info_lock.forall(lambda i, _o=order: self.is_trash(game, i, _o))
            )
            or meta.trash
            or meta.status == CardStatus.CALLED_TO_DISCARD
            or meta.status == CardStatus.PERMISSION_TO_DISCARD
        )
        return conventional_trash or thought.possibilities.forall(
            lambda i, _o=order: self.is_trash(game, i, _o)
        )

    def order_kp(self, game: Game, order: int, exclude_trash: bool = False) -> bool:
        """Known-playable: card status implies the card is playable to this perspective."""
        state = game.state
        thought = self.thoughts[order]

        if thought.possible.forall(lambda i: not state.is_playable(i)):
            return False

        def poss_playable(poss: IdentitySet) -> bool:
            p = poss.difference(state.trash_set) if exclude_trash else poss
            return p.non_empty and p.intersect(state.playable_set) == p

        status = game.meta[order].status
        if status == CardStatus.CALLED_TO_PLAY:
            return (
                thought.possible.intersect(state.playable_set).non_empty
                and (
                    thought.info_lock is None
                    or thought.info_lock.intersect(state.playable_set).non_empty
                )
            )
        if status in (CardStatus.SARCASTIC, CardStatus.GENTLEMANS_DISCARD):
            return poss_playable(thought.inferred)
        return poss_playable(thought.possible) or (
            thought.info_lock is not None and poss_playable(thought.info_lock)
        )

    def order_playable(self, game: Game, order: int, exclude_trash: bool = False) -> bool:
        """Obviously playable (order_kp) OR inferred playable from current possibilities."""
        state = game.state
        if self.order_kp(game, order, exclude_trash):
            return True
        if game.meta[order].trash:
            return False
        infer = game.meta[order].status != CardStatus.CALLED_TO_DISCARD
        poss = self.thoughts[order].possibilities if infer else self.thoughts[order].possible
        p = poss.difference(state.trash_set) if exclude_trash else poss
        return p.non_empty and p.intersect(state.playable_set) == p

    def obvious_playables(self, game: Game, player_index: int) -> list[int]:
        """Cards known to be playable (without inference), filtered by the convention hook."""
        candidates = [o for o in game.state.hands[player_index] if self.order_kp(game, o)]
        return game.filter_playables(self, player_index, candidates)

    def thinks_playables(
        self, game: Game, player_index: int, exclude_trash: bool = False, assume: bool = True
    ) -> list[int]:
        """All orders this perspective thinks are playable, deduped + filtered."""
        candidates = [
            o for o in game.state.hands[player_index]
            if self.order_playable(game, o, exclude_trash=(exclude_trash and game.is_touched(o)))
        ]
        # Exclude unknown cards that have a fully-known duplicate among the candidates.
        filtered: list[int] = []
        for p1 in candidates:
            if self.thoughts[p1].id() is None:
                duplicated = False
                for p2 in candidates:
                    if p1 == p2:
                        continue
                    p2_id = self.thoughts[p2].id()
                    if p2_id is not None and self.thoughts[p1].matches(p2_id, infer=True):
                        duplicated = True
                        break
                if duplicated:
                    continue
            filtered.append(p1)
        return game.filter_playables(self, player_index, filtered, assume)

    def thinks_trash(self, game: Game, player_index: int) -> list[int]:
        return [o for o in game.state.hands[player_index] if self.order_trash(game, o)]

    def thinks_loaded(self, game: Game, player_index: int) -> bool:
        return bool(self.thinks_playables(game, player_index)) or bool(self.thinks_trash(game, player_index))

    def thinks_locked(self, game: Game, player_index: int) -> bool:
        if self.thinks_loaded(game, player_index):
            return False
        for order in game.state.hands[player_index]:
            status = game.meta[order].status
            if game.state.deck[order].clued:
                continue
            if status == CardStatus.NONE:
                return False
            if status == CardStatus.CALLED_TO_DISCARD:
                return False
            if status == CardStatus.FINESSED and game.meta[order].hidden:
                return False
        return True

    def obvious_loaded(self, game: Game, player_index: int) -> bool:
        return bool(self.obvious_playables(game, player_index)) or bool(self.thinks_trash(game, player_index))

    def obvious_locked(self, game: Game, player_index: int) -> bool:
        if self.obvious_loaded(game, player_index):
            return False
        for order in game.state.hands[player_index]:
            status = game.meta[order].status
            if game.state.deck[order].clued:
                continue
            if status == CardStatus.NONE or status == CardStatus.CALLED_TO_DISCARD:
                return False
        return True

    def is_sieved(self, game: Game, id_: Identity, exclude_order: int) -> bool:
        """True if id_ is visible AND won't be discarded (in someone's hand or accounted for by a link)."""
        for player_index in range(game.state.num_players):
            loaded = self.thinks_loaded(game, player_index)
            chop = self.chop_newest(game, player_index)
            for o in game.state.hands[player_index]:
                if o == exclude_order:
                    continue
                if not self.thoughts[o].matches(id_, infer=True):
                    continue
                if loaded:
                    if game.meta[o].status != CardStatus.CALLED_TO_DISCARD:
                        return True
                elif chop != o:
                    return True
        for link in self.links:
            match link:
                case UnpromisedLink(orders=orders, ids=ids):
                    if exclude_order not in orders and id_ in ids:
                        return True
                case _:
                    if exclude_order not in link.orders and link.promise == id_:
                        return True
        return False

    def discardable(
        self, game: Game, player_index: int, allow_locked_sacrifice: bool = False
    ) -> list[int]:
        """Orders that could be discarded — not necessarily trash, but acceptable."""
        result: list[int] = []
        for order in game.state.hands[player_index]:
            if self.order_trash(game, order):
                result.append(order)
                continue
            if self.thoughts[order].possibilities.forall(
                lambda i, _o=order: self.is_sieved(game, i, _o)
            ):
                result.append(order)
                continue
            if (
                allow_locked_sacrifice
                and game.common.thinks_locked(game, player_index)
                and game.state.deck[order].clued
                and self.thoughts[order].possibilities.intersect(game.state.critical_set).is_empty
            ):
                result.append(order)
        return result

    def valid_prompt(
        self,
        prev: Game,
        order: int,
        id_: Identity,
        connected: frozenset[int] = frozenset(),
        force_pink: bool = False,
    ) -> bool:
        """Whether this card can serve as a valid prompt for id_."""
        state = prev.state
        card = state.deck[order]
        thought = self.thoughts[order]

        if order in connected:
            return False
        if not card.clued:
            return False
        if id_ not in thought.possible:
            return False
        if thought.info_lock is not None and id_ not in thought.info_lock:
            return False
        if thought.inferred.length == 1 and id_ not in thought.inferred:
            return False
        if not any(state.variant.id_touched(id_, c.kind.value, c.value) for c in card.clues):
            return False

        # Pink-prompt exception
        if state.variant.suits[id_.suit_index].suit_type.pinkish and not force_pink:
            clues = card.clues
            if clues:
                head = clues[0]
                misranked = (
                    all(c.kind == head.kind and c.value == head.value for c in clues)
                    and head.kind == ClueKind.RANK
                    and head.value != id_.rank
                )
                if misranked or not prev.known_as(order, PINKISH):
                    return False
        return True

    def find_prompt(
        self,
        prev: Game,
        player_index: int,
        id_: Identity,
        connected: frozenset[int] = frozenset(),
        ignore: frozenset[int] = frozenset(),
        force_pink: bool = False,
        rightmost: bool = False,
    ) -> int | None:
        """The card to prompt for id_, or None if no valid prompt exists."""
        state = prev.state
        hand = (
            list(reversed(state.hands[player_index]))
            if rightmost
            else list(state.hands[player_index])
        )
        valid = [o for o in hand if self.valid_prompt(prev, o, id_, connected, force_pink)]
        if not valid:
            return None
        # Prompt the card with the most distinct positive-clue kinds/values.
        def positive_clue_count(o: int) -> int:
            return len({(c.kind, c.value) for c in state.deck[o].clues})
        best = max(valid, key=positive_clue_count)
        if best in ignore:
            return None
        return best

    def find_clued(
        self,
        prev: Game,
        player_index: int,
        id_: Identity,
        ignore: frozenset[int] = frozenset(),
    ) -> list[int]:
        """Clued orders in player_index's hand that could be id_."""
        state = prev.state
        result: list[int] = []
        for order in state.hands[player_index]:
            if order in ignore:
                continue
            if not state.deck[order].clued:
                continue
            thought = self.thoughts[order]
            if id_ not in thought.possible:
                continue
            if thought.info_lock is not None and id_ not in thought.info_lock:
                continue
            if thought.inferred.length == 1 and id_ not in thought.inferred:
                continue
            result.append(order)
        return result

    def locked_discard(self, state: State, player_index: int) -> int:
        """Pick the card least likely to be critical to discard from a locked hand."""
        hand = state.hands[player_index]
        crit_percents: list[tuple[int, float]] = []
        for o in hand:
            poss = self.thoughts[o].possibilities
            percent = (
                poss.intersect(state.critical_set).length / poss.length if poss.length else 0.0
            )
            crit_percents.append((o, percent))
        crit_percents.sort(key=lambda t: t[1])
        min_percent = crit_percents[0][1]
        least_crits = [t for t in crit_percents if t[1] == min_percent]

        def total_score(t: tuple[int, float]) -> int:
            order, percent = t
            total = 0
            for p in self.thoughts[order].possibilities:
                if state.is_basic_trash(p):
                    total += 5
                else:
                    extra = p.rank * 5 if percent == 1.0 else 0
                    total += extra + p.rank - self.hypo_stacks[p.suit_index]
            return total

        return max(least_crits, key=total_score)[0]

    def anxiety_play(self, state: State, player_index: int) -> int | None:
        """Pick the card most likely to be playable, breaking ties by leftmost. None if no playable."""
        hand = state.hands[player_index]
        if not any(
            self.thoughts[o].possibilities.intersect(state.playable_set).non_empty for o in hand
        ):
            return None

        def score(idx_o: tuple[int, int]) -> float:
            i, o = idx_o
            poss = self.thoughts[o].possibilities
            if poss.length == 0:
                return -i
            percent = poss.intersect(state.playable_set).length / poss.length
            return percent * 1000 - i

        return max(enumerate(hand), key=score)[1]

    def update_hypo_stacks(self, game: Game) -> Player:
        """Run a hypothetical play loop driven by this perspective's thinks_playables.

        Repeatedly: for each player, find a card this perspective thinks is playable
        and not yet played in the hypo; "play" it on the hypo game; loop until no
        further progress. Handles linked plays (cards with unknown identity that a
        Link promises must be a specific id once siblings play).

        Returns a new Player with hypo_stacks/unknown_plays/hypo_plays/linked_plays updated.
        """
        from .action import PlayAction  # local import: action.py doesn't depend on player.py

        # Install self into a hypothetical game and mark no_recurse to prevent further hypos.
        if self.is_common:
            hypo = game.copy_with(common=self, no_recurse=True)
        else:
            new_players = (
                *game.players[: self.player_index],
                self,
                *game.players[self.player_index + 1:],
            )
            hypo = game.copy_with(players=new_players, no_recurse=True)
        hypo = hypo.clean_hypo()  # convention hook; default = identity

        unknown_plays: set[int] = set()
        played: set[int] = set()
        attempted: set[int] = set()
        linked_plays = 0

        def get_player() -> Player:
            return hypo.common if self.is_common else hypo.players[self.player_index]

        def play_order(order: int) -> None:
            nonlocal hypo, linked_plays
            holder = hypo.state.holder_of(order)
            id_ = get_player().thoughts[order].id(infer=True)

            if id_ is None:
                # Linked play: if all of this link's siblings have played, the promise resolves.
                for link in self.links:
                    if order in link.orders and all(
                        o == order or o in played for o in link.orders
                    ):
                        promise = link.promise
                        if isinstance(promise, Identity) and hypo.state.is_playable(promise):
                            linked_plays += 1
                            hypo = hypo.with_state(lambda s, _p=promise: s.with_play(_p))
                unknown_plays.add(order)
                played.add(order)
            elif hypo.state.is_playable(id_):
                play_action = PlayAction(
                    player_index=holder, order=order, suit_index=id_.suit_index, rank=id_.rank
                )
                prev_hypo = hypo
                hypo = prev_hypo.on_play(play_action)
                hypo = hypo.refresh_after_play(prev_hypo, play_action)
                played.add(order)
            else:
                attempted.add(order)

            # Whether played or attempted, remove from holder's hand in the hypo state.
            def _drop(s: State, _o=order, _h=holder) -> State:
                new_hand = tuple(o for o in s.hands[_h] if o != _o)
                new_hands = (*s.hands[:_h], new_hand, *s.hands[_h + 1:])
                return dataclasses.replace(s, hands=new_hands)
            hypo = hypo.with_state(_drop)

        # Outer loop: keep going while progress is being made.
        changed = True
        while changed:
            changed = False
            player = get_player()
            # Inner: walk each player's hand, try to play one card per pass, restart on success.
            outer_break = False
            for i in range(hypo.state.num_players):
                if game.good_touch:
                    playables = player.thinks_playables(hypo, i, exclude_trash=True)
                else:
                    playables = player.obvious_playables(hypo, i)
                for o in playables:
                    if o in played or o in attempted:
                        continue
                    if not hypo.state.has_consistent_infs(self.thoughts[o]):
                        continue
                    play_order(o)
                    changed = True
                    outer_break = True
                    break
                if outer_break:
                    break
            if changed:
                continue

            # No card plays this round — try play_links.
            player = get_player()
            for link in player.play_links:
                if all(o in played for o in link.orders) and link.target not in played:
                    in_play = any(link.target in hand for hand in hypo.state.hands)
                    if in_play:
                        target_id = hypo.state.deck[link.target].id()
                        if target_id is None or hypo.state.is_useful(target_id):
                            play_order(link.target)
                            changed = True
                            break

        return dataclasses.replace(
            self,
            hypo_stacks=hypo.state.play_stacks,
            unknown_plays=frozenset(unknown_plays),
            hypo_plays=frozenset(played),
            linked_plays=linked_plays,
        )


def visible_find(state: State, player: Player, id_: Identity, exclude_order: int = -1) -> list[int]:
    """All orders whose card matches `id_` from `player`'s perspective AND in the real deck.

    Used by sarcastic/eval logic. Port of scala-bot utils.visibleFind.
    """
    return [
        o
        for hand in state.hands
        for o in hand
        if o != exclude_order
        and player.thoughts[o].matches(id_, infer=True)
        and state.deck[o].matches(id_, assume=True)
    ]


def players_until(num_players: int, start: int, target: int) -> list[int]:
    """Iterate player indices from `start` up to (but not including) `target`, mod num_players."""
    result: list[int] = []
    i = start
    while i != target:
        result.append(i)
        i = (i + 1) % num_players
    return result


def gen_players(state: State) -> tuple[tuple[Player, ...], Player]:
    """Build the per-player and common-perspective Players for a fresh State.

    Port of `genPlayers` in scala-bot/.../Game.scala lines 66-72.
    Returns (players, common).
    """
    all_possible = state.all_ids
    hypo_stacks = tuple(0 for _ in range(len(state.variant.suits)))
    players = tuple(
        Player.create(i, state.names[i], all_possible, hypo_stacks) for i in range(state.num_players)
    )
    common = Player.create(-1, "common", all_possible, hypo_stacks)
    return players, common
