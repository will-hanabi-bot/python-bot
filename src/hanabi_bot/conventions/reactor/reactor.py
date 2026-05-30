"""Reactor convention: Game subclass + dispatchers + take_action.

Port of scala-bot/src/scala_bot/reactor/reactor.scala.

Endgame solver: ENABLED. When `state.rem_score <= len(state.variant.suits) + 1`,
`take_action` invokes `hanabi_bot.endgame.EndgameSolver` for a Monte Carlo solve
with a 10-second timeout; falls back to the heuristic if winrate is below 1% or
the solver bails out.

Iterative-debugging note: reactor's interpret_clue dispatch is intricate and depends
on `connectable_simple`, `check_fix`, the clue-result statistics, and per-perspective
elim. Some scenarios may need additional fixes — see the test suite under
tests/test_reactor/ for the canonical behaviors.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from fractions import Fraction

from hanabi_bot.basics.action import (
    Action,
    ClueAction,
    DiscardAction,
    PerformAction,
    PerformColour,
    PerformDiscard,
    PerformPlay,
    PerformRank,
    PlayAction,
    TurnAction,
)
from hanabi_bot.basics.card import CardStatus
from hanabi_bot.basics.clue import BaseClue, Clue, ClueKind
from hanabi_bot.basics.fix import FixResultNormal, check_fix
from hanabi_bot.basics.game import Game
from hanabi_bot.basics.identity import Identity, IdentitySet
from hanabi_bot.basics.interp import ClueInterp, DiscardInterp, PlayInterp
from hanabi_bot.basics.player import SarcasticLink, gen_players
from hanabi_bot.basics.sarcastic import (
    DiscardResultGentlemansDiscard,
    DiscardResultMistake,
    DiscardResultNone,
    DiscardResultSarcastic,
    interpret_useful_dc,
)
from hanabi_bot.basics.state import State

log = logging.getLogger("hanabi_bot.reactor")


@dataclass(frozen=True, slots=True)
class ReactorWC:
    """A waiting reactive connection: the team is awaiting the reacter's response."""

    giver: int
    reacter: int
    receiver: int
    receiver_hand: tuple[int, ...]
    clue: BaseClue
    focus_slot: int
    inverted: bool
    turn: int


@dataclass(frozen=True, slots=True)
class Reactor(Game):
    """Game subclass implementing the reactor convention."""

    waiting: ReactorWC | None = None
    zcs_turn: int | None = None  # turn that initiated "zero clue starvation" (8-clue lock state)

    # --- Construction ---

    @classmethod
    def create(cls, table_id: int, state: State, in_progress: bool = False) -> Reactor:
        """Build a fresh Reactor at turn 0. Convention-specific fields use defaults."""
        players, common = gen_players(state)
        last_actions = tuple(None for _ in range(state.num_players))
        base = (state, (), players, common)
        return cls(
            table_id=table_id,
            state=state,
            players=players,
            common=common,
            base=base,
            deck_ids=tuple(None for _ in range(state.cards_total)),
            future=tuple(state.all_ids for _ in range(state.cards_total)),
            last_actions=last_actions,
            in_progress=in_progress,
            good_touch=False,
        )

    # --- Convention hooks ---

    def filter_playables(self, player, player_index, orders, assume=True):  # type: ignore[no-untyped-def, override]
        """Filter out playables that look like they'd connect to a later-queued card.

        Port of reactor.scala lines 48-59.
        """
        order_list = list(orders)

        def should_keep(o: int) -> bool:
            if player.thoughts[o].id(infer=True) is not None:
                return True
            # Has a "max" link entry — if this isn't the max, the larger sibling will resolve first.
            for link in player.links:
                if o in link.orders and max(link.orders) != o:
                    return True
            # Another card was queued earlier that this could connect to (i.e. this is a follow-up
            # play on the same suit). In that case keep this card.
            for o2 in order_list:
                if o == o2:
                    continue
                o2_id = player.thoughts[o2].id(infer=True)
                # If o2 has a known id, see if its next would be in o's inferred.
                if o2_id is not None and o2_id.next is not None and o2_id.next in player.thoughts[o].inferred:
                    continue
                # Both have signal_turn AND o was signalled after o2 → o connects.
                o2_signal = self.meta[o2].signal_turn
                o_signal = self.meta[o].signal_turn
                if o2_signal is not None and o_signal is not None and o_signal > o2_signal:
                    return False
            return True

        return [o for o in order_list if should_keep(o)]

    def valid_arr(self, id_: Identity, order: int) -> bool:
        """Reactor: respect info_lock when assigning identities."""
        info_lock = self.me.thoughts[order].info_lock
        return info_lock is None or id_ in info_lock

    @property
    def in_endgame(self) -> bool:
        """Reactor: endgame starts one turn earlier than the Game default."""
        return self.state.pace < self.state.num_players - 1

    # --- Helper methods ---

    def chop(self, player_index: int) -> int | None:
        """The discard candidate for player_index.

        Priority:
        1. A card marked CalledToDiscard.
        2. The newest unclued card with no conventional status (zcs_turn gates: card drawn before zcs).

        Port of reactor.scala lines 68-75.
        """
        # First pass: explicit CalledToDiscard
        for o in self.state.hands[player_index]:
            if self.meta[o].status == CardStatus.CALLED_TO_DISCARD:
                return o
        # Second pass: newest unclued + status NONE
        for o in self.state.hands[player_index]:
            zcs_ok = self.zcs_turn is None or self.zcs_turn >= self.state.deck[o].turn_drawn
            if (
                zcs_ok
                and not self.state.deck[o].clued
                and self.meta[o].status == CardStatus.NONE
            ):
                return o
        return None

    @property
    def has_ptd(self) -> bool:
        """Whether the next player (Bob) has permission to discard."""
        player_index = self.state.current_player_index
        zelda = self.state.last_player_index(player_index)
        bob = self.state.next_player_index(player_index)
        bob_chop = self.chop(bob)
        if bob_chop is None:
            bob_chop_id: Identity | None = None
        else:
            bob_chop_id = self.state.deck[bob_chop].id()

        def known_dupe() -> bool:
            if bob_chop_id is None:
                return False
            for o in self.state.hands[bob]:
                if o == bob_chop:
                    continue
                if (
                    self.players[zelda].thoughts[o].matches(bob_chop_id)
                    and self.me.thoughts[o].matches(bob_chop_id)
                ):
                    return True
            return False

        def unknown_play() -> bool:
            last = self.last_actions[zelda]
            if not isinstance(last, PlayAction):
                return False
            if bob_chop_id is None:
                return False
            played_id = Identity(last.suit_index, last.rank)
            if played_id != bob_chop_id:
                return False
            old_inf = self.common.thoughts[last.order].old_inferred
            return old_inf is None or old_inf != IdentitySet.single(played_id)

        if self.common.obvious_loaded(self, bob):
            return True
        if bob_chop_id is not None and self.state.is_critical(bob_chop_id):
            return False
        if bob_chop_id is not None and self.state.is_basic_trash(bob_chop_id):
            return not unknown_play()
        if known_dupe():
            return True
        return not (bob_chop_id is not None and (self.state.is_playable(bob_chop_id) or bob_chop_id.rank == 2))

    def reinterp_play(self, prev: Reactor, action: PlayAction | DiscardAction) -> Reactor | None:
        """If our gentleman's-discard turned out wrong, replay the game from that point.

        Port of reactor.scala lines 108-128.
        """
        if isinstance(action, PlayAction):
            order, suit_index, rank = action.order, action.suit_index, action.rank
        else:
            order, suit_index, rank = action.order, action.suit_index, action.rank
        if suit_index == -1 or rank == -1:
            return None

        needs_replay = (
            action.player_index == self.state.our_player_index
            and prev.me.thoughts[order].possible.length > 1
            and prev.meta[order].status == CardStatus.GENTLEMANS_DISCARD
            and self.future[order].length > 1
        )
        if not needs_replay:
            return None

        new_future = (
            *self.future[:order],
            IdentitySet.single(Identity(suit_index, rank)),
            *self.future[order + 1:],
        )
        new_game = self.copy_with(future=new_future)
        try:
            return new_game.replay(self.state.deck[order].turn_drawn)  # type: ignore[return-value]
        except (ValueError, RuntimeError):
            return None

    @staticmethod
    def _check_missed(game: Reactor, player_index: int, action_order: int) -> Reactor:
        """If a player skipped an urgent card, clear the urgent flag and reset its inferences.

        Port of reactor.scala `checkMissed` (lines 152-164).
        """
        urgent_order = None
        for o in game.state.hands[player_index]:
            if game.meta[o].urgent and o != action_order:
                urgent_order = o
                break
        if urgent_order is None:
            return game

        def _restore(t):  # type: ignore[no-untyped-def]
            if t.old_inferred is None:
                raise RuntimeError(f"No old inferred on {urgent_order}!")
            return dataclasses.replace(t, inferred=t.old_inferred, old_inferred=None, info_lock=None)

        new_common = game.common.with_thought(urgent_order, _restore)
        new_meta = game.meta
        cleared = game.meta[urgent_order].cleared().reason(game.state.turn_count)
        new_meta = (*new_meta[:urgent_order], cleared, *new_meta[urgent_order + 1:])
        return game.copy_with(common=new_common, meta=new_meta)

    @staticmethod
    def _reset_zcs(game: Reactor) -> Reactor:
        return game.copy_with(zcs_turn=None)

    # --- Convention dispatchers (interpret_clue/discard/play, update_turn) ---

    def interpret_clue(self, prev: Game, action: ClueAction) -> Reactor:
        """Interpret a clue: try stable, fall back to reactive, then run elim.

        Port of reactor.scala `interpretClue` (lines 204-284).
        """
        # Lazy import to avoid circular module load.
        from .interpret_clue import interpret_reactive, interpret_stable

        assert isinstance(prev, Reactor)
        state = self.state
        game = Reactor._check_missed(self, action.giver, 99)

        # Clear waiting if the giver was the reacter (they acted by giving instead of playing).
        if game.waiting is not None and game.waiting.reacter == action.giver:
            game = game.copy_with(waiting=None)

        interp: ClueInterp | None
        interp_game: Reactor

        if game.next_interp is not None:
            forced = game.next_interp
            assert isinstance(forced, ClueInterp)
            if forced == ClueInterp.REACTIVE:
                reacter = state.next_player_index(action.giver)
                interp, interp_game = interpret_reactive(
                    prev, game, action, reacter, looks_stable=True
                )
            else:
                interp, interp_game = interpret_stable(prev, game, action, stall=False)
        elif game.state.options.empty_clues and len(action.list_) == 0:
            interp, interp_game = ClueInterp.USELESS, game
        elif (
            prev.common.obvious_locked(prev, action.giver)
            or game.in_endgame
            or prev.state.clue_tokens == 8
        ):
            # Stable-by-default branch: Alice is locked/at 8 clues/in endgame, so the bot
            # would normally treat this as a stable (possibly stall) clue.
            #
            # Exception: if Alice clues Cathy (giver + 2) while Bob (giver + 1) has no
            # pending play to make ("Bob is not loaded"), disallow stable and interpret
            # the clue reactively with Bob as the reacter. The reasoning is that an
            # Alice→Cathy clue that skips an unloaded Bob has no path to act on the
            # signal stably — Bob would just discard or do nothing — so the only sound
            # reading is reactive (the cluepair targets Bob's reaction).
            bob = state.next_player_index(action.giver)
            cathy = state.next_player_index(bob)
            target_is_cathy = action.target == cathy and cathy != action.giver
            bob_unloaded = (
                target_is_cathy
                and not prev.common.obvious_playables(prev, bob)
            )
            if bob_unloaded:
                interp, interp_game = interpret_reactive(
                    prev, game, action, reacter=bob, looks_stable=False
                )
            else:
                interp, interp_game = interpret_stable(prev, game, action, stall=True)
        else:
            # Find the reacter (first non-giver, non-us player without obvious playables).
            reacter: int | None = None
            for i in range(1, state.num_players):
                pi = (action.giver + i) % state.num_players
                old_play = prev.common.obvious_playables(prev, pi)
                new_play = game.common.obvious_playables(game, pi)
                playables = [o for o in old_play if o in new_play]
                if not playables:
                    reacter = pi
                    break

            fixed_result = check_fix(prev, game, action)
            if isinstance(fixed_result, FixResultNormal):
                fixed = list(fixed_result.clued_resets) + list(fixed_result.duplicate_reveals)
            else:
                fixed = []
            allowable_fix = action.target == state.next_player_index(action.giver) and bool(fixed)

            if reacter is None:
                interp = ClueInterp.FIX if allowable_fix else None
                interp_game = game
            elif reacter == action.target:
                interp, interp_game = interpret_stable(prev, game, action, stall=False)
            else:
                prev_playables = prev.players[action.target].obvious_playables(
                    prev, action.target
                )
                if allowable_fix and any(o in prev_playables for o in fixed):
                    interp, interp_game = ClueInterp.FIX, game
                else:
                    interp, interp_game = interpret_reactive(
                        prev, game, action, reacter, looks_stable=False
                    )

        if interp is None:
            interp = ClueInterp.MISTAKE
        interp_game = interp_game.with_move(interp)

        # Identify newly-signalled plays before elim.
        signalled_plays = [
            o
            for hand in interp_game.state.hands
            for o in hand
            if prev.meta[o].status != CardStatus.CALLED_TO_PLAY
            and interp_game.meta[o].status == CardStatus.CALLED_TO_PLAY
        ]
        eliminated = interp_game.elim()
        plays_after = [
            o
            for hand in eliminated.state.hands
            for o in hand
            if eliminated.meta[o].status == CardStatus.CALLED_TO_PLAY
        ]
        result = eliminated
        if len(plays_after) < len(signalled_plays):
            result = result.with_move(ClueInterp.MISTAKE, overwrite=True)
        if prev.state.can_clue:
            result = Reactor._reset_zcs(result)
        if not result.state.can_clue:
            result = result.copy_with(zcs_turn=self.state.turn_count)
        return result.copy_with(next_interp=None)

    def interpret_discard(self, prev: Game, action: DiscardAction) -> Reactor:
        """Interpret a discard: handle waiting reactions, useful-dc cases, then elim.

        Port of reactor.scala `interpretDiscard` (lines 286-355).
        """
        from .interpret_reaction import react_discard

        assert isinstance(prev, Reactor)
        state = self.state
        suit_index, rank, failed = action.suit_index, action.rank, action.failed
        id_ = Identity(suit_index, rank) if suit_index != -1 and rank != -1 else None
        game = Reactor._check_missed(self, action.player_index, action.order)

        if failed:
            # Bombed! Clear all conventional info.
            new_common = game.common
            new_meta = list(game.meta)
            for hand in state.hands:
                for o in hand:
                    new_common = new_common.with_thought(
                        o,
                        lambda t: dataclasses.replace(
                            t, inferred=t.possible, old_inferred=None, info_lock=None
                        ),
                    )
                    new_meta[o] = new_meta[o].cleared()
            game = game.copy_with(waiting=None, common=new_common, meta=tuple(new_meta))

        useful_dc = (
            not failed
            and prev.state.deck[action.order].clued
            and id_ is not None
            and state.is_useful(id_)
            and prev.meta[action.order].status != CardStatus.CALLED_TO_DISCARD
            and not (
                prev.common.thinks_locked(prev, action.player_index) and prev.state.clue_tokens == 0
            )
        )

        if game.waiting is not None:
            game = react_discard(prev, game, action.player_index, action.order, game.waiting)
        elif useful_dc and id_ is not None:
            dc_result = interpret_useful_dc(game, action)
            if isinstance(dc_result, DiscardResultNone):
                game = game.with_move(DiscardInterp.NONE)
            elif isinstance(dc_result, DiscardResultMistake):
                game = game.with_move(DiscardInterp.MISTAKE)
            elif isinstance(dc_result, DiscardResultGentlemansDiscard):
                # For each gd target, mark CalledToPlay (hidden until the chain plays).
                targets = dc_result.orders
                hypo = game.state
                for o in targets:
                    hidden = o != targets[-1]
                    inferred = hypo.playable_set if hidden else IdentitySet.single(id_)
                    me_id = game.me.thoughts[o].id()
                    if me_id is not None:
                        hypo = hypo.with_play(me_id)
                    game = game.copy_with(
                        common=game.common.with_thought(
                            o, lambda t, _i=inferred: dataclasses.replace(t, inferred=_i)
                        )
                    )
                    new_meta = list(game.meta)
                    new_meta[o] = dataclasses.replace(
                        new_meta[o], status=CardStatus.GENTLEMANS_DISCARD, hidden=hidden
                    )
                    game = game.copy_with(meta=tuple(new_meta))
                game = game.with_move(DiscardInterp.GENTLEMANS_DISCARD)
            elif isinstance(dc_result, DiscardResultSarcastic):
                game = game.copy_with(
                    common=dataclasses.replace(
                        game.common,
                        links=(SarcasticLink(dc_result.orders, id_), *game.common.links),
                    )
                ).with_move(DiscardInterp.SARCASTIC)
        else:
            game = game.with_move(DiscardInterp.NONE)

        game = game.elim()
        if prev.state.can_clue:
            game = Reactor._reset_zcs(game)
        return game

    def interpret_play(self, prev: Game, action: PlayAction) -> Reactor:
        """Interpret a play: handle waiting reactions, then elim.

        Port of reactor.scala `interpretPlay` (lines 357-368).
        """
        from .interpret_reaction import react_play

        assert isinstance(prev, Reactor)
        replayed = self.reinterp_play(prev, action)
        if replayed is not None:
            return replayed

        game = Reactor._check_missed(self, action.player_index, action.order)
        if game.waiting is not None:
            game = react_play(prev, game, action.player_index, action.order, game.waiting)
        game = game.with_move(PlayInterp.NONE, overwrite=True).elim()
        if prev.state.can_clue:
            game = Reactor._reset_zcs(game)
        return game

    def eval_action(self, action: Action) -> float:
        """Reactor's action evaluator: delegates to state_eval."""
        from .state_eval import eval_action

        return eval_action(self, action)

    def find_all_clues(self, giver: int) -> list[PerformAction]:
        """Enumerate clue candidates the giver could give, sorted by heuristic value.

        Port of reactor.scala `findAllClues` (lines 565-621). Filters out clues that
        produce mistake interpretations and ranks the rest by `get_result`.
        """
        from hanabi_bot.basics.interp import ClueInterp

        from .state_eval import get_result

        state = self.state
        added_useless_clue = False
        scored: list[tuple[Clue, float]] = []

        for target in range(state.num_players):
            if target == giver:
                continue
            for clue in state.all_valid_clues(target):
                list_orders = state.clue_touched(state.hands[target], clue.kind.value, clue.value)
                # Only touches previously-clued trash → mostly useless.
                if list_orders and all(
                    state.deck[o].clued
                    and (id_ := state.deck[o].id()) is not None
                    and state.is_basic_trash(id_)
                    for o in list_orders
                ):
                    if added_useless_clue:
                        continue
                    added_useless_clue = True
                    scored.append((clue, 0.0))
                    continue

                action = ClueAction(giver, clue.target, tuple(list_orders), clue.base)
                hypo = self.simulate_clue(action)
                assert isinstance(hypo, Reactor)
                if hypo.last_move == ClueInterp.MISTAKE:
                    continue

                clue_result = get_result(self, hypo, action)
                useful = clue_result > -1 and (
                    hypo.last_move == ClueInterp.REACTIVE
                    or hypo.common.hypo_score > self.common.hypo_score
                    or any(
                        (self.deck_ids[o] is None or state.is_useful(self.deck_ids[o]))
                        and hypo.state.deck[o].clued
                        and hypo.common.thoughts[o].possible.length
                        < self.common.thoughts[o].possible.length
                        for o in state.hands[clue.target]
                    )
                )
                if useful:
                    scored.append((clue, clue_result))
                elif not added_useless_clue:
                    added_useless_clue = True
                    scored.append((clue, 0.0))
                # else: skip; redundant useless clue

        scored.sort(key=lambda t: -t[1])
        out: list[PerformAction] = []
        for clue, _ in scored:
            if clue.kind == ClueKind.COLOUR:
                out.append(PerformColour(clue.target, clue.value))
            else:
                out.append(PerformRank(clue.target, clue.value))
        return out

    def find_all_discards(self, player_index: int) -> list[PerformAction]:
        """Enumerate discard candidates for player_index — just one preferred target.

        Port of reactor.scala `findAllDiscards` (lines 623-630). Returns the trash
        head, else chop, else locked_discard.
        """
        trash = self.common.thinks_trash(self, player_index)
        if trash:
            target = trash[0]
        else:
            chop = self.chop(player_index)
            target = (
                chop
                if chop is not None
                else self.players[player_index].locked_discard(self.state, player_index)
            )
        return [PerformDiscard(target)]

    def update_turn(self, action: TurnAction) -> Reactor:
        """Run on every TurnAction. Clears stale waiting, advances queued play inferences.

        Port of reactor.scala `updateTurn` (lines 370-400).
        """
        current_player_index = action.current_player_index
        state = self.state
        if current_player_index == -1:
            return self

        # Find queued playable that the current player should play.
        candidates = [
            o
            for o in state.hands[current_player_index]
            if self.meta[o].status == CardStatus.CALLED_TO_PLAY
            and self.common.thoughts[o].id(infer=True) is None
        ]
        next_queued_playable: int | None = None
        if candidates:
            next_queued_playable = min(
                candidates, key=lambda o: self.meta[o].signal_turn or 99
            )

        game = self
        if (
            game.waiting is not None
            and game.waiting.reacter == state.last_player_index(current_player_index)
        ):
            game = game.copy_with(waiting=None)

        if next_queued_playable is not None:
            order = next_queued_playable
            new_inferred = game.common.thoughts[order].inferred.intersect(state.playable_set)
            if new_inferred.is_empty:
                new_common = game.common.with_thought(order, lambda t: t.reset_inferences())
                new_meta = list(game.meta)
                new_meta[order] = dataclasses.replace(
                    new_meta[order], status=CardStatus.NONE, by=None, trash=True
                )
                game = game.copy_with(common=new_common, meta=tuple(new_meta))
            else:
                game = game.copy_with(
                    common=game.common.with_thought(
                        order, lambda t, _ni=new_inferred: dataclasses.replace(t, inferred=_ni)
                    )
                )
        return game.elim()

    # --- take_action ---

    def take_action(self) -> PerformAction:
        """Pick the bot's action. Returns sync (no async I/O at this layer)."""
        from .state_eval import eval_action

        state, me = self.state, self.me
        next_player_index = state.next_player_index(state.our_player_index)

        # Handle urgent (signalled-to-play or signalled-to-discard) cards.
        urgent_order = None
        for o in state.our_hand:
            if self.meta[o].urgent:
                urgent_order = o
                break
        urgent_action: PerformAction | None = None
        if urgent_order is not None:
            urgent_bob_save = (
                state.can_clue
                and self.waiting is not None
                and self.waiting.reacter == state.our_player_index
                and self.waiting.receiver != next_player_index
                and not self.common.obvious_loaded(self, next_player_index)
            )
            if urgent_bob_save:
                bob_chop = self.copy_with(zcs_turn=None).chop(next_player_index)
                if bob_chop is not None:
                    bob_chop_id = state.deck[bob_chop].id()
                    if bob_chop_id is not None and state.is_critical(bob_chop_id):
                        # Try to clue Bob instead.
                        clues = state.all_valid_clues(next_player_index) if state.can_clue else []
                        if clues:
                            best = max(clues, key=lambda c: _clue_eval_value(self, c))
                            urgent_action = (
                                PerformColour(best.target, best.value)
                                if best.kind == ClueKind.COLOUR
                                else PerformRank(best.target, best.value)
                            )
            if urgent_action is None:
                status = self.meta[urgent_order].status
                thought = me.thoughts[urgent_order]
                if status == CardStatus.CALLED_TO_PLAY and not thought.possible.forall(
                    state.is_basic_trash
                ):
                    urgent_action = PerformPlay(urgent_order)
                elif status == CardStatus.CALLED_TO_DISCARD and not thought.possible.forall(
                    state.is_critical
                ):
                    urgent_action = PerformDiscard(urgent_order)

        # Endgame solver: Monte Carlo over deck permutations. Runs BEFORE the
        # urgent-action shortcut so a stall clue can override an unwinnable
        # called-to-play directive (see replay 1875304 turn 22). If the solver
        # bails or doesn't find a winning line, fall back to the urgent action
        # so non-endgame conventional behavior is unchanged.
        if state.rem_score <= len(state.variant.suits) + 1:
            from hanabi_bot.endgame.solver import EndgameSolver

            log.info("entering endgame solver (rem_score=%d)", state.rem_score)
            try:
                result = EndgameSolver(monte_carlo=True, timeout=30.0).solve(self)
            except Exception:
                log.exception("endgame solver crashed; falling back to heuristic")
                result = "exception"
            if isinstance(result, tuple):
                perform, winrate = result
                if winrate >= Fraction(1, 100):
                    log.info("endgame solved: %s winrate=%s", perform, winrate)
                    return perform
                log.info("endgame winrate below 1%% (%s); falling back to heuristic", winrate)
            else:
                log.info("endgame solver bailed: %r; falling back to heuristic", result)

        if urgent_action is not None:
            return urgent_action

        # Find playable orders.
        common_p = self.common.obvious_playables(self, state.our_player_index)
        known_p = me.obvious_playables(self, state.our_player_index)

        possible_connectors: list[int] = []
        if (
            common_p
            and self.waiting is not None
            and self.waiting.receiver == state.our_player_index
        ):
            reacter = self.waiting.reacter
            for p in common_p:
                for i in me.thoughts[p].inferred:
                    nxt = i.next
                    if nxt is None:
                        continue
                    if any(me.thoughts[o].matches(nxt) for o in state.hands[reacter]):
                        possible_connectors.append(p)
                        break

        if possible_connectors:
            target = min(possible_connectors, key=lambda o: self.meta[o].signal_turn or 99)
            playable_orders: list[int] = [target]
        elif known_p:
            playable_orders = [
                order
                for order in known_p
                if self.meta[order].status == CardStatus.CALLED_TO_PLAY
                or not any(
                    o != order
                    and me.thoughts[o].possible == me.thoughts[order].possible
                    and self.meta[o].focused
                    for o in state.hands[state.our_player_index]
                )
            ]
        else:
            playable_orders = me.thinks_playables(self, state.our_player_index)

        can_clue_now = state.can_clue and (
            self.waiting is None or self.waiting.receiver != state.our_player_index
        )

        all_clues: list[tuple[PerformAction, Action]] = []
        if can_clue_now:
            for target in range(state.num_players):
                if target == state.our_player_index:
                    continue
                for clue in state.all_valid_clues(target):
                    perform: PerformAction = (
                        PerformColour(clue.target, clue.value)
                        if clue.kind == ClueKind.COLOUR
                        else PerformRank(clue.target, clue.value)
                    )
                    act = ClueAction(
                        state.our_player_index,
                        clue.target,
                        tuple(state.clue_touched(state.hands[target], clue.kind.value, clue.value)),
                        clue.base,
                    )
                    all_clues.append((perform, act))

        all_plays: list[tuple[PerformAction, Action]] = []
        for o in playable_orders:
            inferred = me.thoughts[o].id(infer=True)
            if inferred is not None:
                act_ = PlayAction(
                    state.our_player_index, o, inferred.suit_index, inferred.rank
                )
            else:
                act_ = PlayAction(state.our_player_index, o, -1, -1)
            all_plays.append((PerformPlay(o), act_))

        # Forced-play detection: avoid discard if reacter might play on top.
        potential_forced_play = False
        if all_plays and self.waiting is not None and self.waiting.reacter == next_player_index:
            for o in playable_orders:
                for id_ in me.thoughts[o].inferred:
                    nxt = id_.next
                    if nxt is None:
                        continue
                    for o2 in state.hands[next_player_index]:
                        deck_id = state.deck[o2].id()
                        if deck_id is not None and deck_id == nxt:
                            potential_forced_play = True
                            break
                    if potential_forced_play:
                        break
                if potential_forced_play:
                    break

        cant_discard = (
            state.clue_tokens == 8
            or (state.pace == 0 and (all_clues or all_plays))
            or potential_forced_play
        )

        all_discards: list[tuple[PerformAction, Action]] = []
        if not cant_discard:
            trash = me.thinks_trash(self, state.our_player_index)
            if trash:
                expected = trash
            elif not me.obvious_locked(self, state.our_player_index) and not all_plays and self.has_ptd:
                chop_o = self.chop(state.our_player_index)
                expected = [chop_o] if chop_o is not None else []
            else:
                expected = []

            if self.waiting is not None and self.waiting.receiver == state.our_player_index:
                discard_orders = list(expected)
            else:
                discardable = me.discardable(self, state.our_player_index)
                discard_orders = list(dict.fromkeys([*expected, *discardable]))

            for o in discard_orders:
                inferred = me.thoughts[o].id(infer=True)
                if inferred is not None:
                    act_d = DiscardAction(
                        state.our_player_index, o, inferred.suit_index, inferred.rank, False
                    )
                else:
                    act_d = DiscardAction(state.our_player_index, o, -1, -1, False)
                all_discards.append((PerformDiscard(o), act_d))

        all_actions = all_clues + all_plays + all_discards
        if not all_actions:
            if state.clue_tokens == 8:
                return PerformPlay(state.our_hand[0])
            return PerformDiscard(me.locked_discard(state, state.our_player_index))
        return max(all_actions, key=lambda pa: eval_action(self, pa[1]))[0]


def _clue_eval_value(game: Reactor, clue: Clue) -> float:
    """Helper: score a clue using the convention's eval_action."""
    from .state_eval import eval_action

    (
        PerformColour(clue.target, clue.value)
        if clue.kind == ClueKind.COLOUR
        else PerformRank(clue.target, clue.value)
    )
    act = ClueAction(
        game.state.our_player_index,
        clue.target,
        tuple(
            game.state.clue_touched(
                game.state.hands[clue.target], clue.kind.value, clue.value
            )
        ),
        clue.base,
    )
    return eval_action(game, act)
