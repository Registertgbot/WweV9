"""Pure Money in the Bank rules: bracket planning, timings, ladder constants, tie-breaks.

Fairness principles
* Seeding and the first move are random — nobody is favoured.
* Byes (odd player counts) never go to the same player twice if it can be avoided, and give no bonus.
* Every duel starts from the same stats (100 HP, 1 Finisher, 2 Reversals); a duel that overruns its time
  limit is decided by remaining HP, then damage dealt, then a coin flip.
* The ladder is capped (12 turns each): highest rung wins, then HP, then damage, then a coin flip.
* A cash-in never faces a wrestler below CASHIN_FLOOR_HP, and the defender strikes first.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

MIN_PLAYERS = 4
SIZES = (8, 16, 32)            # /mitb, /mitb 16, /mitb 32
LADDER_MAX = 4                 # the qualifier runs until this many wrestlers are left
RUNGS = 5
LADDER_ROUNDS_CAP = 12         # turns per wrestler before the ladder is decided on points
STUN_HP = 30                   # a wrestler knocked down on the ladder comes back with this HP
CASHIN_FLOOR_HP = 70          # measured: holder wins ~67% at worst (86% with a floor of 40 was too lopsided)
CASHIN_WINDOW = 20             # seconds the briefcase holder has to cash in
BRIEFCASE_DAYS = 7
AFK_LIMIT = 2


@dataclass(frozen=True)
class Timing:
    lobby: int          # seconds the join lobby stays open
    turn: int           # seconds for a duel / ladder turn
    reversal: int       # seconds to answer an attack
    duel_cap: int       # hard time limit for one duel
    round_gap: int      # unused pause between rounds (kept for tuning)


TIMINGS = {
    "fast": Timing(lobby=180, turn=40, reversal=15, duel_cap=12 * 60, round_gap=0),
    "slow": Timing(lobby=900, turn=600, reversal=300, duel_cap=2 * 3600, round_gap=0),
}


def timing(mode: str) -> Timing:
    return TIMINGS.get(mode, TIMINGS["fast"])


def plan_round(players: list[int], had_bye: set[int], rng: random.Random) -> tuple[list[tuple[int, int]], int | None]:
    """Random pairs for a qualifier round; with an odd count one player gets a bye."""
    order = list(players)
    rng.shuffle(order)
    bye = None
    if len(order) % 2 == 1:
        fresh = [p for p in order if p not in had_bye]
        bye = rng.choice(fresh or order)
        order.remove(bye)
    return [(order[i], order[i + 1]) for i in range(0, len(order), 2)], bye


def rounds_needed(count: int) -> int:
    """How many qualifier rounds before the ladder (<= LADDER_MAX wrestlers left)."""
    rounds = 0
    while count > LADDER_MAX:
        count = (count + 1) // 2
        rounds += 1
    return rounds


def pick_by_points(entries: list[tuple], rng: random.Random):
    """entries = [(key, score_tuple)]; the highest score wins, exact ties go to a coin flip."""
    best = max(score for _, score in entries)
    tied = [key for key, score in entries if score == best]
    return rng.choice(tied) if len(tied) > 1 else tied[0]


def cashin_hp(target_hp: int) -> int:
    return max(target_hp, CASHIN_FLOOR_HP)
