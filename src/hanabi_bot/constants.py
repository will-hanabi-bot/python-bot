"""Wire-protocol constants. Must match hanab.live server constants:
https://github.com/Hanabi-Live/hanabi-live/blob/main/server/src/constants.go
"""

import enum


class ACTION(int, enum.Enum):
    PLAY = 0
    DISCARD = 1
    COLOR_CLUE = 2
    RANK_CLUE = 3


MAX_CLUE_NUM = 8
COLOR_CLUE = 0
RANK_CLUE = 1
