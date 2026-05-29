"""Fix-clue detection and related helpers.

Port of scala-bot/src/scala_bot/basics/fix.scala.

DEFERRED: `connectable_simple` (uses game.simulate, which is Stage 6 territory).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .action import PlayAction, TurnAction
from .card import CardStatus
from .clue import ClueKind
from .identity import Identity, IdentitySet
from .variant import RAINBOWISH

if TYPE_CHECKING:
    from .action import ClueAction
    from .game import Game
    from .player import Player


@dataclass(frozen=True, slots=True)
class FixResultNormal:
    """A "conventional" fix clue — either resets clued cards or reveals duplicates."""

    clued_resets: tuple[int, ...]
    duplicate_reveals: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FixResultNoNewInfo:
    """Fix clue gave no new info — convention decides which order is fixed by play-order."""


@dataclass(frozen=True, slots=True)
class FixResultNone:
    """Not a fix clue."""


FixResult = FixResultNormal | FixResultNoNewInfo | FixResultNone


def check_fix(prev: Game, game: Game, action: ClueAction) -> FixResult:
    """Was the given clue a fix clue?

    A fix clue is one that either (a) resets a previously-clued card whose inferences
    became inconsistent, or (b) reveals that a card is a duplicate of another.

    Port of fix.scala lines 19-44.
    """
    list_ = action.list_
    clued_resets: list[int] = []
    duplicate_reveals: list[int] = []

    for order in reversed(list_):
        # Duplicated: prev-clued, now-known-id, and another order in `list_` shares its true id.
        thought_id = game.common.thoughts[order].id()
        prev_thought_id = prev.common.thoughts[order].id()
        prev_clued = prev.state.deck[order].clued

        duplicated = (
            prev_clued
            and thought_id is not None
            and prev_thought_id is None
            and any(
                o != order
                and prev.state.deck[o].clued
                and game.state.deck[order].matches(game.state.deck[o])
                for o in list_
            )
        )

        if prev.common.order_kt(game, order):
            continue
        if (
            prev.meta[order].status == CardStatus.CALLED_TO_PLAY
            and prev.is_blind_playing(order)
            and game.common.thoughts[order].info_lock is not None
            and game.common.thoughts[order].info_lock.forall(game.state.is_basic_trash)
        ) or (
            prev.state.deck[order].clued
            and not prev.common.thoughts[order].reset
            and game.common.order_kt(game, order)
        ):
            clued_resets.insert(0, order)
        elif duplicated:
            duplicate_reveals.insert(0, order)

    if clued_resets or duplicate_reveals:
        return FixResultNormal(tuple(clued_resets), tuple(duplicate_reveals))
    return FixResultNone()


def distribution_clue(
    prev: Game, game: Game, action: ClueAction, focus: int
) -> IdentitySet | None:
    """Return the candidate ids for a distribution clue (focus could be one of these), or None.

    A distribution clue is given in the endgame to clarify which player should play
    which copy when both have clued duplicates. It returns a non-empty set only if
    EVERY non-trash possibility is duplicated elsewhere AND certain endgame conditions hold.
    """
    state = game.state
    thought = game.common.thoughts[focus]

    if all(prev.state.deck[o].clued for o in action.list_):
        return None
    if not game.in_endgame and state.rem_score > len(state.variant.suits):
        return None
    focus_id = state.deck[focus].id()
    if focus_id is not None and state.is_basic_trash(focus_id):
        return None

    if action.clue.kind == ClueKind.COLOUR:
        poss = thought.possible
    else:
        rank = action.clue.value
        poss = thought.possible.filter(lambda i, _r=rank: i.rank == _r)

    useful = IdentitySet.empty()
    for id_ in poss:
        if state.is_basic_trash(id_):
            continue
        duplicated = any(
            i != action.target
            and any(game.is_touched(o) and game.order_matches(o, id_, infer=True) for o in hand)
            for i, hand in enumerate(state.hands)
        )
        if duplicated:
            useful = useful.add(id_)
        else:
            return None

    return useful if useful.non_empty else None


def connectable_simple(
    game: Game,
    player: Player,
    start: int,
    target: int,
    id_: Identity | None = None,
) -> list[int]:
    """If `id_` is given, return a non-empty list iff it can be made playable by `target`'s turn.

    Otherwise, return the orders of cards that would be playable in `target`'s hand by their turn.

    Port of scala-bot/.../fix.scala lines 55-78. Uses game.simulate_action.
    """
    state = game.state

    if id_ is not None and state.is_playable(id_):
        return [99]
    if start == target:
        return player.obvious_playables(game, target)
    if state.ended:
        return []

    next_player_index = state.next_player_index(start)
    playables = player.obvious_playables(game, start)

    for order in playables:
        play_id = player.thoughts[order].id(infer=True)
        if play_id is None:
            continue
        new_game = game
        if new_game.state.current_player_index != start:
            new_game = new_game.simulate_action(TurnAction(state.turn_count, start))
        new_game = new_game.simulate_action(
            PlayAction(start, order, play_id.suit_index, play_id.rank)
        )
        result = connectable_simple(new_game, player, next_player_index, target, id_)
        if result:
            return result

    return connectable_simple(game, player, next_player_index, target, id_)


def rainbow_mismatch(
    game: Game, action: ClueAction, id_: Identity, prompt: int
) -> bool:
    """Whether a prompted card should be ignored as a Free-Choice Finesse.

    True if: id_ is rainbowish AND the prompt's positive clues don't match the clue kind,
    AND a colour clue matching id_'s "true" colour would have touched the same set of cards
    (so the giver could have used a more specific colour instead).
    """
    state = game.state
    target = action.target
    list_ = action.list_
    clue = action.clue

    if clue.kind != ClueKind.COLOUR:
        return False
    if not state.variant.suits[id_.suit_index].suit_type.rainbowish:
        return False
    if game.known_as(prompt, RAINBOWISH):
        return False
    if any(c.kind == clue.kind and c.value == clue.value for c in state.deck[prompt].clues):
        return False

    if target == state.our_player_index:
        all_rainbow = all(
            game.me.thoughts[o].possible.forall(
                lambda c: state.variant.suits[c.suit_index].suit_type.rainbowish
            )
            for o in list_
        )
    else:
        all_rainbow = all(
            state.variant.suits[state.deck[o].suit_index].suit_type.rainbowish
            for o in list_
        )

    if not all_rainbow:
        return False

    # A matching colour clue would have touched the same set.
    for c in state.deck[prompt].clues:
        touched = state.clue_touched(state.hands[target], c.kind.value, c.value)
        if sorted(touched) == sorted(list_):
            return True
    return False
