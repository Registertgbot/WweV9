import random

import pytest

from services import rules
from services.rules import Move, PlayerState, RuleError, resolve_attack


class Roll:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


def test_successful_reversal_reflects_damage_exactly_once():
    a, b = PlayerState(1), PlayerState(2)
    res = resolve_attack(a, b, Move.SPEAR, reversed_=True)
    assert (a.hp, b.hp) == (75, 100)               # attacker hurt, defender untouched
    assert res.damage_to_attacker == 25 and res.damage_to_defender == 0
    assert res.reversed and res.reversal_attempted
    assert b.reversals_used == 1 and a.reversals_used == 0


def test_no_reversal_attempt_applies_normal_damage():
    a, b = PlayerState(1), PlayerState(2)
    res = resolve_attack(a, b, Move.SPEAR, reversed_=False)
    assert (a.hp, b.hp) == (100, 75)
    assert b.reversals_used == 0 and not res.reversed and not res.reversal_attempted


def test_failed_reversal_takes_full_damage_and_still_costs_a_charge():
    a, b = PlayerState(1), PlayerState(2)
    res = resolve_attack(a, b, Move.SPEAR, reversed_=True, reversal_succeeds=False)
    assert (a.hp, b.hp) == (100, 75)
    assert res.damage_to_defender == 25 and res.damage_to_attacker == 0
    assert res.reversal_attempted and not res.reversed
    assert b.reversals_used == 1 and rules.reversals_left(b) == 1


def test_failed_reversal_can_knock_out_the_defender():
    a, b = PlayerState(1), PlayerState(2, hp=20)
    res = resolve_attack(a, b, Move.F5, reversed_=True, reversal_succeeds=False)
    assert b.hp == 0 and res.winner_id == 1


def test_reflected_finisher_costs_attacker_40_and_consumes_one_finisher():
    a, b = PlayerState(1), PlayerState(2)
    resolve_attack(a, b, Move.FINISHER, reversed_=True)
    assert a.hp == 60 and b.hp == 100
    assert a.finishers_used == 1          # not deducted twice


def test_only_two_reversal_attempts_per_match():
    a, b = PlayerState(1), PlayerState(2)
    resolve_attack(a, b, Move.DDT, reversed_=True)                              # success
    resolve_attack(a, b, Move.SUPERKICK, reversed_=True, reversal_succeeds=False)  # failure, still counts
    assert b.reversals_used == 2 and rules.reversals_left(b) == 0
    with pytest.raises(RuleError):
        resolve_attack(a, b, Move.F5, reversed_=True)           # a third attempt is refused
    assert (a.hp, b.hp) == (100 - 5, 100 - 10)


def test_wild_draw_bonus_reversal_allows_a_third_attempt_and_no_more():
    a, b = PlayerState(1), PlayerState(2, bonus_reversals=1)
    for move in (Move.DDT, Move.SUPERKICK, Move.DDT):
        resolve_attack(a, b, move, reversed_=True)
        a.last_attack = None
    assert b.reversals_used == 3 and rules.reversals_left(b) == 0
    with pytest.raises(RuleError):
        resolve_attack(a, b, Move.F5, reversed_=True)


def test_reversal_cannot_counter_a_reversal():
    a, b = PlayerState(1), PlayerState(2)
    with pytest.raises(RuleError):
        resolve_attack(a, b, Move.REVERSAL, reversed_=True)
    assert (a.hp, b.hp) == (100, 100)


def test_reversal_roll_follows_the_configured_chance():
    assert rules.REVERSAL_CHANCE == 1.0
    assert rules.reversal_succeeds(Roll(0.0)) and rules.reversal_succeeds(Roll(0.5))
    assert rules.reversal_succeeds(Roll(0.999999))


def test_reversal_always_succeeds():
    rng = random.Random(1234)
    assert all(rules.reversal_succeeds(rng) for _ in range(10000))


def test_normal_knockout_sets_winner_and_floors_hp_at_zero():
    a, b = PlayerState(1), PlayerState(2, hp=15)
    res = resolve_attack(a, b, Move.F5, reversed_=False)
    assert b.hp == 0 and res.winner_id == 1 and res.damage_to_defender == 15


def test_reflected_damage_can_knock_out_the_attacker():
    a, b = PlayerState(1, hp=20), PlayerState(2)
    res = resolve_attack(a, b, Move.SPEAR, reversed_=True)
    assert a.hp == 0 and b.hp == 100 and res.winner_id == 2


def test_no_winner_while_both_alive():
    a, b = PlayerState(1), PlayerState(2)
    assert resolve_attack(a, b, Move.DDT, False).winner_id is None


def test_failed_resolution_does_not_mutate_state():
    a, b = PlayerState(1, last_attack="spear"), PlayerState(2)
    with pytest.raises(RuleError):
        resolve_attack(a, b, Move.SPEAR, False)
    assert (a.hp, b.hp, a.finishers_used) == (100, 100, 0)
