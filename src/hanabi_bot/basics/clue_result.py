"""Statistics for a clue: what it touched, what it duped, what plays it enabled.

Port of scala-bot/src/scala_bot/basics/clueResult.scala.

Used by convention evaluators to compare candidate clues and pick the best one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .card import CardStatus
from .identity import Identity

if TYPE_CHECKING:
    from .action import ClueAction
    from .game import Game


def elim_result(
    prev: Game,
    game: Game,
    hand: tuple[int, ...] | list[int],
    list_: tuple[int, ...] | list[int],
) -> tuple[list[int], list[int], list[int]]:
    """Empathy statistics for a clue.

    Returns (new_touched, fill, elim):
    - new_touched: was unclued/blind-playing, now has fewer possibilities (clue revealed something)
    - fill: was clued, is in the clue's touched list, has consistent inferences, not CalledToPlay
    - elim: was clued, has consistent inferences, but NOT in list_ — narrowed by negative info
    """
    state = game.state
    new_touched: list[int] = []
    fill: list[int] = []
    elim: list[int] = []

    list_set = set(list_)

    for order in reversed(list(hand)):
        prev_thought = prev.common.thoughts[order]
        thought = game.common.thoughts[order]
        card = state.deck[order]
        status = game.meta[order].status

        if not (
            card.clued
            and status != CardStatus.CALLED_TO_DISCARD
            and thought.possible.length < prev_thought.possible.length
        ):
            continue

        card_id = card.id()
        if game.common.order_kt(game, order) or (
            card_id is not None and state.is_basic_trash(card_id)
        ):
            continue

        if not prev.state.deck[order].clued and not prev.is_blind_playing(order):
            new_touched.insert(0, order)
        elif (
            order in list_set
            and state.has_consistent_infs(thought)
            and status != CardStatus.CALLED_TO_PLAY
        ):
            fill.insert(0, order)
        elif state.has_consistent_infs(thought):
            elim.insert(0, order)

    return new_touched, fill, elim


def dupe_responsibility(game: Game, id_: Identity, except_: int) -> list[int]:
    """Which player(s) are "responsible" for saving id_ — i.e. have the fewest dupes.

    The teammates with the least visible-dupe count are the most-natural savers.
    """
    state = game.state

    def potential_dupes(player_index: int) -> int:
        return sum(
            1 for o in state.hands[player_index]
            if state.deck[o].clued and id_ in game.common.thoughts[o].inferred
        )

    dupes = [(potential_dupes(i), i) for i in range(state.num_players) if i != except_]
    if not dupes:
        return []
    min_dupe = min(d[0] for d in dupes)
    return [i for cnt, i in dupes if cnt == min_dupe]


def bad_touch_result(
    prev: Game, game: Game, action: ClueAction
) -> tuple[list[int], list[int], int]:
    """Bad-touch statistics for a clue.

    Returns (bad_touch, trash, avoidable_dupe):
    - bad_touch: newly-clued orders that are trash or duped
    - trash: newly-clued orders that are conventionally trash
    - avoidable_dupe: how much extra dupe the giver caused vs the best alternative
    """
    state = game.state
    giver = action.giver
    target = action.target

    # Per-player dupe scores (how many other-hand inferences would dupe with target's new cards).
    dupe_scores: list[int] = []
    for i, player in enumerate(prev.players):
        if i == target:
            dupe_scores.append(99)
            continue
        total = 0
        for order in state.hands[target]:
            card = state.deck[order]
            if prev.state.deck[order].clued or not card.clued:
                continue
            card_id = card.id()
            if card_id is None or state.is_basic_trash(card_id):
                continue
            for o in state.hands[i]:
                t = player.thoughts[o]
                if state.deck[o].clued and t.inferred.length > 1 and card_id in t.inferred:
                    total += 1
        dupe_scores.append(total)

    avoidable_dupe = dupe_scores[giver] - min(dupe_scores)

    def are_dupes(o1: int, o2: int, bad: list[int], trash: list[int]) -> bool:
        if o1 == o2:
            return False
        if not (state.deck[o1].clued and state.deck[o2].clued):
            return False
        if not game.me.thoughts[o1].matches(state.deck[o2]):
            return False
        if o2 in bad or o2 in trash:
            return False
        return (
            game.common.thoughts[o1].id() is None or game.common.thoughts[o2].id() is None
        )

    bad_touch: list[int] = []
    trash: list[int] = []
    for order in reversed(state.hands[target]):
        if prev.state.deck[order].clued or not state.deck[order].clued:
            continue
        if game.common.order_trash(game, order):
            trash.insert(0, order)
            continue
        order_id = state.deck[order].id()
        is_dup = (
            order_id is not None
            and (
                state.is_basic_trash(order_id)
                or any(are_dupes(order, o, bad_touch, trash) for o in state.hands[target])
            )
        )
        if is_dup:
            bad_touch.insert(0, order)

    # Second pass: detect duplicates against all hands, including formerly-finessed cards.
    for order in reversed(state.hands[target]):
        if order in bad_touch or order in trash:
            continue
        if not (not prev.state.deck[order].clued and state.deck[order].clued):
            continue
        duplicated = any(
            (prev.is_touched(o) or game.is_touched(o))
            and are_dupes(order, o, bad_touch, trash)
            for hand in state.hands for o in hand
        )
        if duplicated:
            bad_touch.insert(0, order)

    return bad_touch, trash, avoidable_dupe


def playables_result(prev: Game, game: Game) -> tuple[list[int], list[int]]:
    """Playable-related stats.

    Returns (blind_plays, playables):
    - blind_plays: newly blind-playing (unclued + status implies play) orders
    - playables: all newly hypo-playable orders (includes blind plays)
    """
    blind_plays: list[int] = []
    playables: list[int] = []
    for order in sorted(game.me.hypo_plays):
        if order in prev.me.hypo_plays:
            continue
        id_ = game.me.thoughts[order].id(infer=True)
        bad = False
        if id_ is not None and (prev.me.hypo_stacks[id_.suit_index] >= id_.rank or any(
            game.me.thoughts[o].matches(id_, infer=True)
            for o in prev.me.hypo_plays
        )):
            bad = True
        if bad:
            continue
        if game.is_blind_playing(order) and not prev.is_blind_playing(order):
            blind_plays.append(order)
            playables.append(order)
        else:
            playables.append(order)
    return blind_plays, playables
