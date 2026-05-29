"""Per-card note generation.

After every `handle_action`, the bot diffs the previous game state against the new
one and emits a note segment for each card whose `CALLED_TO_PLAY`/`CALLED_TO_DISCARD`
status changed, or whose `inferred` set shrank while called-to-play.

Notes are formatted as:
- `turn N: [f] <id1>,<id2>,...` when called-to-play (ids from the writer's
  perspective: `me.thoughts[order].inferred`, sorted by ordinal)
- `turn N: [kt]` when called-to-discard
- `turn N: [reset]` when status returns to NONE after a prior signal

Segments are concatenated across the card's lifetime with ` | `.

This module is purely a diff/formatter — no I/O. The dispatcher in `commands.py`
wires it to the WebSocket.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hanabi_bot.basics.card import CardStatus
from hanabi_bot.basics.identity import Identity, IdentitySet

if TYPE_CHECKING:
    from hanabi_bot.basics.game import Game
    from hanabi_bot.basics.state import State


def format_play_segment(turn: int, ids: IdentitySet, state: State) -> str:
    """Format a `turn N: [f] r2,y1,...` segment from an inferred set.

    Identities sorted by ordinal (suit_index, then rank); formatted via `state.log_id`.
    """
    sorted_ids = sorted(ids, key=Identity.to_ord)
    id_str = ",".join(state.log_id(i) for i in sorted_ids)
    return f"turn {turn}: [f] {id_str}"


def format_discard_segment(turn: int) -> str:
    return f"turn {turn}: [kt]"


def format_reset_segment(turn: int) -> str:
    return f"turn {turn}: [reset]"


def compute_note_segments(prev: Game, new: Game) -> list[tuple[int, str]]:
    """Diff `prev` vs `new` and return [(card_order, new_segment)] for each change.

    Triggers:
    1. Status transition to `CALLED_TO_PLAY`, `CALLED_TO_DISCARD`, or back to `NONE`
       (only if `prev_status` was a signalled status).
    2. While `CALLED_TO_PLAY`, the writer's `me.thoughts[order].inferred` strictly shrinks.

    Empty list if nothing changed.
    """
    state = new.state
    me_idx = state.our_player_index
    me_new = new.players[me_idx]
    me_prev = prev.players[me_idx]
    out: list[tuple[int, str]] = []
    prev_meta_len = len(prev.meta)
    prev_thought_len = len(me_prev.thoughts)

    for order in range(len(new.meta)):
        new_status = new.meta[order].status
        prev_status = prev.meta[order].status if order < prev_meta_len else CardStatus.NONE

        if new_status != prev_status:
            if new_status == CardStatus.CALLED_TO_PLAY:
                out.append(
                    (order, format_play_segment(state.turn_count, me_new.thoughts[order].inferred, state))
                )
            elif new_status == CardStatus.CALLED_TO_DISCARD:
                out.append((order, format_discard_segment(state.turn_count)))
            elif new_status == CardStatus.NONE and prev_status in (
                CardStatus.CALLED_TO_PLAY,
                CardStatus.CALLED_TO_DISCARD,
            ):
                out.append((order, format_reset_segment(state.turn_count)))
            continue

        # No status change — check for inferred narrowing while called-to-play.
        if new_status != CardStatus.CALLED_TO_PLAY:
            continue
        if order >= prev_thought_len:
            continue
        prev_inferred = me_prev.thoughts[order].inferred
        new_inferred = me_new.thoughts[order].inferred
        if new_inferred != prev_inferred and new_inferred.length < prev_inferred.length:
            out.append(
                (order, format_play_segment(state.turn_count, new_inferred, state))
            )

    return out
