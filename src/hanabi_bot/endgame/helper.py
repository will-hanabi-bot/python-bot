"""Endgame helper functions: must-plays, unwinnability checks, arrangement gen.

Port of scala-bot/src/scala_bot/endgame/helper.scala.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING

from hanabi_bot.basics.action import PerformDiscard, PerformPlay
from hanabi_bot.basics.identity import Identity
from hanabi_bot.basics.player import players_until

if TYPE_CHECKING:
    from hanabi_bot.basics.game import Game
    from hanabi_bot.basics.state import State

# RemainingMap: dict[Identity, int] — multiplicity of unseen card ids.
RemainingMap = dict[Identity, int]


def remaining_remove(remaining: RemainingMap, id_: Identity) -> RemainingMap:
    """Return a new RemainingMap with one copy of `id_` removed."""
    current = remaining[id_]
    new = dict(remaining)
    if current == 1:
        del new[id_]
    else:
        new[id_] = current - 1
    return new


def remaining_total(remaining: RemainingMap) -> int:
    return sum(remaining.values())


def find_must_plays(state: State, hand: tuple[int, ...]) -> list[Identity]:
    """Identities that this hand MUST play before the deck runs out.

    An id is a must-play if it's useful AND all remaining unseen copies are in this hand.
    """
    ids = [state.deck[o].id() for o in hand]
    ret: list[Identity] = []
    for i, id_ in enumerate(ids):
        if id_ is None:
            continue
        if not state.is_useful(id_):
            continue
        matches = 1
        for j in range(i + 1, len(hand)):
            other = ids[j]
            if other is not None and other.to_ord() == id_.to_ord():
                matches += 1
        if matches == state.card_count[id_.to_ord()] - state.base_count[id_.to_ord()]:
            ret.insert(0, id_)
    return ret


def unwinnable_state(state: State, player_turn: int, depth: int = 0) -> bool:
    """Quick check for unwinnability without recursion."""
    if state.ended or state.pace < 0:
        return True

    is_void = [False] * state.num_players
    must_plays = [0] * state.num_players
    must_start_endgame: list[int] = []

    for i in range(state.num_players - 1, -1, -1):
        hand = state.hands[i]
        # Void player = every card is basic trash.
        void = all(
            (id_ := state.deck[o].id()) is None or state.is_basic_trash(id_)
            for o in hand
        )
        if void:
            is_void[i] = True
        plays = find_must_plays(state, hand)
        must_plays[i] += len(plays)
        if len(plays) > 1:
            must_start_endgame.insert(0, i)

    if state.endgame_turns is not None:
        possible_players = 0
        double_play = -1
        for i in range(state.endgame_turns):
            pi = (player_turn + i) % state.num_players
            if not is_void[pi]:
                possible_players += 1
                if must_plays[pi] > 1:
                    double_play = i
        if possible_players + state.score < state.max_score:
            return True
        if double_play != -1:
            return True

    if state.cards_left == 1:
        if len(must_start_endgame) > 1:
            return True
        if len(must_start_endgame) == 1:
            target = must_start_endgame[0]
            if player_turn != target and len(
                players_until(state.num_players, player_turn, target)
            ) > state.clue_tokens:
                return True
    elif state.endgame_turns is None and sum(1 for v in is_void if v) > state.pace:
        return True

    return False


def trivially_winnable(game: Game, player_turn: int) -> tuple[list, Fraction] | str:  # type: ignore[type-arg]
    """Check if the final round is winnable by everyone just playing what they know.

    Returns (perform_actions, winrate) on success, or an error string. The action list
    has one PerformAction (a Play if a play is available this turn, else a Discard).
    """
    state = game.state
    if state.endgame_turns is None:
        return ""
    endgame_turns = state.endgame_turns
    if state.rem_score > endgame_turns:
        return ""

    # Iterate through the remaining turns; if a player has an obvious playable, play it.
    perform = PerformDiscard(state.hands[player_turn][0])
    play_stacks = list(state.play_stacks)
    for i in range(endgame_turns):
        pi = (player_turn + i) % state.num_players
        playables = game.players[pi].obvious_playables(game, pi)
        if not playables:
            continue
        first = playables[0]
        id_ = state.deck[first].id()
        if id_ is None:
            continue
        if i == 0:
            perform = PerformPlay(first)
        play_stacks[id_.suit_index] = id_.rank

    if sum(play_stacks) == state.max_score:
        return ([perform], Fraction(1, 1))
    return ""


@dataclass(frozen=True, slots=True)
class GameArr:
    prob: Fraction
    remaining: RemainingMap
    drew: Identity | None

    def __hash__(self) -> int:
        # RemainingMap is a dict; use frozen tuple of items for hashing.
        return hash((self.prob, tuple(sorted(self.remaining.items(), key=lambda t: t[0].to_ord())), self.drew))


def gen_arrs(
    game: Game, remaining: RemainingMap, clue_only: bool
) -> tuple[list[GameArr], list[GameArr]]:
    """Generate the undrawn-list and drawn-list of GameArr arrangements.

    Returns (undrawn, drawn). For clue actions (which don't draw), use undrawn.
    For play/discard actions (which draw), use drawn.
    """
    state = game.state
    undrawn = GameArr(prob=Fraction(1, 1), remaining=remaining, drew=None)

    rem_total = remaining_total(remaining)
    assert rem_total == state.cards_left, (
        f"gen_arrs failed: remaining total {rem_total} != cards_left {state.cards_left}"
    )

    if clue_only:
        drawn: list[GameArr] = []
    elif len(remaining) > 0 and all(state.is_basic_trash(id_) for id_ in remaining):
        # Short-circuit: all remaining are trash; just pick one.
        id_, _ = next(iter(remaining.items()))
        drawn = [GameArr(prob=Fraction(1, 1), remaining=remaining_remove(remaining, id_), drew=id_)]
    else:
        useful_arrs: list[GameArr] = []
        trash_arr_prob = Fraction(0, 1)
        trash_arr_remaining: RemainingMap = remaining
        trash_arr_drew: Identity | None = None
        for id_, missing in remaining.items():
            new_prob = Fraction(missing, state.cards_left)
            new_remaining = remaining_remove(remaining, id_)
            if state.is_basic_trash(id_):
                trash_arr_prob += new_prob
                trash_arr_remaining = new_remaining
                trash_arr_drew = id_
            else:
                useful_arrs.insert(0, GameArr(prob=new_prob, remaining=new_remaining, drew=id_))
        if trash_arr_prob > 0:
            trash_arr = GameArr(prob=trash_arr_prob, remaining=trash_arr_remaining, drew=trash_arr_drew)
            drawn = [*useful_arrs, trash_arr]
        else:
            drawn = useful_arrs
        if not drawn:
            drawn = [undrawn]

    return [undrawn], drawn
