"""Pure game rules: no I/O, no database, no Telegram. Everything here is unit-testable."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Protocol

MAX_HP = 100
MAX_FINISHERS = 1
MAX_REVERSALS = 2            # base Reversal charges per match (a Wild Draw can add a bonus one)
REVERSAL_CHANCE = 1.0        # an attempted Reversal always works (1.0 = 100%)
MAX_WILD_DRAWS = 1           # Wild Draw cards per match


class Move(str, Enum):
    FINISHER = "finisher"
    F5 = "f5"
    SPEAR = "spear"
    SUPERKICK = "superkick"
    DDT = "ddt"
    REVERSAL = "reversal"


ATTACK_DAMAGE: dict[Move, int] = {
    Move.FINISHER: 40,
    Move.F5: 30,
    Move.SPEAR: 25,
    Move.SUPERKICK: 10,
    Move.DDT: 5,
}
ATTACKS = tuple(ATTACK_DAMAGE)

# Wild Draw: relative weights of what the card can turn into (they add up to 100).
# Reversal is not an attack: drawing it grants one bonus Reversal charge instead.
WILD_WEIGHTS: dict[Move, int] = {
    Move.F5: 20,
    Move.SPEAR: 20,
    Move.SUPERKICK: 20,
    Move.DDT: 10,
    Move.FINISHER: 10,
    Move.REVERSAL: 20,
}

MOVE_NAMES: dict[Move, str] = {
    Move.FINISHER: "FINISHER",
    Move.F5: "F5",
    Move.SPEAR: "SPEAR",
    Move.SUPERKICK: "SUPER KICK",
    Move.DDT: "DDT",
    Move.REVERSAL: "REVERSAL",
}
MOVE_EMOJI: dict[Move, str] = {
    Move.FINISHER: "🔥",
    Move.F5: "💣",
    Move.SPEAR: "💥",
    Move.SUPERKICK: "⚡",
    Move.DDT: "💢",
    Move.REVERSAL: "🛡️",
}


class RuleError(ValueError):
    """Raised when a move breaks a rule. The message is safe to show to players."""


class FighterLike(Protocol):
    user_id: int
    hp: int
    finishers_used: int
    reversals_used: int
    bonus_reversals: int
    wild_draws_used: int
    last_attack: str | None


@dataclass
class PlayerState:
    """Plain stand-in with the same fields as the ``MatchPlayer`` ORM row."""

    user_id: int
    hp: int = MAX_HP
    finishers_used: int = 0
    reversals_used: int = 0
    bonus_reversals: int = 0
    wild_draws_used: int = 0
    last_attack: str | None = None


def finishers_left(p: FighterLike) -> int:
    return MAX_FINISHERS - p.finishers_used


def reversals_left(p: FighterLike) -> int:
    """Remaining Reversal charges (base + Wild Draw bonus - attempts made, success or not)."""
    return max(0, MAX_REVERSALS + p.bonus_reversals - p.reversals_used)


def wild_draws_left(p: FighterLike) -> int:
    return max(0, MAX_WILD_DRAWS - p.wild_draws_used)


def parse_move(value: str) -> Move:
    try:
        return Move(value)
    except ValueError as exc:
        raise RuleError("Unknown move.") from exc


def validate_attack(player: FighterLike, move: Move, ignore_repeat: bool = False) -> None:
    """``ignore_repeat`` is used for Wild Draw moves, which skip the no-consecutive-repeat rule."""
    if move == Move.REVERSAL:
        raise RuleError("🛡️ Reversal is not an attack — it can only be used when an opponent's attack is incoming.")
    if move not in ATTACK_DAMAGE:
        raise RuleError("Unknown move.")
    if move == Move.FINISHER and player.finishers_used >= MAX_FINISHERS:
        raise RuleError(f"🔥 You have no Finishers left ({MAX_FINISHERS}/{MAX_FINISHERS} used).")
    if not ignore_repeat and player.last_attack == move.value:
        raise RuleError(f"🚫 You can't use {MOVE_NAMES[move]} twice in a row. Pick a different attack.")


def validate_reversal(player: FighterLike) -> None:
    if reversals_left(player) <= 0:
        raise RuleError("🛡️ You have no Reversals left.")


def validate_wild_draw(player: FighterLike) -> None:
    if wild_draws_left(player) <= 0:
        raise RuleError("🎴 You already used your Wild Draw this match.")


def draw_wild(player: FighterLike, rng: random.Random) -> Move:
    """Pick the Wild Draw result. A Finisher can't come up once the player has none left."""
    pool = [(m, w) for m, w in WILD_WEIGHTS.items()
            if not (m == Move.FINISHER and finishers_left(player) <= 0)]
    return rng.choices([m for m, _ in pool], weights=[w for _, w in pool], k=1)[0]


def reversal_succeeds(rng: random.Random) -> bool:
    """Roll for an attempted Reversal."""
    return rng.random() < REVERSAL_CHANCE


def available_attacks(player: FighterLike) -> list[Move]:
    out = []
    for move in ATTACKS:
        try:
            validate_attack(player, move)
        except RuleError:
            continue
        out.append(move)
    return out


@dataclass(frozen=True)
class Resolution:
    move: Move
    original_damage: int
    reversed: bool
    damage_to_defender: int
    damage_to_attacker: int
    winner_id: int | None
    reversal_attempted: bool = False   # ``reversed`` means the attempt succeeded


def resolve_attack(attacker: FighterLike, defender: FighterLike, move: Move, reversed_: bool,
                   reversal_succeeds: bool = True, wild: bool = False) -> Resolution:
    """Apply one attack exactly once, mutating both fighters.

    * normal hit: defender loses the move's damage
    * ``reversed_`` = the defender *attempted* a Reversal. Every attempt consumes one charge:
      - succeeded: the damage is reflected onto the attacker, the defender takes nothing
      - failed (``reversal_succeeds=False``): the defender takes the full damage as usual
    * ``wild``: the move came from a Wild Draw, so the no-repeat rule is skipped
    * the attacker's Finisher/last-attack bookkeeping is updated exactly once
    """
    validate_attack(attacker, move, ignore_repeat=wild)
    if reversed_:
        validate_reversal(defender)
    damage = ATTACK_DAMAGE[move]
    success = reversed_ and reversal_succeeds

    attacker.last_attack = move.value
    if move == Move.FINISHER:
        attacker.finishers_used += 1
    if reversed_:
        defender.reversals_used += 1

    to_defender = to_attacker = 0
    if success:
        to_attacker = min(damage, attacker.hp)
        attacker.hp -= to_attacker
    else:
        to_defender = min(damage, defender.hp)
        defender.hp -= to_defender

    winner = None
    if defender.hp <= 0:
        winner = attacker.user_id
    elif attacker.hp <= 0:
        winner = defender.user_id
    return Resolution(move, damage, success, to_defender, to_attacker, winner, reversed_)


class TurnLike(Protocol):
    attacker_id: int
    target_id: int
    move: str
    reversed: bool
    damage_to_target: int
    damage_to_attacker: int


@dataclass
class MatchSummary:
    total_turns: int = 0
    damage_dealt: dict[int, int] = field(default_factory=dict)
    damage_received: dict[int, int] = field(default_factory=dict)
    finishers: dict[int, int] = field(default_factory=dict)
    reversals: dict[int, int] = field(default_factory=dict)


def summarize_turns(turns: Iterable[TurnLike], user_ids: Iterable[int]) -> MatchSummary:
    """Compute match statistics from the turn history (never hardcoded)."""
    ids = list(user_ids)
    s = MatchSummary(
        damage_dealt={u: 0 for u in ids}, damage_received={u: 0 for u in ids},
        finishers={u: 0 for u in ids}, reversals={u: 0 for u in ids})
    for t in turns:
        s.total_turns += 1
        # damage the attacker inflicted on the target, and reflected damage the target inflicted back
        s.damage_dealt[t.attacker_id] = s.damage_dealt.get(t.attacker_id, 0) + t.damage_to_target
        s.damage_received[t.target_id] = s.damage_received.get(t.target_id, 0) + t.damage_to_target
        s.damage_dealt[t.target_id] = s.damage_dealt.get(t.target_id, 0) + t.damage_to_attacker
        s.damage_received[t.attacker_id] = s.damage_received.get(t.attacker_id, 0) + t.damage_to_attacker
        if t.move == Move.FINISHER.value:
            s.finishers[t.attacker_id] = s.finishers.get(t.attacker_id, 0) + 1
        if t.reversed:
            s.reversals[t.target_id] = s.reversals.get(t.target_id, 0) + 1
    return s


def win_rate(wins: int, total: int) -> int | None:
    return None if total <= 0 else round(100 * wins / total)
