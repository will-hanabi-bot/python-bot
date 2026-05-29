"""State: the public game state shared by every observer.

Port of scala-bot/src/scala_bot/basics/State.scala.

The Scala source uses `Vector` for sequence fields and `Array[Int]` for the
flat cardCount; in Python we use `tuple` for everything immutable. Updates
return new State instances via `dataclasses.replace`.

Per-card identity-set fields (playable_set, critical_set, trash_set, all_ids)
are `IdentitySet` instances (Python int subclass).

Helper accessors (score, max_score, is_playable, etc.) are plain methods or
properties rather than Scala's `inline def`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .card import Card
from .clue import Clue, ClueKind
from .identity import Identity, IdentitySet
from .options import TableOptions
from .variant import Variant

if TYPE_CHECKING:
    from .action import Action

# Hand size by number of players (index = num_players).
# Index 0,1 unused; matches scala-bot/.../Game.scala line 14.
HAND_SIZE: tuple[int, ...] = (0, 0, 5, 5, 4, 4, 3)


@dataclass(frozen=True, slots=True)
class State:
    """Public game state.

    Tuples are used in place of Scala's Vector for all collection fields;
    `dataclasses.replace` is the equivalent of Scala's `state.copy(...)`.
    """

    variant: Variant
    options: TableOptions

    num_players: int
    names: tuple[str, ...]
    our_player_index: int

    cards_left: int
    cards_total: int  # total deck size; used to detect deck-plays edge case

    play_stacks: tuple[int, ...]
    # discard_stacks[suit_index][rank-1] = tuple of card orders discarded for that identity
    discard_stacks: tuple[tuple[tuple[int, ...], ...], ...]
    max_ranks: tuple[int, ...]
    # base_count[ord] = number of physical copies known to be unavailable
    # (discarded, misplayed, played) for that identity
    base_count: tuple[int, ...]

    all_ids: IdentitySet
    playable_set: IdentitySet
    critical_set: IdentitySet
    trash_set: IdentitySet
    # card_count[ord] = total copies of that identity in the deck
    card_count: tuple[int, ...]

    hands: tuple[tuple[int, ...], ...]  # per-player tuple of card orders
    deck: tuple[Card, ...]
    holders: tuple[int, ...]  # holders[order] = player index that drew it

    turn_count: int = 0
    clue_tokens: int = 8
    half_clue_token: bool = False  # set in clue-starved variants after a 5 plays
    strikes: int = 0
    endgame_turns: int | None = None
    next_card_order: int = 0

    action_list: tuple[tuple[Action, ...], ...] = ()
    current_player_index: int = 0

    @classmethod
    def create(
        cls,
        names: tuple[str, ...],
        our_player_index: int,
        variant: Variant,
        options: TableOptions,
    ) -> State:
        """Build a fresh State at turn 0 with empty hands and an empty deck.

        Port of `object State { def apply(...) }` in State.scala lines 220-266.
        """
        num_suits = len(variant.suits)
        card_count_list: list[int] = [0] * (num_suits * 5)
        playable_set = IdentitySet.empty()
        critical_set = IdentitySet.empty()

        for suit_index in range(num_suits):
            for rank in range(1, 6):
                id_ = Identity(suit_index, rank)
                count = variant.card_count(id_)
                card_count_list[id_.to_ord()] = count
                if rank == 1:
                    playable_set = playable_set.add(id_)
                if count == 1:
                    critical_set = critical_set.add(id_)

        return cls(
            variant=variant,
            options=options,
            num_players=len(names),
            names=names,
            our_player_index=our_player_index,
            cards_left=variant.total_cards,
            cards_total=variant.total_cards,
            play_stacks=tuple([0] * num_suits),
            discard_stacks=tuple(tuple(() for _ in range(5)) for _ in range(num_suits)),
            max_ranks=tuple([5] * num_suits),
            base_count=tuple([0] * (num_suits * 5)),
            all_ids=IdentitySet.from_iter(variant.all_ids()),
            playable_set=playable_set,
            critical_set=critical_set,
            trash_set=IdentitySet.empty(),
            card_count=tuple(card_count_list),
            hands=tuple(() for _ in range(len(names))),
            deck=(),
            holders=(),
        )

    # --- Mutators (return new State) ---

    def with_discard(self, id_: Identity, order: int) -> State:
        """Apply a discard: add to discard stacks, update base count, update critical/trash/max."""
        suit_index, rank = id_.suit_index, id_.rank
        rank_idx = rank - 1

        # Append to the discard pile for (suit, rank). Note: Scala uses cons (order +: list),
        # so newer orders go to the head. We do the same.
        suit_piles = self.discard_stacks[suit_index]
        new_pile = (order, *suit_piles[rank_idx])
        new_suit = (*suit_piles[:rank_idx], new_pile, *suit_piles[rank_idx + 1:])
        new_discard = (*self.discard_stacks[:suit_index], new_suit, *self.discard_stacks[suit_index + 1:])

        ord_ = id_.to_ord()
        new_base_list = list(self.base_count)
        new_base_list[ord_] += 1
        new_base = tuple(new_base_list)

        if id_ in self.critical_set:
            # Discarding a critical card lowers max rank for that suit and removes from playable
            new_max_list = list(self.max_ranks)
            new_max_list[suit_index] = min(new_max_list[suit_index], rank - 1)
            new_max = tuple(new_max_list)
            new_critical = self.critical_set.difference(id_)
            new_trash = self.trash_set.union(id_)
            new_playable = self.playable_set.difference(id_)
        else:
            new_max = self.max_ranks
            # Check if this discard makes it critical (last copy)
            became_critical = self.card_count[ord_] - new_base[ord_] == 1 and not self.is_basic_trash(id_)
            new_critical = self.critical_set.union(id_) if became_critical else self.critical_set
            new_trash = self.trash_set
            new_playable = self.playable_set

        return dataclasses.replace(
            self,
            discard_stacks=new_discard,
            base_count=new_base,
            max_ranks=new_max,
            playable_set=new_playable,
            critical_set=new_critical,
            trash_set=new_trash,
        )

    def with_play(self, id_: Identity) -> State:
        """Apply a successful play: advance play stack, update playable set, regain clue if 5."""
        suit_index, rank = id_.suit_index, id_.rank
        next_id = id_.next

        new_playable = self.playable_set.difference(id_)
        if next_id is not None:
            new_playable = new_playable.union(next_id)

        new_stacks_list = list(self.play_stacks)
        new_stacks_list[suit_index] = rank
        new_stacks = tuple(new_stacks_list)

        ord_ = id_.to_ord()
        new_base_list = list(self.base_count)
        new_base_list[ord_] += 1

        new_state = dataclasses.replace(
            self,
            play_stacks=new_stacks,
            base_count=tuple(new_base_list),
            playable_set=new_playable,
            trash_set=self.trash_set.union(id_),
        )
        if rank == 5:
            new_state = new_state.regain_clue()
        return new_state

    def try_play(self, id_: Identity) -> State:
        """Apply a play if the identity is currently playable; otherwise unchanged."""
        return self.with_play(id_) if self.is_playable(id_) else self

    def regain_clue(self) -> State:
        """Gain a clue token (or half-token in clue-starved variants). Caps at 8."""
        if self.variant.clue_starved:
            if self.half_clue_token:
                return dataclasses.replace(self, clue_tokens=self.clue_tokens + 1, half_clue_token=False)
            return dataclasses.replace(self, half_clue_token=self.clue_tokens < 8)
        return dataclasses.replace(self, clue_tokens=min(8, self.clue_tokens + 1))

    # --- Pure helpers ---

    @property
    def ended(self) -> bool:
        """Game has ended (lost by strikeouts, perfect score, or endgame timer expired)."""
        return (
            self.strikes == 3
            or self.score == self.max_score
            or (self.endgame_turns is not None and self.endgame_turns == 0)
        )

    @property
    def score(self) -> int:
        return sum(self.play_stacks)

    @property
    def max_score(self) -> int:
        return sum(self.max_ranks)

    @property
    def rem_score(self) -> int:
        return self.max_score - self.score

    @property
    def pace(self) -> int:
        return self.score + self.cards_left + self.num_players - self.max_score

    def last_player_index(self, player_index: int) -> int:
        return (player_index + self.num_players - 1) % self.num_players

    def next_player_index(self, player_index: int) -> int:
        return (player_index + 1) % self.num_players

    def is_basic_trash(self, id_: Identity) -> bool:
        """Already played or above the max achievable rank in that suit."""
        return id_.rank <= self.play_stacks[id_.suit_index] or id_.rank > self.max_ranks[id_.suit_index]

    def is_useful(self, id_: Identity) -> bool:
        """Strictly above the current play stack and within max achievable."""
        return id_.rank > self.play_stacks[id_.suit_index] and id_.rank <= self.max_ranks[id_.suit_index]

    def playable_away(self, id_: Identity) -> int:
        return id_.rank - (self.play_stacks[id_.suit_index] + 1)

    def is_playable(self, id_: Identity) -> bool:
        return self.playable_away(id_) == 0

    def is_critical(self, id_: Identity) -> bool:
        return (
            not self.is_basic_trash(id_)
            and len(self.discard_stacks[id_.suit_index][id_.rank - 1]) == self.card_count[id_.to_ord()] - 1
        )

    @property
    def our_hand(self) -> tuple[int, ...]:
        return self.hands[self.our_player_index]

    @property
    def can_clue(self) -> bool:
        return self.clue_tokens > 0

    def holder_of(self, order: int) -> int:
        if order >= len(self.holders):
            raise ValueError(f"Tried to get holder of {order} but it hasn't been drawn yet!")
        return self.holders[order]

    def in_starting_hand(self, order: int) -> bool:
        return order < self.num_players * HAND_SIZE[self.num_players]

    def multiplicity(self, ids: IdentitySet) -> int:
        """Total physical copies of the given identities in the deck."""
        return sum(self.card_count[i.to_ord()] for i in ids)

    def has_consistent_infs(self, thought) -> bool:  # type: ignore[no-untyped-def]
        """A thought is consistent if it's fully determined or its inferred set contains the truth."""
        if thought.possible.length == 1:
            return True
        true_id = self.deck[thought.order].id()
        return true_id is None or true_id in thought.inferred

    def clue_touched(self, orders: tuple[int, ...] | list[int], clue_kind: int, clue_value: int) -> list[int]:
        """Return the subset of orders whose cards are touched by the given (kind, value) clue."""
        result = []
        for order in orders:
            card = self.deck[order]
            id_ = card.id()
            if id_ is not None and self.variant.id_touched(id_, clue_kind, clue_value):
                result.append(order)
        return result

    def all_colour_clues(self, target: int) -> list[Clue]:
        """All colour clues that touch at least one card in target's hand."""
        result = []
        for suit_index in range(len(self.variant.colourable_suits)):
            if self.clue_touched(self.hands[target], ClueKind.COLOUR.value, suit_index):
                result.append(Clue(ClueKind.COLOUR, suit_index, target))
        return result

    def all_valid_clues(self, target: int) -> list[Clue]:
        """All clues (colour + rank) that touch at least one card in target's hand.

        Rank clues are excluded when the variant has a special rank that breaks rank semantics
        (pink_s/brown_s/deceptive_s) — those clues are special-cased per scala State.scala line 138.
        """
        clues: list[Clue] = []
        v = self.variant
        rank_blocked = v.special_rank is not None and (v.pink_s or v.brown_s or v.deceptive_s)
        for rank in range(1, 6):
            if rank_blocked and rank == v.special_rank:
                continue
            clue = Clue(ClueKind.RANK, rank, target)
            if self.clue_touched(self.hands[target], ClueKind.RANK.value, rank):
                clues.append(clue)
        for suit_index in range(len(v.colourable_suits)):
            clue = Clue(ClueKind.COLOUR, suit_index, target)
            if self.clue_touched(self.hands[target], ClueKind.COLOUR.value, suit_index):
                clues.append(clue)
        return clues

    def includes_variant(self, regex) -> bool:  # type: ignore[no-untyped-def]
        """True if any suit name matches the regex (re.Pattern)."""
        return any(regex.search(suit.name) for suit in self.variant.suits)

    def expand_short(self, short: str) -> Identity:
        """Parse a 2-char short like 'r5' into an Identity. Used in test/debug helpers.

        First char = variant short_form; second char = rank digit.
        """
        if len(short) != 2:
            raise ValueError(f"Short should be exactly 2 characters (got {short!r})")
        try:
            suit_index = self.variant.short_forms.index(short[0])
        except ValueError as e:
            raise ValueError(f"Colour {short!r} doesn't exist in selected variant") from e
        if not short[1].isdigit():
            raise ValueError(f"Rank {short!r} doesn't exist in selected variant")
        return Identity(suit_index, int(short[1]))

    def log_id(self, id_or_order: Identity | int | None) -> str:
        """Pretty-print an identity, an order (looked up in the deck), or None."""
        if id_or_order is None:
            return "xx"
        if isinstance(id_or_order, int):
            return self.log_id(self.deck[id_or_order].id())
        return f"{self.variant.short_forms[id_or_order.suit_index]}{id_or_order.rank}"
