"""Pure Tag Team rules."""
from __future__ import annotations

TEAM_SIZE = 2            # default team size (/tag, /tag 3, /tag 4)
MIN_TEAM_SIZE = 2
MAX_TEAM_SIZE = 4
MIN_PER_TEAM = 2          # a match needs at least this many wrestlers on each side
APRON_HEAL = 5            # the partner resting on the apron recovers this much after every turn
AFK_LIMIT = 2             # skipped turns in a row before a wrestler is eliminated
TEAMS = {1: "LIONS", 2: "TIGERS"}
TEAM_EMOJI = {1: "🦁", 2: "🐯"}


def other_team(team: int) -> int:
    return 2 if team == 1 else 1


def rest(hp: int, max_hp: int) -> int:
    """HP after resting on the apron for one turn."""
    return min(max_hp, hp + APRON_HEAL)


def valid_teams(a: int, b: int) -> bool:
    """Can a match start with ``a`` Lions and ``b`` Tigers? Equal sides, or one player apart."""
    return a >= MIN_PER_TEAM and b >= MIN_PER_TEAM and abs(a - b) <= 1


def compensation(a: int, b: int) -> int:
    """Extra Reversals for every wrestler on the smaller team (1 when the sides differ by one)."""
    return 1 if abs(a - b) == 1 else 0
