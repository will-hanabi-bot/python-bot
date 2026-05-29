"""Self-play: simulate N games with the bot playing every perspective.

Port of scala-bot/src/scala_bot/selfPlay.scala.

Run:
    python -m hanabi_bot self-play games=10 seed=0 variant="No Variant" players=3
"""

from __future__ import annotations

import enum
import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from hanabi_bot.basics.action import (
    Action,
    ClueAction,
    DiscardAction,
    DrawAction,
    GameOverAction,
    PerformAction,
    PerformColour,
    PerformDiscard,
    PerformPlay,
    PerformRank,
    PerformTerminate,
    PlayAction,
    TurnAction,
)
from hanabi_bot.basics.clue import BaseClue, ClueKind
from hanabi_bot.basics.identity import Identity
from hanabi_bot.basics.options import TableOptions
from hanabi_bot.basics.state import HAND_SIZE, State
from hanabi_bot.basics.variant import Variant, get_variant
from hanabi_bot.conventions.reactor import Reactor

NAMES: tuple[str, ...] = ("Alice", "Bob", "Cathy", "Donald", "Emily")


class GameResult(enum.Enum):
    PERFECT = "Perfect"
    STRIKEOUT = "Strikeout"
    DISCARDED_CRIT = "DiscardedCrit"
    OUT_OF_PACE = "OutOfPace"


@dataclass(frozen=True, slots=True)
class GameSummary:
    score: int
    result: GameResult
    actions: tuple[PerformAction, ...]
    notes: tuple[tuple[str, ...], ...]  # per-perspective per-order


def _build_deck(variant: Variant) -> list[Identity]:
    """All identities with their card_count multiplicity."""
    deck: list[Identity] = []
    for id_ in variant.all_ids():
        deck.extend([id_] * variant.card_count(id_))
    return deck


def _perform_to_action(
    perform: PerformAction,
    state: State,
    current_player_index: int,
    deck: list[Identity],
) -> Action:
    """Translate a PerformAction to the corresponding game Action using the known deck."""
    if isinstance(perform, PerformPlay):
        order = perform.target
        if order >= len(deck):
            return DiscardAction(current_player_index, order, -1, -1, True)
        id_ = deck[order]
        if state.is_playable(id_):
            return PlayAction(current_player_index, order, id_.suit_index, id_.rank)
        return DiscardAction(current_player_index, order, id_.suit_index, id_.rank, True)
    if isinstance(perform, PerformDiscard):
        order = perform.target
        if order >= len(deck):
            return DiscardAction(current_player_index, order, -1, -1, False)
        id_ = deck[order]
        return DiscardAction(current_player_index, order, id_.suit_index, id_.rank, False)
    if isinstance(perform, PerformColour):
        clue = BaseClue(ClueKind.COLOUR, perform.value)
        touched = tuple(
            state.clue_touched(state.hands[perform.target], 0, perform.value)
        )
        return ClueAction(current_player_index, perform.target, touched, clue)
    if isinstance(perform, PerformRank):
        clue = BaseClue(ClueKind.RANK, perform.value)
        touched = tuple(
            state.clue_touched(state.hands[perform.target], 1, perform.value)
        )
        return ClueAction(current_player_index, perform.target, touched, clue)
    if isinstance(perform, PerformTerminate):
        return GameOverAction(perform.value, perform.target)
    raise ValueError(f"unknown PerformAction: {perform!r}")


def simulate_game(games: list[Reactor], deck: list[Identity]) -> GameSummary:
    """Run one self-played game. `games[i]` is the perspective from player i."""
    # Deal: each game sees other players' cards but not its own.
    dealt_games: list[Reactor] = []
    for i, g in enumerate(games):
        gg = g.copy_with(catchup=True)
        for player_index in range(gg.state.num_players):
            for _ in range(HAND_SIZE[gg.state.num_players]):
                order = gg.state.next_card_order
                suit = -1 if player_index == i else deck[order].suit_index
                rank = -1 if player_index == i else deck[order].rank
                gg = gg.handle_action(
                    DrawAction(player_index, order, suit, rank)
                )
        dealt_games.append(gg)

    state_games = dealt_games
    actions: list[PerformAction] = []

    while not state_games[0].state.ended:
        cpi = state_games[0].state.current_player_index
        try:
            perform = state_games[cpi].take_action()
        except Exception as e:
            print(f"!! take_action raised in self-play: {e}")
            # Force game over.
            forced_over = state_games[0].with_state(
                lambda s: s.__class__(  # type: ignore[call-arg]
                    **{**vars(s), "endgame_turns": 0}
                )
            )
            state_games = [forced_over for _ in state_games]
            break

        new_games: list[Reactor] = []
        for game in state_games:
            state = game.state
            action = _perform_to_action(perform, state, cpi, deck)
            new_game = game.handle_action(action)
            if (
                not new_game.state.ended
                and new_game.state.next_card_order < len(deck)
                and isinstance(perform, (PerformPlay, PerformDiscard))
            ):
                order = new_game.state.next_card_order
                own = cpi == new_game.state.our_player_index
                suit = -1 if own else deck[order].suit_index
                rank = -1 if own else deck[order].rank
                new_game = new_game.handle_action(
                    DrawAction(cpi, order, suit, rank)
                )
            new_game = new_game.handle_action(
                TurnAction(new_game.state.turn_count, new_game.state.next_player_index(cpi))
            )
            new_games.append(new_game)
        state_games = new_games
        actions.append(perform)

    # Append a Terminate action if the last wasn't already one.
    final_game = state_games[0]
    final_state = final_game.state
    target = final_state.last_player_index(final_state.current_player_index)
    if not actions or not isinstance(actions[-1], PerformTerminate):
        actions.append(PerformTerminate(target=target, value=0))

    score = final_state.score
    num_suits = len(final_state.variant.suits)
    if final_state.strikes == 3:
        result = GameResult.STRIKEOUT
    elif score == num_suits * 5:
        result = GameResult.PERFECT
    elif final_state.max_score < num_suits * 5:
        result = GameResult.DISCARDED_CRIT
    else:
        result = GameResult.OUT_OF_PACE

    return GameSummary(
        score=score,
        result=result,
        actions=tuple(actions),
        notes=tuple(() for _ in state_games),  # notes aren't currently tracked; placeholder
    )


def run_self_play(
    num_games: int = 1,
    seed: int = 0,
    variant_name: str = "No Variant",
    num_players: int = 3,
    seeds_dir: Path | None = None,
) -> list[tuple[GameResult, int]]:
    """Run num_games self-plays starting at the given seed. Returns (result, score) per game."""
    variant = get_variant(variant_name)
    base_deck = _build_deck(variant)
    seeds_dir = seeds_dir or Path("seeds")
    seeds_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[GameResult, int]] = []
    for i in range(seed, seed + num_games):
        rng = random.Random(i)
        shuffled = base_deck.copy()
        rng.shuffle(shuffled)

        names = NAMES[:num_players]
        opts = TableOptions(num_players=num_players, variant_name=variant.name)
        states = [
            State.create(names=names, our_player_index=p, variant=variant, options=opts)
            for p in range(num_players)
        ]
        games = [Reactor.create(0, st, in_progress=True) for st in states]

        summary = simulate_game(games, shuffled)

        # Write the seed file (mirrors Scala's seeds/{i}.json).
        out_data = {
            "players": list(names),
            "deck": [{"suitIndex": id_.suit_index, "rank": id_.rank} for id_ in shuffled],
            "actions": [a.to_json(0) for a in summary.actions],
            "notes": [list(n) for n in summary.notes],
            "options": {"variant": variant.name},
        }
        (seeds_dir / f"{i}.json").write_text(json.dumps(out_data), encoding="utf-8")

        print(f"Seed {i}: score {summary.score}, {summary.result.value}")
        results.append((summary.result, summary.score))

    if num_games > 1:
        perfect = sum(1 for r, _ in results if r == GameResult.PERFECT)
        avg = sum(s for _, s in results) / num_games
        dist = Counter(r for r, _ in results)
        print("----------------")
        print(f"Perfect scores: {perfect}/{num_games}, {100.0 * perfect / num_games:.2f}%")
        print(f"Average score: {avg:.2f}")
        print(f"Result distribution: {dict(dist)}")

    return results


def main(args: dict[str, str]) -> int:
    """CLI entry — args is the parsed `key=value` dict from __main__."""
    num_games = int(args.get("games", "1"))
    seed = int(args.get("seed", "0"))
    variant_name = args.get("variant", "No Variant")
    num_players = int(args.get("players", "3"))
    seeds_dir = Path(args.get("seeds_dir", "seeds"))

    run_self_play(
        num_games=num_games,
        seed=seed,
        variant_name=variant_name,
        num_players=num_players,
        seeds_dir=seeds_dir,
    )
    return 0
