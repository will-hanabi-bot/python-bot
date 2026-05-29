"""Test harness for the Hanabi bot.

Port of scala-bot/src/test/util.scala + exAsserts.scala.

Exposes:
- `Player` enum (ALICE=0, BOB=1, ...)
- `setup(...)` — build a Game with prepared hands/stacks/discarded/etc.
- `take_turn(game, raw_action, draw=...)` — apply a natural-language action
- `parse_action(state, raw)` — parse natural-language action strings
- `pre_clue(...)`, `fully_known(...)` — pre-set conventional state on cards
- `has_infs`, `has_poss`, `has_status` — assertion helpers

Card identities are passed as short strings — e.g. `"r1"` for red 1, `"g5"` for green 5
in a variant whose short_forms include 'r' and 'g'. Use `"xx"` for unknown.

Action strings follow the Scala convention:
- `"Alice clues red to Bob"` / `"Alice clues 3 to Bob (slots 1,2)"` / `"Alice clues red to Alice (slot 2)"`
- `"Bob plays r1"` / `"Bob plays r1 (slot 3)"` (slot required for our hand or ambiguous identity)
- `"Bob discards b3"` / `"Bob bombs r2 (slot 1)"` (bombs = failed discard)
"""

from __future__ import annotations

import dataclasses
import enum
import re
from collections.abc import Callable, Iterable, Sequence

from hanabi_bot.basics.action import (
    ClueAction,
    DiscardAction,
    DrawAction,
    PlayAction,
    TurnAction,
)
from hanabi_bot.basics.card import CardStatus
from hanabi_bot.basics.clue import BaseClue, CardClue, ClueKind
from hanabi_bot.basics.game import Game
from hanabi_bot.basics.identity import Identity, IdentitySet
from hanabi_bot.basics.options import TableOptions
from hanabi_bot.basics.player import MatchEntry
from hanabi_bot.basics.player import Player as _BasicsPlayer
from hanabi_bot.basics.state import HAND_SIZE, State
from hanabi_bot.basics.variant import Variant, get_variant

# Sentinel order used when seeding pre-discarded cards into certain_map.
# Matches Scala util.scala's `MatchEntry(61, -1)` magic value.
_SEEDED_ORDER = 61


class TestPlayer(enum.IntEnum):
    """Player names used in test setups. Ordinal matches the player index."""

    ALICE = 0
    BOB = 1
    CATHY = 2
    DONALD = 3
    EMILY = 4


# Public alias matching the Scala name.
Player = TestPlayer

NAMES: tuple[str, ...] = ("Alice", "Bob", "Cathy", "Donald", "Emily")


# --- Setup ---


def setup(
    constructor: Callable[[int, State], Game] = Game.create,
    hands: Sequence[Sequence[str]] = (),
    *,
    play_stacks: Sequence[int] | None = None,
    discarded: Sequence[str] = (),
    strikes: int = 0,
    clue_tokens: int = 8,
    starting: TestPlayer = TestPlayer.ALICE,
    variant: Variant | str = "No Variant",
    init: Callable[[Game], Game] = lambda g: g,
) -> Game:
    """Build a Game in a specific position.

    :param constructor: callable producing a Game subclass from (table_id, state).
                        Default uses base Game (no convention).
    :param hands: per-player hand cards as short strings ("xx" = hidden).
    :param play_stacks: optional initial play stack heights (one int per suit).
    :param discarded: short strings of cards already in the discard pile.
    :param strikes: starting strike count.
    :param clue_tokens: starting clue-token count.
    :param starting: which player has the first turn.
    :param variant: Variant instance or variant name (looked up in vendored catalog).
    :param init: extra initialization closure applied before the final elim() pass.

    Port of `setup` in scala-bot/src/test/util.scala (lines 33-128).
    """
    v = get_variant(variant) if isinstance(variant, str) else variant
    num_players = len(hands)
    if num_players == 0:
        raise ValueError("hands must be non-empty")
    if any(len(h) > HAND_SIZE[num_players] for h in hands):
        raise ValueError(
            f"Hand size should be {HAND_SIZE[num_players]} for a {num_players}-player game"
        )

    names = NAMES[:num_players]
    state = State.create(
        names=names,
        our_player_index=0,
        variant=v,
        options=TableOptions(num_players=num_players, variant_name=v.name),
    )
    game = constructor(0, state)
    game = game.copy_with(catchup=True)

    # 1. Pre-seed play stacks (and base_count + certain_map for the implied plays).
    if play_stacks is not None:
        if len(play_stacks) != len(v.suits):
            raise ValueError(
                f"play_stacks length {len(play_stacks)} != {len(v.suits)} suits"
            )
        seeded_ids: list[Identity] = []
        for suit_index, stack in enumerate(play_stacks):
            for rank in range(1, stack + 1):
                seeded_ids.append(Identity(suit_index, rank))

        new_base = list(game.state.base_count)
        for id_ in seeded_ids:
            new_base[id_.to_ord()] += 1

        new_state = dataclasses.replace(
            game.state,
            play_stacks=tuple(play_stacks),
            base_count=tuple(new_base),
        )

        def _seed_certain_map(cm: tuple[tuple[MatchEntry, ...], ...]) -> tuple[tuple[MatchEntry, ...], ...]:
            cm_list = list(cm)
            for id_ in seeded_ids:
                cm_list[id_.to_ord()] = (MatchEntry(_SEEDED_ORDER, -1), *cm_list[id_.to_ord()])
            return tuple(cm_list)

        game = game.copy_with(
            state=new_state,
            common=dataclasses.replace(
                game.common,
                hypo_stacks=tuple(play_stacks),
                certain_map=_seed_certain_map(game.common.certain_map),
            ),
            players=tuple(
                dataclasses.replace(
                    p,
                    hypo_stacks=tuple(play_stacks),
                    certain_map=_seed_certain_map(p.certain_map),
                )
                for p in game.players
            ),
        )

    # 2. Deal the hands.
    draw_actions: list[DrawAction] = []
    order_counter = -1
    for player_index, hand in enumerate(hands):
        # Scala reverses each hand (slot 1 = leftmost = newest = highest order).
        # In the deal loop we issue draws oldest-first so the final hand prepends to [newest, ..., oldest].
        for short in reversed(hand):
            order_counter += 1
            if short == "xx":
                draw_actions.append(DrawAction(player_index, order_counter, -1, -1))
            else:
                id_ = game.state.expand_short(short)
                draw_actions.append(DrawAction(player_index, order_counter, id_.suit_index, id_.rank))

    for action in draw_actions:
        game = game.handle_action(action)

    # 3. Apply pre-existing discards (using sentinel order 99 so they don't conflict with deck orders).
    for short in discarded:
        id_ = game.state.expand_short(short)

        def _seed_discard_certain_map(
            cm: tuple[tuple[MatchEntry, ...], ...], _id=id_
        ) -> tuple[tuple[MatchEntry, ...], ...]:
            cm_list = list(cm)
            cm_list[_id.to_ord()] = (MatchEntry(_SEEDED_ORDER, -1), *cm_list[_id.to_ord()])
            return tuple(cm_list)

        game = game.with_state(lambda s, _i=id_: s.with_discard(_i, 99))
        game = game.copy_with(
            common=dataclasses.replace(
                game.common, certain_map=_seed_discard_certain_map(game.common.certain_map)
            ),
            players=tuple(
                dataclasses.replace(
                    p, certain_map=_seed_discard_certain_map(p.certain_map)
                )
                for p in game.players
            ),
        )

    # 4. Sanity check: not more copies of any identity than the deck allows.
    me_thoughts = game.players[game.state.our_player_index].thoughts
    for id_ in game.state.variant.all_ids():
        visible = sum(
            1
            for hand in game.state.hands
            for o in hand
            if me_thoughts[o].matches(id_, infer=True)
        )
        count = game.state.base_count[id_.to_ord()] + visible
        if count > game.state.card_count[id_.to_ord()]:
            raise ValueError(f"Found {count} copies of {game.state.log_id(id_)}!")

    # 5. Final state tweaks: fixup card counts, current player, recompute id sets.
    def _finalize_state(s: State) -> State:
        all_ids = s.all_ids
        playable_set = all_ids.filter(lambda i: s.is_playable(i) and not s.is_basic_trash(i))
        critical_set = all_ids.filter(s.is_critical)
        trash_set = all_ids.filter(s.is_basic_trash)
        return dataclasses.replace(
            s,
            cards_left=s.cards_left - s.score - len(discarded),
            current_player_index=starting.value,
            clue_tokens=clue_tokens,
            strikes=strikes,
            playable_set=playable_set,
            critical_set=critical_set,
            trash_set=trash_set,
        )

    game = game.with_state(_finalize_state)
    game = init(game)
    game = game.elim()
    # Snapshot base state for rewinds.
    game = game.copy_with(base=(game.state, game.meta, game.players, game.common))
    game = game.copy_with(catchup=False)
    return game


# --- Action parsing ---


def str_to_clue(state: State, s: str) -> BaseClue:
    """Parse a clue string. Rank: "1".."5". Colour: case-insensitive suit name."""
    if s in "12345":
        return BaseClue(ClueKind.RANK, int(s))
    lowered = s.lower()
    for i, suit in enumerate(state.variant.suits):
        if suit.name.lower() == lowered:
            return BaseClue(ClueKind.COLOUR, i)
    raise ValueError(
        f"Colour {s!r} not found in [{', '.join(suit.name for suit in state.variant.suits)}]"
    )


_CLUE_RE = re.compile(r"^(\w+) clues (\d|\w+) to (\w+)(?: \(slots? ((?:\d)(?:,\d)*)\))?$")
_PLAY_RE = re.compile(r"^(\w+) plays (\w\d)(?: \(slot (\d)\))?$")
_DISCARD_RE = re.compile(r"^(\w+) (discards|bombs) (\w\d)(?: \(slot (\d)\))?$")


def _parse_player(state: State, name: str) -> int:
    try:
        return state.names.index(name)
    except ValueError as e:
        raise ValueError(
            f"Player {name!r} not found in [{', '.join(state.names)}]"
        ) from e


def parse_action(state: State, raw: str) -> ClueAction | PlayAction | DiscardAction:
    """Parse a natural-language action like 'Alice clues red to Bob (slot 1)'."""
    m = _CLUE_RE.match(raw)
    if m:
        giver_s, value_s, target_s, slots_s = m.groups()
        if not state.can_clue:
            raise ValueError("Tried to clue with 0 clue tokens")
        if giver_s == target_s:
            raise ValueError(f"{giver_s} cannot clue themselves")
        giver = _parse_player(state, giver_s)
        target = _parse_player(state, target_s)
        clue = str_to_clue(state, value_s)
        if target != state.our_player_index:
            list_ = state.clue_touched(state.hands[target], clue.kind.value, clue.value)
            if not list_:
                raise ValueError(f"No cards touched by clue ({value_s} to {target_s})")
            return ClueAction(giver, target, tuple(list_), clue)
        if slots_s is None:
            raise ValueError(
                f"Not enough arguments (clue to us) in {raw!r}, needs '(slot x)'"
            )
        slot_list = [int(s) - 1 for s in slots_s.split(",")]
        list_ = [state.our_hand[i] for i in slot_list]
        return ClueAction(giver, target, tuple(list_), clue)

    m = _PLAY_RE.match(raw)
    if m:
        player_s, short, slot_s = m.groups()
        return _parse_play_or_discard(
            state, raw, player_s, short, slot_s, is_play=True, failed=False
        )

    m = _DISCARD_RE.match(raw)
    if m:
        player_s, verb, short, slot_s = m.groups()
        failed = verb == "bombs"
        if state.clue_tokens == 8 and not failed:
            raise ValueError("Tried to discard with 8 clue tokens")
        return _parse_play_or_discard(
            state, raw, player_s, short, slot_s, is_play=False, failed=failed
        )

    raise ValueError(f"Invalid action: {raw!r}")


def _parse_play_or_discard(
    state: State,
    raw: str,
    player_s: str,
    short: str,
    slot_s: str | None,
    *,
    is_play: bool,
    failed: bool,
) -> PlayAction | DiscardAction:
    player_index = _parse_player(state, player_s)
    id_ = state.expand_short(short)

    def _build(order: int) -> PlayAction | DiscardAction:
        if is_play:
            return PlayAction(player_index, order, id_.suit_index, id_.rank)
        return DiscardAction(player_index, order, id_.suit_index, id_.rank, failed)

    if player_index != state.our_player_index:
        matching = [o for o in state.hands[player_index] if state.deck[o].matches(id_)]
        if not matching:
            raise ValueError(
                f"Unable to find {short} in {state.names[player_index]}'s hand"
            )
        if len(matching) == 1:
            order = matching[0]
            if slot_s is not None:
                slot = int(slot_s)
                if state.hands[player_index][slot - 1] != order:
                    raise ValueError(f"Identity {short} not in slot {slot_s}")
            return _build(order)
        # Ambiguous
        if slot_s is None:
            raise ValueError(
                f"Not enough arguments (ambiguous identity) in {raw!r}, needs '(slot x)'"
            )
        order = state.hands[player_index][int(slot_s) - 1]
        if not state.deck[order].matches(id_):
            raise ValueError(f"Identity {short} not in slot {slot_s}")
        return _build(order)

    if slot_s is None:
        verb = "play" if is_play else "discard"
        raise ValueError(
            f"Not enough arguments ({verb} from us) in {raw!r}, needs '(slot x)'"
        )
    order = state.hands[state.our_player_index][int(slot_s) - 1]
    return _build(order)


# --- take_turn ---


def take_turn(game: Game, raw_action: str, draw: str = "") -> Game:
    """Apply an action by parsing a natural-language string, then advance to the next turn.

    For plays/discards by other players, a `draw` short-string is required (the card they
    drew next). For our (player 0) plays/discards, no draw is provided (server hides it).

    Wraps the action in catchup=True so notes/commands don't get queued during tests.
    """
    state = game.state
    action = parse_action(state, raw_action)

    if action.player_index != state.current_player_index:
        raise ValueError(
            f"Expected '{state.names[state.current_player_index]}'s turn for action"
        )

    draw_id: Identity | None = state.expand_short(draw) if draw else None

    result = game.copy_with(catchup=True)
    result = result.handle_action(action)

    if isinstance(action, (PlayAction, DiscardAction)):
        if draw_id is not None and state.cards_left == 0:
            raise ValueError("Cannot draw at 0 cards left")
        if draw_id is None and action.player_index != state.our_player_index:
            raise ValueError(f"Missing draw for {state.names[action.player_index]}'s action")
        if draw_id is not None:
            me_thoughts = game.players[state.our_player_index].thoughts
            visible = sum(
                1 for hand in state.hands for o in hand
                if me_thoughts[o].matches(draw_id, infer=True)
            )
            count = state.base_count[draw_id.to_ord()] + visible
            if count + 1 > state.card_count[draw_id.to_ord()]:
                raise ValueError(f"Found {count + 1} copies of {state.log_id(draw_id)}")
            result = result.handle_action(
                DrawAction(
                    state.current_player_index,
                    state.next_card_order,
                    draw_id.suit_index,
                    draw_id.rank,
                )
            )
        else:
            result = result.handle_action(
                DrawAction(state.current_player_index, state.next_card_order, -1, -1)
            )
    elif draw_id is not None:
        raise ValueError(f"Unexpected draw for action {raw_action!r}")

    result = result.handle_action(
        TurnAction(state.turn_count, state.next_player_index(action.player_index))
    )
    return result.copy_with(catchup=False)


# --- pre_clue / fully_known ---


@dataclasses.dataclass(frozen=True, slots=True)
class _TestClue:
    kind: ClueKind
    value: int
    giver: TestPlayer

    @property
    def base(self) -> BaseClue:
        return BaseClue(self.kind, self.value)


def pre_clue(
    game: Game, player: TestPlayer, slot: int, clues: Iterable[str]
) -> Game:
    """Imagine these clues were already given to (player, slot) before recording started.

    The default giver alternates between Alice and Bob.
    """
    other = TestPlayer.BOB if player == TestPlayer.ALICE else TestPlayer.ALICE
    test_clues = [
        _TestClue(c.kind, c.value, other)
        for c in (str_to_clue(game.state, raw) for raw in clues)
    ]
    return _apply_pre_clue(game, player, slot, test_clues)


def fully_known(game: Game, player: TestPlayer, slot: int, short: str) -> Game:
    """Pre-clue the slot with BOTH colour and rank so the card is fully known.

    For prism variants, derives the prism colour from the rank.
    """
    state = game.state
    order = state.hands[player.value][slot - 1]
    card = state.deck[order]
    id_ = state.expand_short(short)
    if card.id() is not None and card.id() != id_:
        raise ValueError(
            f"{state.names[player.value]}'s card at slot {slot} is not {state.log_id(id_)} "
            f"(found {state.log_id(card.id())})"
        )

    giver = TestPlayer.BOB if player == TestPlayer.ALICE else TestPlayer.ALICE
    if state.variant.suits[id_.suit_index].suit_type.prism:
        colour_value = (id_.rank - 1) % len(state.variant.colourable_suits)
    else:
        colour_value = id_.suit_index

    clues = [
        _TestClue(ClueKind.RANK, id_.rank, giver),
        _TestClue(ClueKind.COLOUR, colour_value, giver),
    ]
    return _apply_pre_clue(game, player, slot, clues)


def _apply_pre_clue(
    game: Game, player: TestPlayer, slot: int, clues: list[_TestClue]
) -> Game:
    state = game.state
    order = state.hands[player.value][slot - 1]

    # Sanity-check that every clue touches the underlying identity (if known).
    card_id = state.deck[order].id()
    if card_id is not None:
        for c in clues:
            if not state.variant.id_touched(card_id, c.kind.value, c.value):
                raise ValueError(
                    f"Clue (kind={c.kind.name}, value={c.value}) doesn't touch order {order}"
                )

    # Intersection of all id-touched sets across the clues.
    possibilities = IdentitySet.from_iter(
        i
        for i in state.variant.all_ids()
        if all(state.variant.id_touched(i, c.kind.value, c.value) for c in clues)
    )

    new_card = dataclasses.replace(
        state.deck[order],
        clued=True,
        clues=tuple(CardClue(c.kind, c.value, c.giver.value, 0) for c in clues),
    )
    new_deck = (*state.deck[:order], new_card, *state.deck[order + 1:])
    new_state = dataclasses.replace(state, deck=new_deck)

    new_common = game.common.with_thought(
        order,
        lambda t: dataclasses.replace(t, inferred=possibilities, possible=possibilities),
    )

    return game.copy_with(state=new_state, common=new_common)


# --- Assertion helpers ---


def has_infs(
    game: Game,
    according_to: TestPlayer | None,
    target: TestPlayer,
    slot: int,
    expected: Iterable[str],
) -> None:
    """Assert that the inferences of (target, slot) from `according_to`'s view match `expected`."""
    hand = game.state.hands[target.value]
    if slot < 1 or slot > len(hand):
        raise AssertionError(f"Slot {slot} doesn't exist for {target.name}")
    order = hand[slot - 1]
    perspective: _BasicsPlayer = (
        game.players[according_to.value] if according_to is not None else game.common
    )
    actual = perspective.thoughts[order].inferred
    expected_set = IdentitySet.from_iter(game.state.expand_short(s) for s in expected)
    if actual != expected_set:
        actual_str = perspective.str_infs(game.state, order)
        expected_str = ",".join(expected)
        raise AssertionError(
            f"Differing inferences (order {order}). Expected {expected_str}, got {actual_str}"
        )


def has_poss(
    game: Game,
    according_to: TestPlayer | None,
    target: TestPlayer,
    slot: int,
    expected: Iterable[str],
) -> None:
    """Assert that the possibilities of (target, slot) from `according_to`'s view match `expected`."""
    hand = game.state.hands[target.value]
    if slot < 1 or slot > len(hand):
        raise AssertionError(f"Slot {slot} doesn't exist for {target.name}")
    order = hand[slot - 1]
    perspective: _BasicsPlayer = (
        game.players[according_to.value] if according_to is not None else game.common
    )
    actual = perspective.thoughts[order].possible
    expected_set = IdentitySet.from_iter(game.state.expand_short(s) for s in expected)
    if actual != expected_set:
        actual_str = perspective.str_poss(game.state, order)
        expected_str = ",".join(expected)
        raise AssertionError(
            f"Differing possibilities (order {order}). Expected {expected_str}, got {actual_str}"
        )


def has_status(
    game: Game, target: TestPlayer, slot: int, status: CardStatus
) -> None:
    """Assert that the conventional status of (target, slot) matches `status`."""
    hand = game.state.hands[target.value]
    if slot < 1 or slot > len(hand):
        raise AssertionError(f"Slot {slot} doesn't exist for {target.name}")
    order = hand[slot - 1]
    actual = game.meta[order].status
    if actual != status:
        raise AssertionError(
            f"Differing status (order {order}). Expected {status.name}, got {actual.name}"
        )
