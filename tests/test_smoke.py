"""Stage 0 smoke test: package imports, wire-protocol constants match server."""

from hanabi_bot import __version__
from hanabi_bot.constants import ACTION, COLOR_CLUE, MAX_CLUE_NUM, RANK_CLUE


def test_version_set() -> None:
    assert __version__


def test_action_enum_values_match_server_protocol() -> None:
    # hanabi-live/server/src/constants.go: ActionType{Play=0, Discard=1, ColorClue=2, RankClue=3}
    assert ACTION.PLAY == 0
    assert ACTION.DISCARD == 1
    assert ACTION.COLOR_CLUE == 2
    assert ACTION.RANK_CLUE == 3


def test_clue_kind_constants() -> None:
    # hanab.live BaseClue.type: 0 = color, 1 = rank
    assert COLOR_CLUE == 0
    assert RANK_CLUE == 1


def test_max_clue_num() -> None:
    assert MAX_CLUE_NUM == 8
