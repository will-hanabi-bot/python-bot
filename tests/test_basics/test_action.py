"""Action ADT and PerformAction ADT: construction, JSON round-trip, match/case."""

from __future__ import annotations

import pytest

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
    StatusAction,
    StrikeAction,
    TurnAction,
    action_from_json,
    perform_action_from_json,
)
from hanabi_bot.basics.clue import BaseClue, ClueKind

# --- Action: properties ---


def test_status_action_properties() -> None:
    a = StatusAction(clues=5, score=10, max_score=25)
    assert a.player_index == -1
    assert a.requires_draw is False
    assert a.is_player_action is False


def test_turn_action_properties() -> None:
    a = TurnAction(num=3, current_player_index=2)
    assert a.player_index == 2
    assert a.is_player_action is False


def test_clue_action_properties() -> None:
    a = ClueAction(giver=0, target=1, list_=(3, 4), clue=BaseClue(ClueKind.COLOUR, 2))
    assert a.player_index == 0
    assert a.is_player_action is True
    assert a.requires_draw is False


def test_play_action_requires_draw() -> None:
    a = PlayAction(player_index=0, order=5, suit_index=0, rank=1)
    assert a.requires_draw is True
    assert a.is_player_action is True


def test_discard_action_requires_draw() -> None:
    a = DiscardAction(player_index=0, order=5, suit_index=0, rank=1, failed=False)
    assert a.requires_draw is True
    a_bombed = DiscardAction(player_index=0, order=5, suit_index=-1, rank=-1, failed=True)
    assert a_bombed.failed is True


def test_strike_action_properties() -> None:
    a = StrikeAction(num=1, turn=10, order=20)
    assert a.player_index == -1


def test_game_over_action() -> None:
    a = GameOverAction(end_condition=1, player_index=0)
    assert a.player_index == 0


# --- Action: from_json ---


def test_action_from_json_status() -> None:
    a = action_from_json({"type": "status", "clues": 5, "score": 10, "maxScore": 25})
    assert a == StatusAction(5, 10, 25)


def test_action_from_json_turn() -> None:
    a = action_from_json({"type": "turn", "num": 3, "currentPlayerIndex": 1})
    assert a == TurnAction(3, 1)


def test_action_from_json_clue_colour() -> None:
    a = action_from_json(
        {
            "type": "clue",
            "giver": 0,
            "target": 1,
            "list": [3, 4],
            "clue": {"type": 0, "value": 2},
        }
    )
    assert a == ClueAction(0, 1, (3, 4), BaseClue(ClueKind.COLOUR, 2))


def test_action_from_json_clue_rank() -> None:
    a = action_from_json(
        {
            "type": "clue",
            "giver": 0,
            "target": 1,
            "list": [3],
            "clue": {"type": 1, "value": 3},
        }
    )
    assert a == ClueAction(0, 1, (3,), BaseClue(ClueKind.RANK, 3))


def test_action_from_json_draw() -> None:
    a = action_from_json({"type": "draw", "playerIndex": 0, "order": 5, "suitIndex": -1, "rank": -1})
    assert a == DrawAction(0, 5, -1, -1)


def test_action_from_json_play() -> None:
    a = action_from_json({"type": "play", "playerIndex": 0, "order": 5, "suitIndex": 0, "rank": 1})
    assert a == PlayAction(0, 5, 0, 1)


def test_action_from_json_discard() -> None:
    a = action_from_json(
        {"type": "discard", "playerIndex": 0, "order": 5, "suitIndex": 1, "rank": 2, "failed": False}
    )
    assert a == DiscardAction(0, 5, 1, 2, False)


def test_action_from_json_strike() -> None:
    a = action_from_json({"type": "strike", "num": 1, "turn": 10, "order": 20})
    assert a == StrikeAction(1, 10, 20)


def test_action_from_json_game_over() -> None:
    a = action_from_json({"type": "gameOver", "endCondition": 1, "playerIndex": 0})
    assert a == GameOverAction(1, 0)


def test_action_from_json_unknown_returns_none() -> None:
    assert action_from_json({"type": "weird-unknown"}) is None


# --- Action: match/case exhaustiveness ---


def _player_for(a: Action) -> int:
    """A pattern-match harness that touches every Action subtype."""
    match a:
        case StatusAction():
            return -10
        case TurnAction(num=_, current_player_index=p):
            return p
        case ClueAction(giver=g):
            return g
        case DrawAction(player_index=p):
            return p
        case PlayAction(player_index=p):
            return p
        case DiscardAction(player_index=p):
            return p
        case StrikeAction():
            return -20
        case GameOverAction(player_index=p):
            return p
        case _:
            return -99


def test_match_case_dispatches_correctly() -> None:
    assert _player_for(StatusAction(0, 0, 0)) == -10
    assert _player_for(TurnAction(1, 7)) == 7
    assert _player_for(ClueAction(2, 3, (), BaseClue(ClueKind.RANK, 1))) == 2
    assert _player_for(DrawAction(4, 0, -1, -1)) == 4
    assert _player_for(PlayAction(5, 0, 0, 0)) == 5
    assert _player_for(DiscardAction(6, 0, 0, 0, False)) == 6
    assert _player_for(StrikeAction(1, 1, 1)) == -20
    assert _player_for(GameOverAction(1, 8)) == 8


# --- PerformAction ---


def test_perform_play_json() -> None:
    a = PerformPlay(target=3)
    assert a.is_clue is False
    assert a.hash_int == 3
    assert a.to_json(table_id=42) == {"tableID": 42, "type": 0, "target": 3}


def test_perform_discard_json() -> None:
    a = PerformDiscard(target=2)
    assert a.is_clue is False
    assert a.hash_int == 12
    assert a.to_json(table_id=42) == {"tableID": 42, "type": 1, "target": 2}


def test_perform_colour_json() -> None:
    a = PerformColour(target=1, value=0)
    assert a.is_clue is True
    assert a.hash_int == 21  # 20 + target + value*100 = 20 + 1 + 0
    assert a.to_json(table_id=7) == {"tableID": 7, "type": 2, "target": 1, "value": 0}


def test_perform_rank_json() -> None:
    a = PerformRank(target=1, value=3)
    assert a.is_clue is True
    assert a.hash_int == 331  # 30 + 1 + 3*100
    assert a.to_json(table_id=7) == {"tableID": 7, "type": 3, "target": 1, "value": 3}


def test_perform_terminate_json() -> None:
    a = PerformTerminate(target=0, value=0)
    assert a.hash_int == -1
    assert a.to_json(table_id=7) == {"tableID": 7, "type": 4, "target": 0, "value": 0}


def test_perform_action_from_json_round_trip() -> None:
    cases: list[PerformAction] = [
        PerformPlay(2),
        PerformDiscard(3),
        PerformColour(1, 4),
        PerformRank(1, 5),
        PerformTerminate(0, 0),
    ]
    for orig in cases:
        encoded = orig.to_json(table_id=99)
        decoded = perform_action_from_json(encoded)
        assert decoded == orig


def test_perform_action_from_json_invalid_type() -> None:
    with pytest.raises(ValueError):
        perform_action_from_json({"type": 99, "target": 0})


# --- PerformAction: match/case ---


def _describe(a: PerformAction) -> str:
    match a:
        case PerformPlay(target=t):
            return f"play:{t}"
        case PerformDiscard(target=t):
            return f"discard:{t}"
        case PerformColour(target=t, value=v):
            return f"colour:{t}:{v}"
        case PerformRank(target=t, value=v):
            return f"rank:{t}:{v}"
        case PerformTerminate():
            return "terminate"


def test_perform_action_match() -> None:
    assert _describe(PerformPlay(3)) == "play:3"
    assert _describe(PerformDiscard(4)) == "discard:4"
    assert _describe(PerformColour(1, 2)) == "colour:1:2"
    assert _describe(PerformRank(1, 3)) == "rank:1:3"
    assert _describe(PerformTerminate(0, 0)) == "terminate"
