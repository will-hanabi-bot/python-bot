"""Monte Carlo endgame solver.

Port of scala-bot/src/scala_bot/endgame/solver.scala.

Entry point: `EndgameSolver(monte_carlo=True, timeout=30.0).solve(game)`. Returns
either `(PerformAction, Fraction)` with a winrate >= 0, or a string error message.

The solver works by enumerating deck arrangements (permutations of unseen cards),
recursively walking each player's plausible actions, and picking the action with
the highest weighted winrate across arrangements. Bail conditions:
- Too many unseen useful ids (≥4): returns immediately.
- Timeout exceeded: returns partial best-so-far.

Uses `fractions.Fraction` for exact winrate arithmetic.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import TYPE_CHECKING

from hanabi_bot.basics.action import (
    PerformAction,
    PerformDiscard,
    PerformPlay,
    PerformRank,
)
from hanabi_bot.basics.card import CardStatus
from hanabi_bot.basics.identity import Identity
from hanabi_bot.basics.player import visible_find

from .helper import (
    GameArr,
    RemainingMap,
    gen_arrs,
    remaining_remove,
    remaining_total,
    trivially_winnable,
    unwinnable_state,
)
from .winnable import (
    SimpleResult,
    WinnableWithDraws,
    clueless_winnable,
    winnable_if,
)

if TYPE_CHECKING:
    from hanabi_bot.basics.game import Game

log = logging.getLogger("hanabi_bot.endgame")

WinnableResult = tuple[list[PerformAction], Fraction] | str
SolveResult = tuple[PerformAction, Fraction] | str


@dataclass(frozen=True, slots=True)
class Arrangement:
    """One candidate assignment of unknown own-hand cards to identities."""
    ids: tuple[Identity, ...]
    prob: Fraction
    remaining: RemainingMap


def find_remaining_ids(game: Game) -> tuple[RemainingMap, list[tuple[int, Identity | None]]]:
    """Compute the unseen-identity multiset + a list of own-hand (order, known-id-or-None).

    Returns (remaining_ids_map, own_ids_list).
    """
    state = game.state
    seen_ids: dict[Identity, int] = {}
    own_ids: list[tuple[int, Identity | None]] = []

    for i in range(state.num_players):
        for order in state.hands[i]:
            id_ = game.me.thoughts[order].id()
            if id_ is not None:
                seen_ids[id_] = seen_ids.get(id_, 0) + 1
                if i == state.our_player_index:
                    own_ids.insert(0, (order, id_))
            elif i == state.our_player_index:
                own_ids.insert(0, (order, None))

    remaining_ids: RemainingMap = {}
    for id_ in state.variant.all_ids():
        missing = state.card_count[id_.to_ord()] - state.base_count[id_.to_ord()] - seen_ids.get(id_, 0)
        if missing > 0:
            remaining_ids[id_] = missing
    return remaining_ids, own_ids


def _past_deadline(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() > deadline


@dataclass
class EndgameSolver:
    """Solver state.

    `success_rate` caches per-depth per-action winrates to prioritize promising actions
    in subsequent iterations (port of Scala's successRate field).
    """
    monte_carlo: bool = True
    timeout: float = 30.0  # seconds
    success_rate: dict[int, dict[PerformAction, tuple[Fraction, int]]] = field(default_factory=dict)

    # --- Main entry point ---

    def solve(
        self, game: Game, only_action: PerformAction | None = None
    ) -> SolveResult:
        """Compute the best PerformAction for the bot to take, with its winrate.

        Returns `(action, winrate)` on success or a `str` error.
        """
        state = game.state

        # Trivial: one play wins.
        if state.score + 1 == state.max_score:
            for o in state.our_hand:
                id_ = game.me.thoughts[o].id(infer=True)
                if id_ is not None and state.is_playable(id_):
                    return PerformPlay(o), Fraction(1, 1)

        start = time.monotonic()
        deadline = start + self.timeout
        remaining_ids, own_ids = find_remaining_ids(game)

        # Too many unseen useful ids — abort.
        useful_unseen = sum(
            1 for id_, v in remaining_ids.items()
            if state.is_useful(id_) and v == state.card_count[id_.to_ord()]
        )
        if useful_unseen > 3:
            missing = ",".join(
                state.log_id(id_) for id_ in remaining_ids if state.is_useful(id_)
            )
            return f"couldn't find any {missing}!"

        # Pre-populate the assumed game with known own-hand ids.
        assumed_game = game
        for order, id_ in own_ids:
            if id_ is not None:
                assumed_game = assumed_game.with_id(order, id_)

        linked_orders = set(game.me.linked_orders(state))
        unknown_own = [order for order, id_ in own_ids if id_ is None]
        total_unknown = state.cards_left + len(unknown_own)
        log.debug("unknown_own=%s cards_left=%d", unknown_own, state.cards_left)

        if total_unknown == 0:
            result = self.winnable(assumed_game, state.our_player_index, remaining_ids, deadline)
            if isinstance(result, str):
                return "couldn't find a winning strategy"
            actions, winrate = result
            log.info("solved in %dms", int((time.monotonic() - start) * 1000))
            return actions[0], winrate

        log.debug("remaining ids: %s", _fmt_remaining(remaining_ids, state))

        # --- Arrangement generation ---

        def impossible_arr(
            ids: tuple[Identity, ...], id_: Identity, order: int, try_filter: bool
        ) -> bool:
            thought = game.me.thoughts[order]
            deck_id = state.deck[order].id()
            if deck_id is not None and deck_id != id_:
                return True
            if id_ not in thought.possible:
                return True
            if try_filter and not game.valid_arr(id_, order):
                return True
            # Trash id can't be assigned if linked for non-trash and other linked are already trash.
            if state.is_basic_trash(id_) and order in linked_orders:
                for link in game.me.links:
                    orders = link.orders
                    promise = getattr(link, "promise", None)
                    if (
                        isinstance(promise, Identity)
                        and state.is_useful(promise)
                        and order in orders
                    ):
                        all_trash = True
                        for o in orders:
                            if o == order:
                                continue
                            # Look up o in unknown_own; if found, check arrangement says trash there.
                            try:
                                idx = unknown_own.index(o)
                                if idx >= len(ids) or not state.is_basic_trash(ids[idx]):
                                    all_trash = False
                                    break
                            except ValueError:
                                all_trash = False
                                break
                        if all_trash:
                            return True
            return False

        def expand_arr(arrangement: Arrangement, try_filter: bool) -> list[Arrangement]:
            if _past_deadline(deadline):
                return [arrangement]
            total_cards = remaining_total(arrangement.remaining)
            if total_cards == 0:
                return []
            result: list[Arrangement] = []
            order_for_next = unknown_own[len(arrangement.ids)]
            for id_, missing in arrangement.remaining.items():
                if impossible_arr(arrangement.ids, id_, order_for_next, try_filter):
                    continue
                new_remaining = remaining_remove(arrangement.remaining, id_)
                new_ids = (*arrangement.ids, id_)
                new_prob = arrangement.prob * Fraction(missing, total_cards)
                result.append(Arrangement(ids=new_ids, prob=new_prob, remaining=new_remaining))
            return result

        initial_arr = Arrangement(ids=(), prob=Fraction(1, 1), remaining=remaining_ids)
        arrs_iter: list[Arrangement] = [initial_arr]
        for _ in range(len(unknown_own)):
            next_arrs: list[Arrangement] = []
            for arr in arrs_iter:
                next_arrs.extend(expand_arr(arr, try_filter=True))
            arrs_iter = next_arrs

        if not arrs_iter:
            # Try again with no filter.
            log.debug("retrying arrangement gen with no filter")
            arrs_iter = [initial_arr]
            for _ in range(len(unknown_own)):
                next_arrs2: list[Arrangement] = []
                for arr in arrs_iter:
                    next_arrs2.extend(expand_arr(arr, try_filter=False))
                arrs_iter = next_arrs2

        if _past_deadline(deadline):
            return "timeout"

        # Monte Carlo grouping: collapse trash-equivalent arrangements.
        if self.monte_carlo:
            sum_prob = Fraction(0, 1)
            grouped: dict[str, Arrangement] = {}
            for arr in arrs_iter:
                key = ",".join(
                    "_" if state.is_basic_trash(i) else state.log_id(i) for i in arr.ids
                )
                if key in grouped:
                    existing = grouped[key]
                    grouped[key] = dataclasses.replace(existing, prob=existing.prob + arr.prob)
                else:
                    grouped[key] = arr
                sum_prob += arr.prob
            if sum_prob > 0:
                arrs = [
                    dataclasses.replace(a, prob=a.prob / sum_prob) for a in grouped.values()
                ]
            else:
                arrs = list(grouped.values())
        else:
            arrs = list(arrs_iter)

        arrs.sort(key=lambda a: -a.prob)
        if not arrs:
            arrs = [Arrangement(ids=(), prob=Fraction(1, 1), remaining=remaining_ids)]

        # Build hypos: (game, actions, (undrawn, drawn), prob) per arrangement.
        hypos: list[tuple[Game, list[tuple[PerformAction, list[Identity]]], tuple[list[GameArr], list[GameArr]], Fraction]] = []
        for arr in arrs:
            hypo = assumed_game
            for i, id_ in enumerate(arr.ids):
                hypo = hypo.with_id(unknown_own[i], id_)
            actions = self.possible_actions(hypo, state.our_player_index, arr.remaining, deadline)
            if not actions:
                actions = self.possible_actions(
                    hypo, state.our_player_index, arr.remaining, deadline, infer=True
                )
            clue_only = bool(actions) and all(a[0].is_clue for a in actions)
            game_arrs = gen_arrs(hypo, arr.remaining, clue_only)
            hypos.append((hypo, actions, game_arrs, arr.prob))

        if only_action is not None:
            winrate = Fraction(0, 1)
            for hypo, actions, game_arrs, prob in hypos:
                match = next((a for a in actions if a[0] == only_action), None)
                if match is None:
                    continue
                undrawn, drawn = game_arrs
                arr_list = undrawn if match[0].is_clue else drawn
                winrate += prob * self.action_winrate(
                    hypo, arr_list, match, state.our_player_index, deadline
                )
            return only_action, winrate

        all_actions: list[PerformAction] = []
        for _, actions, _, _ in hypos:
            for perform, _ in actions:
                if perform not in all_actions:
                    all_actions.append(perform)

        first_hypo, first_actions, first_arrs, first_prob = hypos[0]

        # Compute initial winrates over the first (most probable) arrangement.
        initial_actions: list[tuple[PerformAction, Fraction]] = self.optimize_full(
            first_hypo, first_arrs, first_actions, state.our_player_index, deadline
        )
        initial_actions = [(p, w * first_prob) for p, w in initial_actions]
        # Append any actions not in initial with winrate 0.
        for a in all_actions:
            if not any(p == a for p, _ in initial_actions):
                initial_actions.append((a, Fraction(0, 1)))

        if not initial_actions:
            return "couldn't find any winning actions"

        # Iterative refinement across remaining arrangements.
        if len(hypos) > 1:
            best: tuple[PerformAction, Fraction] = initial_actions[0]
            for action, winrate in initial_actions:
                if _past_deadline(deadline):
                    break
                cur_winrate = winrate
                rem_prob = Fraction(1, 1) - winrate
                for hypo, actions_h, game_arrs_h, prob_h in hypos[1:]:
                    if cur_winrate + rem_prob < best[1]:
                        break
                    match = next((a for a in actions_h if a[0] == action), None)
                    if match is None:
                        rem_prob -= prob_h
                        continue
                    undrawn, drawn = game_arrs_h
                    arr_list = undrawn if match[0].is_clue else drawn
                    hypo_wr = prob_h * self.action_winrate(
                        hypo, arr_list, match, state.our_player_index, deadline
                    )
                    cur_winrate += hypo_wr
                    rem_prob -= prob_h
                if cur_winrate == Fraction(1, 1):
                    best = (action, cur_winrate)
                    break
                if cur_winrate > best[1]:
                    best = (action, cur_winrate)
            log.info("endgame solved in %dms: %s winrate=%s", int((time.monotonic() - start) * 1000), best[0], best[1])
            if best[1] == 0:
                return "couldn't find any winning actions"
            return best

        # Single hypo: just take the top initial action.
        best_action, best_winrate = initial_actions[0]
        log.info("endgame solved in %dms: %s winrate=%s", int((time.monotonic() - start) * 1000), best_action, best_winrate)
        return best_action, best_winrate

    # --- Recursive winnability ---

    def winnable(
        self,
        game: Game,
        player_turn: int,
        remaining: RemainingMap,
        deadline: float | None,
        depth: int = 0,
    ) -> WinnableResult:
        state = game.state
        if _past_deadline(deadline):
            return "timeout"

        trivial = trivially_winnable(game, player_turn)
        if not isinstance(trivial, str):
            return trivial

        # Clueless-winnable: if every remaining useful id is identified somewhere,
        # everyone plays-what-they-know.
        viable_clueless = True
        for suit_index in range(len(state.variant.suits)):
            for rank in range(state.play_stacks[suit_index] + 1, state.max_ranks[suit_index] + 1):
                id_ = Identity(suit_index, rank)
                found = False
                for hand in state.hands:
                    for o in hand:
                        if game.common.thoughts[o].matches(id_, infer=True):
                            deck_id = state.deck[o].id()
                            if deck_id is None or deck_id == id_:
                                found = True
                                break
                    if found:
                        break
                if not found:
                    viable_clueless = False
                    break
            if not viable_clueless:
                break

        if viable_clueless:
            clueless_state = state
            for hand in state.hands:
                for order in hand:
                    common_id = game.common.thoughts[order].id()
                    if common_id is not None:
                        new_card = dataclasses.replace(
                            clueless_state.deck[order],
                            suit_index=common_id.suit_index,
                            rank=common_id.rank,
                        )
                        deck = clueless_state.deck
                        new_deck = (*deck[:order], new_card, *deck[order + 1:])
                        clueless_state = dataclasses.replace(clueless_state, deck=new_deck)
            win = clueless_winnable(clueless_state, player_turn, deadline, depth)
            if win is not None:
                if isinstance(win, PerformRank) and win.target == 0 and win.value == 0:
                    # Replace dummy clue with a real clue from find_all_clues.
                    real_clues = game.find_all_clues(player_turn) if hasattr(game, "find_all_clues") else []
                    if real_clues:
                        return [real_clues[0]], Fraction(1, 1)
                return [win], Fraction(1, 1)

        bottom_decked = bool(remaining) and all(
            state.is_critical(id_) and id_.rank != 5 for id_ in remaining
        )
        if bottom_decked or unwinnable_state(state, player_turn, depth):
            return ""

        performs = self.possible_actions(game, player_turn, remaining, deadline, depth)
        if not performs:
            return ""

        # Direct win check.
        if state.score + 1 == state.max_score:
            for p, _ in performs:
                if isinstance(p, PerformPlay):
                    deck_id = state.deck[p.target].id()
                    if deck_id is not None and state.is_playable(deck_id):
                        return [p], Fraction(1, 1)

        # One-BDR-left special case
        one_bdr_left = (
            state.score == state.max_score - 2
            and sum(
                1 for i, stack in enumerate(state.play_stacks)
                if stack == state.max_ranks[i]
            ) == len(state.variant.suits) - 1
            and state.playable_set.length > 0
            and state.is_critical(state.playable_set.head)
        )
        if one_bdr_left:
            bdr_id = state.playable_set.head
            unseen = not visible_find(state, game.me, bdr_id)
            next_id = bdr_id.next
            known_5 = False
            if next_id is not None:
                for o in visible_find(state, game.me, next_id):
                    if game.players[state.holder_of(o)].thoughts[o].matches(next_id):
                        known_5 = True
                        break
            navigable = state.clue_tokens + state.pace > state.num_players and state.cards_left > 2
            if unseen and known_5 and navigable:
                return [performs[-1][0]], Fraction(state.cards_left - 1, state.cards_left)

        arrs = gen_arrs(game, remaining, False)
        return self.optimize(game, arrs, performs, player_turn, deadline, depth)

    # --- Action enumeration ---

    def possible_actions(
        self,
        game: Game,
        player_turn: int,
        remaining: RemainingMap,
        deadline: float | None,
        depth: int = 0,
        infer: bool = False,
    ) -> list[tuple[PerformAction, list[Identity]]]:
        state = game.state

        def try_action(perform: PerformAction) -> tuple[PerformAction, list[Identity]] | None:
            res = winnable_if(game, player_turn, perform, remaining, deadline, depth)
            if res == SimpleResult.UNWINNABLE:
                return None
            if isinstance(res, WinnableWithDraws):
                return perform, res.draws
            return perform, []

        # Urgent first — but only short-circuit if the urgent action actually wins.
        # If it loses (per the per-player winnable_if check), fall through to enumerate
        # alternative actions so the solver can find a winning stall/clue.
        urgent_perform: PerformAction | None = None
        for urgent in state.hands[player_turn]:
            if game.meta[urgent].urgent:
                status = game.meta[urgent].status
                urgent_perform = (
                    PerformPlay(urgent)
                    if status == CardStatus.CALLED_TO_PLAY
                    else PerformDiscard(urgent)
                )
                break
        if urgent_perform is not None:
            r = try_action(urgent_perform)
            if r is not None:
                return [r]
            # urgent action is unwinnable — fall through to full enumeration.

        if infer or game.good_touch or state.endgame_turns is not None:
            playables = game.players[player_turn].thinks_playables(game, player_turn, exclude_trash=True)
        else:
            playables = game.players[player_turn].obvious_playables(game, player_turn)

        play_actions: list[tuple[PerformAction, list[Identity]]] = []
        for order in playables:
            if _past_deadline(deadline):
                return []
            if state.deck[order].id() is None:
                continue
            r = try_action(PerformPlay(order))
            if r is not None:
                play_actions.append(r)

        # Cap consecutive clues to discourage stall loops.
        too_many_clues = False
        if state.num_players > 2:
            count = 0
            for turn in reversed(state.action_list):
                for a in reversed(turn):
                    if getattr(a, "requires_draw", False):
                        break
                    if a.__class__.__name__ == "ClueAction":
                        count += 1
                else:
                    continue
                break
            if count > state.num_players + 1:
                too_many_clues = True

        default_clue = PerformRank(0, 0)
        clue_winnable = False
        if state.can_clue and not too_many_clues:
            res = winnable_if(game, player_turn, default_clue, remaining, deadline, depth)
            if res == SimpleResult.ALWAYS_WINNABLE:
                clue_winnable = True

        clue_actions: list[tuple[PerformAction, list[Identity]]] = []
        # Enumerate real clues when:
        # - the dummy stall is itself winnable (existing fast path), OR
        # - depth <= 1 (our own turn or the very next player's turn). At those depths a
        #   specific clue can convey information that the dummy stall doesn't carry —
        #   the dummy is a proxy for "any clue used purely as a stall", which is too
        #   pessimistic for multi-turn information-passing lines.
        should_enumerate_clues = (
            state.can_clue
            and not too_many_clues
            and (clue_winnable or depth <= 1)
        )
        if should_enumerate_clues:
            fully_known = (
                not remaining
                or (len(remaining) == 1 and state.is_basic_trash(next(iter(remaining))))
            ) and all(
                (id_ := state.deck[o].id()) is None
                or state.is_basic_trash(id_)
                or game.common.thoughts[o].matches(id_, infer=True)
                for hand in state.hands
                for o in hand
            )
            all_clues = game.find_all_clues(player_turn) if hasattr(game, "find_all_clues") else []
            clue_actions = (
                [(all_clues[0], [])]
                if fully_known and all_clues
                else [(c, []) for c in all_clues]
            )

        if _past_deadline(deadline):
            return []

        # Discard gates.
        ignore_dc = (
            state.pace == 0
            or state.clue_tokens == 8
            or any(
                (
                    (id_ := game.players[player_turn].thoughts[p].id(infer=True)) is not None
                    and (
                        id_.rank == 5
                        or (
                            any(
                                o != p
                                and state.deck[o].clued
                                and game.common.thoughts[o].possible.forall(state.is_critical)
                                for o in state.hands[player_turn]
                            )
                            and (
                                state.is_critical(id_)
                                or any(
                                    o != p
                                    and game.players[player_turn].thoughts[o].matches(id_, infer=True)
                                    for o in state.hands[player_turn]
                                )
                            )
                        )
                    )
                )
                for p in playables
            )
        )

        dc_actions: list[tuple[PerformAction, list[Identity]]] = []
        if not ignore_dc:
            discard_candidates = game.find_all_discards(player_turn) if hasattr(game, "find_all_discards") else []
            for d in discard_candidates:
                r = try_action(d)
                if r is not None:
                    dc_actions.append(r)

        # Prefer discard if no visible playables exist for anyone except the turn-player.
        prefer_dc = all(
            i == player_turn
            or all(
                (deck_id := state.deck[o].id()) is None or not state.is_playable(deck_id)
                for o in hand
            )
            for i, hand in enumerate(state.hands)
        )
        if prefer_dc:
            return play_actions + dc_actions + clue_actions
        return play_actions + clue_actions + dc_actions

    # --- Per-action winrate ---

    def action_winrate(
        self,
        game: Game,
        arrs: list[GameArr],
        action: tuple[PerformAction, list[Identity]],
        player_turn: int,
        deadline: float | None,
    ) -> Fraction:
        if _past_deadline(deadline):
            return Fraction(0, 1)
        perform, winnable_draws = action
        next_player = game.state.next_player_index(player_turn)
        total = Fraction(0, 1)
        for arr in arrs:
            if arr.drew is not None and arr.drew not in winnable_draws:
                continue
            game_action = self._perform_to_action(perform, game, player_turn)
            new_game = game.simulate_action(game_action, draw=arr.drew)
            if new_game.state.max_score < game.state.max_score:
                continue
            res = self.winnable(new_game, next_player, arr.remaining, deadline, 1)
            if isinstance(res, str):
                continue
            _, wr = res
            total += arr.prob * wr
        return total

    # --- Optimization across arrangements ---

    def optimize_full(
        self,
        game: Game,
        arrs: tuple[list[GameArr], list[GameArr]],
        actions: list[tuple[PerformAction, list[Identity]]],
        player_turn: int,
        deadline: float | None,
    ) -> list[tuple[PerformAction, Fraction]]:
        undrawn, drawn = arrs
        result: list[tuple[PerformAction, Fraction]] = []
        for perform, winnable_draws in actions:
            arr_list = undrawn if perform.is_clue else drawn
            wr = self.action_winrate(game, arr_list, (perform, winnable_draws), player_turn, deadline)
            result.append((perform, wr))
        result.sort(key=lambda t: -t[1])
        return result

    def optimize(
        self,
        game: Game,
        arrs: tuple[list[GameArr], list[GameArr]],
        actions: list[tuple[PerformAction, list[Identity]]],
        player_turn: int,
        deadline: float | None,
        depth: int = 0,
    ) -> WinnableResult:
        undrawn, drawn = arrs
        next_player = game.state.next_player_index(player_turn)

        # Sort by cached success rate if available.
        sr = self.success_rate.get(depth, {})
        if sr:
            actions = sorted(
                actions,
                key=lambda a: -sr.get(a[0], (Fraction(-1, 1), 0))[0],
            )

        best_actions: list[PerformAction] = []
        best_winrate = Fraction(0, 1)

        for perform, winnable_draws in actions:
            if _past_deadline(deadline):
                if best_actions:
                    return best_actions, best_winrate
                return "timeout"

            arr_list = undrawn if perform.is_clue else drawn
            winrate = Fraction(0, 1)
            rem_prob = Fraction(1, 1)
            for arr in arr_list:
                if winrate + rem_prob < best_winrate:
                    break
                rem_prob -= arr.prob
                if arr.drew is not None and arr.drew not in winnable_draws:
                    continue
                game_action = self._perform_to_action(perform, game, player_turn)
                new_game = game.simulate_action(game_action, draw=arr.drew)
                if new_game.state.max_score < game.state.max_score:
                    continue
                res = self.winnable(new_game, next_player, arr.remaining, deadline, depth + 1)
                if isinstance(res, str):
                    continue
                _, wr = res
                winrate += arr.prob * wr
                if winrate > Fraction(1, 1):
                    log.warning("winrate exceeded 1 at depth %d for %s", depth, perform)

            # Cache result.
            entry = self.success_rate.setdefault(depth, {})
            if perform in entry:
                old_frac, times = entry[perform]
                entry[perform] = ((old_frac * times + winrate) / (times + 1), times + 1)
            else:
                entry[perform] = (winrate, 1)

            # Early-exit if we found a winning line.
            cards_left = game.state.cards_left
            bdrs = [
                id_ for id_ in game.state.all_ids
                if id_ not in game.state.trash_set
                and game.state.is_useful(id_)
                and game.state.is_critical(id_)
                and id_.rank != 5
            ]
            if winrate == Fraction(1, 1) or (
                len(bdrs) == 1 and cards_left > 1 and winrate == Fraction(cards_left - 1, cards_left)
            ):
                return [perform], winrate

            if winrate > best_winrate:
                best_actions = [perform]
                best_winrate = winrate
            elif winrate > 0 and winrate == best_winrate:
                best_actions.append(perform)

        if not best_actions:
            return ""
        return best_actions, best_winrate

    @staticmethod
    def _perform_to_action(perform: PerformAction, game: Game, player_index: int):
        """Convert a PerformAction to the corresponding Action (Clue/Play/Discard)."""
        from hanabi_bot.basics.action import (
            ClueAction,
            DiscardAction,
            PerformColour,
            PerformRank,
            PlayAction,
        )
        from hanabi_bot.basics.clue import BaseClue, ClueKind

        state = game.state
        if isinstance(perform, PerformPlay):
            order = perform.target
            if order < len(state.deck):
                deck_id = state.deck[order].id()
                if deck_id is not None:
                    return PlayAction(player_index, order, deck_id.suit_index, deck_id.rank)
            return PlayAction(player_index, order, -1, -1)
        if isinstance(perform, PerformDiscard):
            order = perform.target
            if order < len(state.deck):
                deck_id = state.deck[order].id()
                if deck_id is not None:
                    return DiscardAction(player_index, order, deck_id.suit_index, deck_id.rank, False)
            return DiscardAction(player_index, order, -1, -1, False)
        if isinstance(perform, PerformColour):
            clue = BaseClue(ClueKind.COLOUR, perform.value)
            touched = tuple(state.clue_touched(state.hands[perform.target], 0, perform.value))
            return ClueAction(player_index, perform.target, touched, clue)
        if isinstance(perform, PerformRank):
            clue = BaseClue(ClueKind.RANK, perform.value)
            touched = tuple(state.clue_touched(state.hands[perform.target], 1, perform.value))
            return ClueAction(player_index, perform.target, touched, clue)
        raise ValueError(f"Cannot convert {perform!r} to Action")


def _fmt_remaining(remaining: RemainingMap, state) -> str:  # type: ignore[no-untyped-def]
    return ", ".join(f"{state.log_id(id_)}({m})" for id_, m in remaining.items())
