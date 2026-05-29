"""Game: holds the full game tree, dispatches action handlers, applies elim.

Port of scala-bot/src/scala_bot/basics/Game.scala (the trait + extension methods)
AND scala-bot/src/scala_bot/basics/basics.scala (the action handlers onClue/
onDiscard/onDraw/onPlay/elim).

Stage 2a includes:
- Game frozen dataclass + copy_with
- Note dataclass
- Action handlers: on_clue, on_discard, on_draw, on_play
- handle_action dispatcher
- Generic helpers: with_thought, with_meta, with_state, with_card, with_id
- Status predicates: is_touched, is_blind_playing, is_saved, order_matches, known_as
- Default no-op interpret_clue/discard/play/update_turn (convention subclasses override)

DEFERRED to Stage 2b:
- The real elim() (currently a stub that clears dirty)
- update_notes (Stage 5)
- simulate / rewind / replay / navigate / analyze (Stage 6 debugging)
- get_note (Stage 5 notes-to-server)
- take_action / find_all_clues / find_all_discards / eval_action (Stage 4 convention API)
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field

from .action import (
    Action,
    ClueAction,
    DiscardAction,
    DrawAction,
    GameOverAction,
    InterpAction,
    PlayAction,
    StatusAction,
    StrikeAction,
    TurnAction,
)
from .card import Card, CardStatus, ConvData, Thought
from .clue import CardClue
from .identity import Identity, IdentitySet
from .interp import Interp
from .player import Player, gen_players
from .state import HAND_SIZE, State


@dataclass(frozen=True, slots=True)
class Note:
    """A note written on a card for display in hanab.live.

    :param turn: Most recent turn the note was updated.
    :param last: Most recent specific note (ignoring older entries).
    :param full: Full appended note across turns.
    """

    turn: int
    last: str
    full: str


def _perform_from_action(action: Action):  # type: ignore[no-untyped-def]
    """Convert a recorded Action back to its corresponding PerformAction. Used by analyze."""
    from .action import (
        PerformColour,
        PerformDiscard,
        PerformPlay,
        PerformRank,
    )

    if isinstance(action, ClueAction):
        if action.clue.kind.value == 0:
            return PerformColour(action.target, action.clue.value)
        return PerformRank(action.target, action.clue.value)
    if isinstance(action, DiscardAction):
        if action.failed:
            return PerformPlay(action.order)
        return PerformDiscard(action.order)
    if isinstance(action, PlayAction):
        return PerformPlay(action.order)
    raise ValueError(f"Cannot convert {action} to PerformAction")


def _heuristic_eval(game, perform) -> float:  # type: ignore[no-untyped-def]
    """Eval a PerformAction by simulating it. Convention subclasses override eval_action."""
    # Convert PerformAction -> Action by constructing the corresponding game action.
    from .action import (
        ClueAction,
        DiscardAction,
        PerformColour,
        PerformDiscard,
        PerformPlay,
        PerformRank,
        PlayAction,
    )
    from .clue import BaseClue, ClueKind

    state = game.state
    if isinstance(perform, PerformPlay):
        deck_id = state.deck[perform.target].id() if perform.target < len(state.deck) else None
        if deck_id is None:
            act = PlayAction(state.current_player_index, perform.target, -1, -1)
        else:
            act = PlayAction(
                state.current_player_index, perform.target, deck_id.suit_index, deck_id.rank
            )
    elif isinstance(perform, PerformDiscard):
        deck_id = state.deck[perform.target].id() if perform.target < len(state.deck) else None
        if deck_id is None:
            act = DiscardAction(state.current_player_index, perform.target, -1, -1, False)
        else:
            act = DiscardAction(
                state.current_player_index, perform.target, deck_id.suit_index, deck_id.rank, False
            )
    elif isinstance(perform, PerformColour):
        clue = BaseClue(ClueKind.COLOUR, perform.value)
        touched = tuple(state.clue_touched(state.hands[perform.target], 0, perform.value))
        act = ClueAction(state.current_player_index, perform.target, touched, clue)
    elif isinstance(perform, PerformRank):
        clue = BaseClue(ClueKind.RANK, perform.value)
        touched = tuple(state.clue_touched(state.hands[perform.target], 1, perform.value))
        act = ClueAction(state.current_player_index, perform.target, touched, clue)
    else:
        return 0.0

    # Convention subclasses provide eval_action; default Game has no scoring.
    try:
        return float(game.eval_action(act))  # type: ignore[attr-defined]
    except AttributeError:
        return 0.0


def _add_action(
    action_list: tuple[tuple[Action, ...], ...], action: Action, turn: int
) -> tuple[tuple[Action, ...], ...]:
    """Insert an action into action_list at the given turn.

    Port of scala-bot/.../Action.scala's `addAction` (lines 278-288).
    No-op if the action is already present on that turn.
    Raises if the turn is beyond action_list.length + 1.
    """
    if turn < len(action_list):
        if action in action_list[turn]:
            return action_list
        return (*action_list[:turn], (*action_list[turn], action), *action_list[turn + 1:])
    if turn == len(action_list):
        return (*action_list, (action,))
    raise IndexError(f"Attempted to add action to turn {turn}, but action list had size {len(action_list)}")


@dataclass(frozen=True, slots=True)
class Game:
    """The full game tree at a point in time.

    Subclassed by convention implementations (Reactor, HGroup, RefSieve) which
    override the interpret_* methods. The default implementations are identity
    (no convention interpretation applied), making Game directly usable as a
    "no convention" base for tests and replay tooling.
    """

    table_id: int
    state: State
    players: tuple[Player, ...]
    common: Player
    # (state, meta, players, common) snapshot taken at game start; used for rewinds.
    base: tuple[State, tuple[ConvData, ...], tuple[Player, ...], Player]
    meta: tuple[ConvData, ...] = ()
    deck_ids: tuple[Identity | None, ...] = ()
    future: tuple[IdentitySet, ...] = ()
    catchup: bool = False
    notes: dict[int, Note] = field(default_factory=dict)
    last_actions: tuple[Action | None, ...] = ()
    move_history: tuple[Interp, ...] = ()
    queued_cmds: tuple[tuple[str, str], ...] = ()
    next_interp: Interp | None = None
    no_recurse: bool = False
    rewind_depth: int = 0
    in_progress: bool = True

    # Whether this convention observes Good Touch Principle. Conventions override.
    good_touch: bool = False

    @classmethod
    def create(cls, table_id: int, state: State) -> Game:
        """Build a fresh Game at turn 0 with empty meta and per-player Thoughts.

        Convention subclasses should provide their own factories that add their
        extra fields, but they can delegate the common setup to this.
        """
        players, common = gen_players(state)
        last_actions = tuple(None for _ in range(state.num_players))
        base = (state, (), players, common)
        return cls(
            table_id=table_id,
            state=state,
            players=players,
            common=common,
            base=base,
            last_actions=last_actions,
        )

    # --- Conventional hooks (override in convention subclasses) ---

    def interpret_clue(self, prev: Game, action: ClueAction) -> Game:
        """Apply convention-specific interpretation after on_clue. Default: identity."""
        return self

    def interpret_discard(self, prev: Game, action: DiscardAction) -> Game:
        return self

    def interpret_play(self, prev: Game, action: PlayAction) -> Game:
        return self

    def update_turn(self, action: TurnAction) -> Game:
        """Convention hook called on every TurnAction. Default: identity."""
        return self

    def filter_playables(
        self,
        player: Player,
        player_index: int,
        orders: list[int] | tuple[int, ...],
        assume: bool = True,
    ) -> list[int]:
        """Extra filter applied before Player.thinks_playables / obvious_playables returns.

        Conventions can override (e.g. 1s must be played in a specific order). Default: pass-through.
        """
        return list(orders)

    def valid_arr(self, id_: Identity, order: int) -> bool:
        """Whether assigning the given identity to the card with given order is valid.

        Conventions with good touch may disallow assigning trash to clued cards. Default: True.
        """
        return True

    def refresh_after_play(self, prev: Game, action: PlayAction) -> Game:
        """Hook after a play (used by update_hypo_stacks simulations). Default: identity."""
        return self

    def clean_hypo(self) -> Game:
        """Hook before update_hypo_stacks runs. Conventions can drop symmetric waiting connections. Default: identity."""
        return self

    def eval_action(self, action: Action) -> float:
        """Score an action. Conventions override; default is 0 (no preference)."""
        return 0.0

    def blank(self, keep_deck: bool = False) -> Game:
        """Return a copy reset to the base snapshot (used by rewind/replay).

        Convention subclasses with extra fields should override and reset them too.
        """
        base_state, base_meta, base_players, base_common = self.base
        deck_ids: tuple[Identity | None, ...] = (
            self.deck_ids
            if keep_deck
            else tuple(None for _ in range(base_state.cards_total))
        )
        return self.copy_with(
            state=base_state,
            meta=base_meta,
            players=base_players,
            common=base_common,
            deck_ids=deck_ids,
            last_actions=tuple(None for _ in range(base_state.num_players)),
            move_history=(),
            queued_cmds=(),
        )

    def with_move(self, interp: Interp, overwrite: bool = False) -> Game:
        """Append (or overwrite the latest) interpretation entry in move_history.

        Port of basics.scala lines 189-198. Convention interpret_* methods call this
        after deciding on a meaning for a clue/play/discard.
        """
        player_actions = sum(
            1 for turn in self.state.action_list for a in turn if a.is_player_action
        )
        if not overwrite:
            if len(self.move_history) == player_actions:
                raise RuntimeError(
                    f"trying to add move {interp} to full move history ({self.move_history})"
                )
            return self.copy_with(move_history=(*self.move_history, interp))
        if len(self.move_history) < player_actions:
            return self.copy_with(move_history=(*self.move_history, interp))
        return self.copy_with(
            move_history=(*self.move_history[:-1], interp)
        )

    def with_catchup(self, f: Callable[[Game], Game]) -> Game:
        """Run `f` with catchup=True, then restore catchup=False. Suppresses note/cmd queuing."""
        return f(self.copy_with(catchup=True)).copy_with(catchup=False)

    def simulate_clue(self, action: ClueAction, free: bool = False) -> Game:
        """Apply a hypothetical clue without sending it to the server.

        Port of basics.scala `simulateClue` (lines 295-315). Used by convention evaluators
        to score candidate clues.
        """
        def _inner(g: Game) -> Game:
            new_action_list = _add_action(g.state.action_list, action, g.state.turn_count)
            new_state = dataclasses.replace(
                g.state,
                action_list=new_action_list,
                clue_tokens=g.state.clue_tokens + (1 if free else 0),
            )
            g2 = g.copy_with(state=new_state)
            g2 = g2.on_clue(action).interpret_clue(g, action)
            new_last = (
                *g2.last_actions[: action.giver],
                action,
                *g2.last_actions[action.giver + 1:],
            )
            g2 = g2.copy_with(last_actions=new_last)
            g2 = g2.with_state(lambda s: dataclasses.replace(s, turn_count=s.turn_count + 1))
            return g2
        return self.with_catchup(_inner)

    def simulate_action(self, action: Action, draw: Identity | None = None) -> Game:
        """Apply a hypothetical action + draw + turn advance.

        Port of basics.scala `simulateAction` (lines 317-341). Used for game-tree exploration.
        """
        player_index = action.player_index

        def _inner(g: Game) -> Game:
            # If the action_list isn't already ending on a TurnAction, push one.
            if (
                len(g.state.action_list) > 1
                and g.state.action_list[-1]
                and not isinstance(g.state.action_list[-1][-1], TurnAction)
            ):
                g = g.handle_action(TurnAction(g.state.turn_count, player_index))
            g = g.handle_action(action)
            if action.requires_draw and g.state.cards_left > 0:
                order = g.state.next_card_order
                # Prefer pre-known deck id for this order; else use draw arg; else hidden.
                next_id: Identity | None = None
                if order < len(g.deck_ids) and g.deck_ids[order] is not None:
                    next_id = g.deck_ids[order]
                elif draw is not None:
                    next_id = draw
                if next_id is not None:
                    g = g.handle_action(
                        DrawAction(player_index, order, next_id.suit_index, next_id.rank)
                    )
                else:
                    g = g.handle_action(DrawAction(player_index, order, -1, -1))
            g = g.handle_action(TurnAction(g.state.turn_count, player_index))
            return g
        return self.with_catchup(_inner)

    def simulate(self, action: Action) -> Game:
        """Dispatch to simulate_action (works for any Action)."""
        return self.simulate_action(action)

    def rewind(self, turn: int, action: Action) -> Game:
        """Reset to base and replay the action list with `action` inserted at `turn`.

        Port of basics.scala `rewind` (lines 348-396). Raises on invalid turn / depth limit.
        """
        if turn < 1 or turn > len(self.state.action_list) + 1:
            raise ValueError(f"attempted to rewind to invalid turn {turn}")
        if turn < len(self.state.action_list) and action in self.state.action_list[turn]:
            raise ValueError("action was already rewinded")
        if self.rewind_depth > 4:
            raise ValueError("rewind depth went too deep")

        new_game = self.blank(keep_deck=True).copy_with(
            catchup=True, rewind_depth=self.rewind_depth + 1
        )
        # Replay turns 0..turn-1, then the new action, then the remaining turns.
        for t in self.state.action_list[:turn]:
            for a in t:
                if isinstance(a, DrawAction) and a.order in new_game.state.hands[a.player_index]:
                    continue
                new_game = new_game.handle_action(a)
        new_game = new_game.handle_action(action)
        for t in self.state.action_list[turn:]:
            for a in t:
                new_game = new_game.handle_action(a)

        # Fill in deck identities from our saved deck_ids snapshot.
        new_deck: list[Card] = []
        for c in new_game.state.deck:
            if c.id() is not None:
                new_deck.append(c)
            elif c.order < len(self.deck_ids) and self.deck_ids[c.order] is not None:
                d_id = self.deck_ids[c.order]
                new_deck.append(dataclasses.replace(c, suit_index=d_id.suit_index, rank=d_id.rank))
            else:
                new_deck.append(c)

        return new_game.copy_with(
            catchup=self.catchup,
            notes=self.notes,
            state=dataclasses.replace(new_game.state, deck=tuple(new_deck)),
            rewind_depth=self.rewind_depth,
        )

    def analyze(self) -> list[str]:
        """Walk the action list from each player's POV; emit comments where the bot's
        take_action diverges from the actual action.

        Port of basics.scala `analyze` (lines 466-539). The Scala source uses the
        EndgameSolver to compute winrate-based endgame comments; without it, the
        endgame branch falls back to the heuristic eval_action.
        """
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class Comment:
            turn: int
            note: str

        base_state = self.base[0]
        blank = self.blank(keep_deck=False)
        comments: list[Comment] = []

        for index in range(base_state.num_players):
            initial = blank.with_state(
                lambda s, _i=index: dataclasses.replace(s, our_player_index=_i)
            )
            initial = initial.copy_with(base=(initial.state, initial.meta, initial.players, initial.common))
            g = initial

            for turn in self.state.action_list:
                for action in turn:
                    if isinstance(action, InterpAction):
                        continue
                    if isinstance(action, DrawAction):
                        if action.player_index != index:
                            seen_id = self.deck_ids[action.order] if action.order < len(self.deck_ids) else None
                            if seen_id is not None:
                                a = DrawAction(
                                    action.player_index, action.order, seen_id.suit_index, seen_id.rank
                                )
                            else:
                                a = action
                        else:
                            a = DrawAction(action.player_index, action.order, -1, -1)
                        g = g.handle_action(a)
                        continue
                    if action.is_player_action and action.player_index == index:
                        try:
                            suggested = g.take_action()
                        except Exception:
                            suggested = None
                        actual_perform = _perform_from_action(action)
                        if suggested is not None and suggested != actual_perform:
                            try:
                                suggested_value = _heuristic_eval(g, suggested)
                                actual_value = _heuristic_eval(g, actual_perform)
                                diff = suggested_value - actual_value
                                if diff < 1:
                                    pass
                                elif diff > 50:
                                    comments.append(
                                        Comment(
                                            g.state.turn_count,
                                            f"mistake! prefer ({suggested}) over ({action})",
                                        )
                                    )
                                else:
                                    comments.append(
                                        Comment(
                                            g.state.turn_count,
                                            f"suggest ({suggested}) over ({action})",
                                        )
                                    )
                            except Exception:
                                pass
                        g = g.handle_action(action)
                    else:
                        g = g.handle_action(action)

        comments.sort(key=lambda c: c.turn)
        return [f"turn {c.turn}: {c.note}" for c in comments]

    def replay(self, turn: int) -> Game:
        """Replay the action list from base up to `turn` and return that game state.

        Port of basics.scala `replay` (lines 398-435). Used by reactor.reinterp_play.
        """
        if self.rewind_depth > 4:
            raise ValueError("rewind depth went too deep")

        new_game = self.blank(keep_deck=True).copy_with(
            catchup=True, rewind_depth=self.rewind_depth + 1
        )
        for t in self.state.action_list:
            for a in t:
                if isinstance(a, DrawAction) and a.order in new_game.state.hands[a.player_index]:
                    continue
                new_game = new_game.handle_action(a)

        new_deck: list[Card] = []
        for c in new_game.state.deck:
            if c.id() is not None:
                new_deck.append(c)
            elif c.order < len(self.deck_ids) and self.deck_ids[c.order] is not None:
                d_id = self.deck_ids[c.order]
                new_deck.append(dataclasses.replace(c, suit_index=d_id.suit_index, rank=d_id.rank))
            else:
                new_deck.append(c)

        return new_game.copy_with(
            catchup=self.catchup,
            notes=self.notes,
            state=dataclasses.replace(new_game.state, deck=tuple(new_deck)),
            rewind_depth=self.rewind_depth,
        )

    # --- Generic state updates ---

    def copy_with(self, **kw) -> Game:  # type: ignore[no-untyped-def]
        """Return a new Game with the named fields replaced. Type is preserved across subclasses."""
        return dataclasses.replace(self, **kw)

    def with_state(self, f: Callable[[State], State]) -> Game:
        return self.copy_with(state=f(self.state))

    def with_meta(self, order: int, f: Callable[[ConvData], ConvData]) -> Game:
        new_meta = (*self.meta[:order], f(self.meta[order]), *self.meta[order + 1:])
        return self.copy_with(meta=new_meta)

    def with_card(self, order: int, f: Callable[[Card], Card]) -> Game:
        deck = self.state.deck
        new_deck = (*deck[:order], f(deck[order]), *deck[order + 1:])
        return self.with_state(lambda s: dataclasses.replace(s, deck=new_deck))

    def with_thought(self, order: int, f: Callable[[Thought], Thought]) -> Game:
        """Update common's Thought at `order`. Per-player thoughts are synced by elim()."""
        return self.copy_with(common=self.common.with_thought(order, f))

    def with_id(self, order: int, id_: Identity) -> Game:
        """Write the identity into both state.deck[order] and game.deck_ids[order]."""
        deck = self.state.deck
        new_card = dataclasses.replace(deck[order], suit_index=id_.suit_index, rank=id_.rank)
        new_deck = (*deck[:order], new_card, *deck[order + 1:])
        new_state = dataclasses.replace(self.state, deck=new_deck)
        new_deck_ids = (*self.deck_ids[:order], id_, *self.deck_ids[order + 1:])
        return self.copy_with(state=new_state, deck_ids=new_deck_ids)

    # --- Status predicates ---

    def is_touched(self, order: int) -> bool:
        """A card is 'gotten' (clued, finessed, gentleman'd, or called-to-play)."""
        status = self.meta[order].status
        return (
            self.state.deck[order].clued
            or status == CardStatus.CALLED_TO_PLAY
            or status == CardStatus.GENTLEMANS_DISCARD
            or status == CardStatus.FINESSED
        )

    def is_blind_playing(self, order: int) -> bool:
        """The card is playing without being clued (excludes gentleman's discard)."""
        if self.state.deck[order].clued:
            return False
        meta = self.meta[order]
        return (
            meta.status == CardStatus.CALLED_TO_PLAY
            or meta.status == CardStatus.FINESSED
            or meta.bluffed
        )

    def is_saved(self, order: int) -> bool:
        """A card is touched OR chop-moved (i.e. won't be discarded)."""
        return self.is_touched(order) or self.meta[order].cm

    def order_matches(self, order: int, id_: Identity, infer: bool = False) -> bool:
        """Try every angle to see whether the order matches the identity."""
        card_id = self.state.deck[order].id()
        if card_id is not None:
            return card_id == id_
        deck_id = self.deck_ids[order] if order < len(self.deck_ids) else None
        if deck_id is not None:
            return deck_id == id_
        # Fall back to our own perspective's inferences.
        me = self.players[self.state.our_player_index]
        thought_id = me.thoughts[order].id(infer=infer)
        return thought_id == id_

    def known_as(self, order: int, regex, special_rank: int | None = None) -> bool:  # type: ignore[no-untyped-def]
        """True if every possible id of `order` is a suit matching the regex OR a special-rank match."""
        possible = self.common.thoughts[order].possible
        v = self.state.variant
        return possible.forall(
            lambda i: bool(regex.search(v.suits[i.suit_index].name)) or i.rank == special_rank
        )

    @property
    def me(self) -> Player:
        """Our perspective. Note: this is what the bot sees, including its own hidden cards."""
        return self.players[self.state.our_player_index]

    @property
    def last_move(self) -> Interp | None:
        return self.move_history[-1] if self.move_history else None

    @property
    def in_endgame(self) -> bool:
        return self.state.pace < self.state.num_players

    # --- Empathy elimination (Stage 2b real impl) ---

    def elim(self, except_: int | None = None) -> Game:
        """Full empathy pass: card_elim + good_touch_elim + refresh_links + hypo_stacks.

        Port of basics.scala `elim` (lines 189-264). Runs the elim chain on `common` first,
        then syncs every per-player perspective from common and re-runs the chain there.
        """
        # Local imports to avoid circular module load.
        from .player_elim import (
            card_elim,
            good_touch_elim,
            refresh_links,
            refresh_play_links,
        )

        state = self.state
        result: Game = self

        # Step 1: pre-elim cleanup — reset thoughts with empty inferred; clear emptied info_lock.
        for hand in state.hands:
            for order in hand:
                thought = result.common.thoughts[order]
                if thought.inferred.is_empty and not thought.reset:
                    result = result.with_thought(order, lambda t: t.reset_inferences())
                    result = result.with_meta(
                        order,
                        lambda m: dataclasses.replace(m, status=CardStatus.NONE, by=None),
                    )
                updated = result.common.thoughts[order]
                if updated.info_lock is not None and updated.info_lock.is_empty:
                    result = result.with_thought(
                        order, lambda t: dataclasses.replace(t, info_lock=None)
                    )

        # Step 2: card_elim + (optional) good_touch_elim on common.
        resets, new_common = card_elim(result.common, state)
        if result.good_touch:
            gt_resets, new_common = good_touch_elim(new_common, result, except_)
            resets = resets | gt_resets
        result = result.copy_with(common=new_common)
        # Clear CalledToPlay status on any orders that got reset.
        for order in resets:
            if result.meta[order].status == CardStatus.CALLED_TO_PLAY:
                result = result.with_meta(
                    order,
                    lambda m: dataclasses.replace(m, status=CardStatus.NONE, by=None),
                )

        # Step 3: refresh_links + refresh_play_links + update_hypo_stacks on common.
        sarcastics, new_common = refresh_links(result.common, result)
        new_common = refresh_play_links(new_common, result)
        new_common = new_common.update_hypo_stacks(result)
        result = result.copy_with(common=new_common)
        for order in sarcastics:
            result = result.with_meta(
                order, lambda m: dataclasses.replace(m, status=CardStatus.SARCASTIC)
            )

        # Step 4: sync each per-player perspective from common, run elim chain per-player.
        common = result.common
        new_players_list = []
        for p in result.players:
            synced_thoughts = list(p.thoughts)
            for o in common.dirty:
                t = p.thoughts[o]
                c_t = common.thoughts[o]
                new_inferred = c_t.inferred.intersect(t.possible).when_empty(t.possible)
                if c_t.info_lock is None:
                    new_info_lock: IdentitySet | None = None
                else:
                    ids = c_t.info_lock.intersect(t.possible)
                    new_info_lock = None if ids.is_empty else ids
                synced_thoughts[o] = dataclasses.replace(
                    t,
                    possible=c_t.possible,
                    inferred=new_inferred,
                    info_lock=new_info_lock,
                    reset=c_t.reset,
                )
            np = dataclasses.replace(
                p,
                thoughts=tuple(synced_thoughts),
                links=common.links,
                play_links=common.play_links,
                dirty=common.dirty,
            )
            _, np = card_elim(np, state)
            if result.good_touch:
                _, np = good_touch_elim(np, result, except_)
            _, np = refresh_links(np, result)
            np = refresh_play_links(np, result)
            np = np.update_hypo_stacks(result)
            np = dataclasses.replace(np, dirty=frozenset())
            new_players_list.append(np)

        return result.copy_with(
            common=dataclasses.replace(result.common, dirty=frozenset()),
            players=tuple(new_players_list),
        )

    # --- Action handlers (port of basics.scala) ---

    def on_clue(self, action: ClueAction) -> Game:
        """Apply a clue: mark touched cards as clued, intersect/difference possibilities.

        Port of basics.scala `onClue` (lines 7-44). Does NOT apply conventional interpretation.
        """
        v = self.state.variant
        kind_value = action.clue.kind.value
        # Identities that this clue touches.
        new_possible_ids = [i for i in v.all_ids() if v.id_touched(i, kind_value, action.clue.value)]
        new_possible = IdentitySet.from_iter(new_possible_ids)
        touched_orders = set(action.list_)

        result: Game = self
        for order in self.state.hands[action.target]:
            common_thought_before = result.common.thoughts[order]
            if order in touched_orders:
                # Mark card clued + add CardClue
                def _mark_clued(c: Card, _giver=action.giver, _kind=action.clue.kind,
                                _value=action.clue.value, _turn=self.state.turn_count) -> Card:
                    return dataclasses.replace(
                        c,
                        clued=True,
                        clues=(*c.clues, CardClue(_kind, _value, _giver, _turn)),
                    )
                result = result.with_card(order, _mark_clued)

                # Intersect thought possibilities with new_possible
                def _intersect(t: Thought, _np=new_possible) -> Thought:
                    new_lock = t.info_lock.intersect(_np) if t.info_lock is not None else None
                    if new_lock is not None and new_lock.is_empty:
                        new_lock = None
                    return dataclasses.replace(
                        t,
                        inferred=t.inferred.intersect(_np),
                        possible=t.possible.intersect(_np),
                        info_lock=new_lock,
                    )
                result = result.with_thought(order, _intersect)

                new_thought = result.common.thoughts[order]
                # If now fully known, write identity to deck + deck_ids.
                if new_thought.possible.length == 1:
                    id_ = new_thought.possible.head
                    result = result.with_id(order, id_)
                # If inference shrunk, append reason on this turn.
                if new_thought.inferred.length < common_thought_before.inferred.length:
                    result = result.with_meta(order, lambda m, _t=self.state.turn_count: m.reason(_t))
            else:
                # Card not touched -> difference out new_possible from this card's beliefs.
                def _difference(t: Thought, _np=new_possible) -> Thought:
                    new_lock = t.info_lock.difference(_np) if t.info_lock is not None else None
                    if new_lock is not None and new_lock.is_empty:
                        new_lock = None
                    return dataclasses.replace(
                        t,
                        inferred=t.inferred.difference(_np),
                        possible=t.possible.difference(_np),
                        info_lock=new_lock,
                    )
                result = result.with_thought(order, _difference)

        # Decrement clue tokens and endgame timer.
        def _tick(s: State) -> State:
            return dataclasses.replace(
                s,
                clue_tokens=s.clue_tokens - 1,
                endgame_turns=(s.endgame_turns - 1) if s.endgame_turns is not None else None,
            )
        return result.with_state(_tick)

    def on_discard(self, action: DiscardAction) -> Game:
        """Apply a discard: remove from hand, append to discard pile, update state. Strike if `failed`."""
        # Remove from hand and tick endgame.
        def _drop_from_hand(s: State) -> State:
            new_hand = tuple(o for o in s.hands[action.player_index] if o != action.order)
            new_hands = (*s.hands[:action.player_index], new_hand, *s.hands[action.player_index + 1:])
            new_state = dataclasses.replace(
                s,
                hands=new_hands,
                endgame_turns=(s.endgame_turns - 1) if s.endgame_turns is not None else None,
            )
            if action.failed:
                new_state = dataclasses.replace(new_state, strikes=new_state.strikes + 1)
            else:
                new_state = new_state.regain_clue()
            return new_state

        result: Game = self.with_state(_drop_from_hand)

        # If identity is known, update discard pile / play stacks + thought.
        if action.suit_index != -1 and action.rank != -1:
            id_ = Identity(action.suit_index, action.rank)
            result = result.with_state(lambda s, _i=id_, _o=action.order: s.with_discard(_i, _o))
            result = result.with_id(action.order, id_)

            def _resolve_thought(t: Thought, _id=id_) -> Thought:
                return dataclasses.replace(
                    t,
                    suit_index=_id.suit_index,
                    rank=_id.rank,
                    inferred=IdentitySet.single(_id),
                    old_inferred=t.inferred,
                    possible=IdentitySet.single(_id),
                    old_possible=t.possible,
                )
            result = result.with_thought(action.order, _resolve_thought)

        return result

    def on_play(self, action: PlayAction) -> Game:
        """Apply a successful play: remove from hand, advance play stack, update thought."""
        def _drop_from_hand(s: State) -> State:
            new_hand = tuple(o for o in s.hands[action.player_index] if o != action.order)
            new_hands = (*s.hands[:action.player_index], new_hand, *s.hands[action.player_index + 1:])
            return dataclasses.replace(
                s,
                hands=new_hands,
                endgame_turns=(s.endgame_turns - 1) if s.endgame_turns is not None else None,
            )

        result: Game = self.with_state(_drop_from_hand)

        # Deck-plays edge case (the last card of the deck was played, not drawn): create a Card retroactively.
        deck_plays_edge = (
            self.state.options.deck_plays
            and action.order == self.state.cards_total - 1
            and len(self.state.deck) == action.order
        )
        if deck_plays_edge:
            def _deck_play(s: State, _a=action) -> State:
                new_card = Card(
                    suit_index=_a.suit_index, rank=_a.rank, order=_a.order, turn_drawn=s.turn_count,
                )
                return dataclasses.replace(
                    s,
                    deck=(*s.deck, new_card),
                    holders=(*s.holders, _a.player_index),
                    next_card_order=s.next_card_order + 1,
                    cards_left=s.cards_left - 1,
                    endgame_turns=s.num_players,
                )
            result = result.with_state(_deck_play)

            # Add a thought for the new card to each player and common.
            order = action.order
            new_players = tuple(
                dataclasses.replace(
                    p,
                    thoughts=(*p.thoughts, Thought.initial(
                        suit_index=(action.suit_index if i != action.player_index else -1),
                        rank=(action.rank if i != action.player_index else -1),
                        order=order,
                        possible=p.all_possible,
                    )),
                    dirty=p.dirty | {order},
                )
                for i, p in enumerate(result.players)
            )
            new_common = dataclasses.replace(
                result.common,
                thoughts=(*result.common.thoughts, Thought.initial(-1, -1, order, result.common.all_possible)),
                dirty=result.common.dirty | {order},
            )
            result = result.copy_with(
                players=new_players,
                common=new_common,
                meta=(*result.meta, ConvData(order)),
            )

        if action.suit_index != -1 and action.rank != -1:
            id_ = Identity(action.suit_index, action.rank)
            result = result.with_state(lambda s, _i=id_: s.with_play(_i))
            result = result.with_id(action.order, id_)

            def _resolve_thought(t: Thought, _id=id_) -> Thought:
                return dataclasses.replace(
                    t,
                    suit_index=_id.suit_index,
                    rank=_id.rank,
                    inferred=IdentitySet.single(_id),
                    old_inferred=t.inferred,
                    possible=IdentitySet.single(_id),
                    old_possible=t.possible,
                )
            result = result.with_thought(action.order, _resolve_thought)

        return result

    def on_draw(self, action: DrawAction) -> Game:
        """Apply a draw: prepend to hand, append to deck, create Thoughts for every observer.

        The drawn card's identity is recorded in state.deck only if known (i.e. not -1/-1).
        Each Player records the identity they would observe (own = unknown; others = visible).
        """
        order = action.order
        state = self.state

        if (
            len(state.hands[action.player_index]) == HAND_SIZE[state.num_players]
            and not (state.options.deck_plays and order == state.cards_total - 1)
        ):
            raise RuntimeError(f"{state.names[action.player_index]} already has a full hand!")

        id_ = Identity(action.suit_index, action.rank) if action.suit_index != -1 and action.rank != -1 else None

        # Cross-check against any pre-existing deck_ids entry for this order.
        if (
            id_ is not None
            and order < len(self.deck_ids)
            and self.deck_ids[order] is not None
            and self.deck_ids[order] != id_
        ):
            raise RuntimeError(
                f"drew {state.log_id(id_)}, expected {state.log_id(self.deck_ids[order])} at order {order}"
            )

        # Update deck_ids (extend or fill in).
        if len(self.deck_ids) == order:
            new_deck_ids: tuple[Identity | None, ...] = (*self.deck_ids, id_)
        elif len(self.deck_ids) > order:
            if self.deck_ids[order] is None and id_ is not None:
                new_deck_ids = (*self.deck_ids[:order], id_, *self.deck_ids[order + 1:])
            else:
                new_deck_ids = self.deck_ids
        else:
            raise RuntimeError(
                f"Only have {len(self.deck_ids)} deck ids, but drew card with order {order}"
            )

        result: Game = self.copy_with(deck_ids=new_deck_ids)

        # Sanity check (mirrors Scala asserts at basics.scala lines 102-106).
        if not state.options.deck_plays:
            assert len(state.deck) == order, f"Deck length {len(state.deck)} doesn't match drawn order {order}"
            assert len(state.deck) == state.next_card_order

        # Prepend to the drawing player's hand, append to deck + holders, decrement cards_left.
        def _update_state(s: State, _a=action) -> State:
            new_hand = (_a.order, *s.hands[_a.player_index])
            new_hands = (*s.hands[:_a.player_index], new_hand, *s.hands[_a.player_index + 1:])
            new_card = Card(suit_index=_a.suit_index, rank=_a.rank, order=_a.order, turn_drawn=s.turn_count)
            new_state = dataclasses.replace(
                s,
                hands=new_hands,
                deck=(*s.deck, new_card),
                holders=(*s.holders, _a.player_index),
                next_card_order=_a.order + 1,
                cards_left=s.cards_left - 1,
            )
            if new_state.cards_left == 0 and new_state.endgame_turns is None:
                new_state = dataclasses.replace(new_state, endgame_turns=new_state.num_players)
            return new_state

        result = result.with_state(_update_state)

        # If a new card index, create matching Thoughts and ConvData.
        if order == len(self.meta):
            new_players = tuple(
                dataclasses.replace(
                    p,
                    thoughts=(*p.thoughts, Thought.initial(
                        suit_index=(action.suit_index if i != action.player_index else -1),
                        rank=(action.rank if i != action.player_index else -1),
                        order=order,
                        possible=p.all_possible,
                    )),
                    dirty=p.dirty | {order},
                )
                for i, p in enumerate(result.players)
            )
            new_common = dataclasses.replace(
                result.common,
                thoughts=(*result.common.thoughts, Thought.initial(-1, -1, order, result.common.all_possible)),
                dirty=result.common.dirty | {order},
            )
            result = result.copy_with(
                players=new_players,
                common=new_common,
                meta=(*result.meta, ConvData(order)),
            )

        return result

    # --- Top-level dispatcher ---

    def handle_action(self, action: Action) -> Game:
        """Apply an Action: record in action_list, dispatch to on_*, then convention interpret_*.

        Port of basics.scala `handleAction` (lines 201-251).
        """
        state = self.state
        if len(state.action_list) < state.turn_count:
            raise RuntimeError(
                f"Turn count {state.turn_count}, action_list length {len(state.action_list)}"
            )

        new_action_list = _add_action(state.action_list, action, state.turn_count)
        new_game: Game = self.with_state(lambda s, _al=new_action_list: dataclasses.replace(s, action_list=_al))

        prev = self

        match action:
            case ClueAction():
                after_clue = new_game.on_clue(action)
                after_interp = after_clue.interpret_clue(prev, action)
                new_last = (*after_interp.last_actions[:action.giver], action,
                            *after_interp.last_actions[action.giver + 1:])
                return after_interp.copy_with(last_actions=new_last)

            case DiscardAction():
                after_discard = new_game.on_discard(action)
                after_interp = after_discard.interpret_discard(prev, action)
                new_last = (*after_interp.last_actions[:action.player_index], action,
                            *after_interp.last_actions[action.player_index + 1:])
                return after_interp.copy_with(last_actions=new_last)

            case PlayAction():
                after_play = new_game.on_play(action)
                after_interp = after_play.interpret_play(prev, action)
                new_last = (*after_interp.last_actions[:action.player_index], action,
                            *after_interp.last_actions[action.player_index + 1:])
                return after_interp.copy_with(last_actions=new_last)

            case DrawAction():
                after_draw = new_game.on_draw(action)
                # On the very first deal (turn 0), once every hand is full, advance to turn 1.
                hand_size = HAND_SIZE[state.num_players]
                if after_draw.state.turn_count == 0 and all(
                    len(h) == hand_size for h in after_draw.state.hands
                ):
                    after_draw = after_draw.with_state(lambda s: dataclasses.replace(s, turn_count=1))
                return after_draw.elim()

            case GameOverAction():
                return new_game.copy_with(in_progress=False)

            case TurnAction(num=num, current_player_index=cpi):
                advanced = new_game.with_state(
                    lambda s, _cpi=cpi, _n=num: dataclasses.replace(s, current_player_index=_cpi, turn_count=_n + 1)
                )
                if cpi != -1:
                    advanced = advanced.update_turn(action)
                return advanced

            case InterpAction(interp=interp):
                return new_game.copy_with(next_interp=interp)

            case StatusAction() | StrikeAction():
                # No state changes beyond action_list recording (which already happened).
                return new_game

            case _:
                return self
