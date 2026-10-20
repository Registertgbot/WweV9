"""Pure rule tests (no database, no Telegram)."""
import random

import pytest

from services import rules
from services.rules import Move, PlayerState, RuleError, resolve_attack, validate_attack


def pair():
    return PlayerState(1), PlayerState(2)


def test_both_players_start_with_100_hp():
    a, b = pair()
    assert a.hp == b.hp == 100
    assert rules.finishers_left(a) == 1 and rules.reversals_left(a) == 2
    assert rules.wild_draws_left(a) == 1


@pytest.mark.parametrize("move,dmg", [(Move.FINISHER, 40), (Move.F5, 30), (Move.SPEAR, 25),
                                      (Move.SUPERKICK, 10), (Move.DDT, 5)])
def test_damage_values(move, dmg):
    a, b = pair()
    res = resolve_attack(a, b, move, reversed_=False)
    assert res.original_damage == dmg and res.damage_to_defender == dmg
    assert b.hp == 100 - dmg and a.hp == 100


def test_finisher_cannot_be_used_more_than_once():
    a, b = pair()
    resolve_attack(a, b, Move.FINISHER, False)
    assert a.finishers_used == 1
    resolve_attack(a, b, Move.DDT, False)
    with pytest.raises(RuleError):
        validate_attack(a, Move.FINISHER)


@pytest.mark.parametrize("move", list(rules.ATTACKS))
def test_repeated_attacking_move_rejected(move):
    a, b = pair()
    b.hp = 100
    resolve_attack(a, b, move, False)
    with pytest.raises(RuleError):
        validate_attack(a, move)


def test_different_move_after_attack_is_fine():
    a, b = pair()
    resolve_attack(a, b, Move.SPEAR, False)
    validate_attack(a, Move.F5)  # no error


def test_reversal_is_not_an_attack():
    a, _ = pair()
    with pytest.raises(RuleError):
        validate_attack(a, Move.REVERSAL)


def test_unknown_move_rejected():
    with pytest.raises(RuleError):
        rules.parse_move("chokeslam")


def test_available_attacks_excludes_last_move_and_spent_finishers():
    a = PlayerState(1, finishers_used=1, last_attack="spear")
    assert Move.FINISHER not in rules.available_attacks(a)
    assert Move.SPEAR not in rules.available_attacks(a)
    assert Move.F5 in rules.available_attacks(a)


def test_summary_is_calculated_from_turns():
    from types import SimpleNamespace as T
    turns = [
        T(attacker_id=1, target_id=2, move="finisher", reversed=False, damage_to_target=40, damage_to_attacker=0),
        T(attacker_id=2, target_id=1, move="spear", reversed=True, damage_to_target=0, damage_to_attacker=25),
        T(attacker_id=1, target_id=2, move="ddt", reversed=False, damage_to_target=5, damage_to_attacker=0),
    ]
    s = rules.summarize_turns(turns, [1, 2])
    assert s.total_turns == 3
    # player 1: 40 + 5 direct, plus 25 reflected back at player 2 when player 1 reversed the Spear
    assert s.damage_dealt == {1: 70, 2: 0}
    assert s.damage_received == {1: 0, 2: 70}
    assert s.finishers == {1: 1, 2: 0}
    assert s.reversals == {1: 1, 2: 0}


def test_wild_draw_weights_add_up_to_100_and_cover_every_move():
    assert sum(rules.WILD_WEIGHTS.values()) == 100
    assert set(rules.WILD_WEIGHTS) == set(Move)


def test_wild_draw_can_give_any_move_including_a_reversal():
    rng, a = random.Random(42), PlayerState(1)
    assert {rules.draw_wild(a, rng) for _ in range(600)} == set(Move)


def test_wild_draw_never_gives_a_finisher_when_none_are_left():
    rng, a = random.Random(7), PlayerState(1, finishers_used=1)
    seen = {rules.draw_wild(a, rng) for _ in range(600)}
    assert Move.FINISHER not in seen and Move.REVERSAL in seen


def test_wild_draw_frequencies_follow_the_weights():
    rng, a = random.Random(2026), PlayerState(1)
    n = 20000
    counts = {m: 0 for m in Move}
    for _ in range(n):
        counts[rules.draw_wild(a, rng)] += 1
    for move, weight in rules.WILD_WEIGHTS.items():
        assert abs(counts[move] / n - weight / 100) < 0.02, move


def test_wild_move_ignores_the_no_repeat_rule_but_normal_attacks_do_not():
    a, b = PlayerState(1, last_attack="spear"), PlayerState(2)
    with pytest.raises(RuleError):
        resolve_attack(a, b, Move.SPEAR, False)
    res = resolve_attack(a, b, Move.SPEAR, False, wild=True)
    assert res.damage_to_defender == 25 and b.hp == 75


def test_wild_finisher_still_counts_as_a_finisher_use():
    a, b = PlayerState(1), PlayerState(2)
    resolve_attack(a, b, Move.FINISHER, False, wild=True)
    assert a.finishers_used == 1


def test_only_one_wild_draw_per_match():
    a = PlayerState(1)
    rules.validate_wild_draw(a)
    a.wild_draws_used = 1
    with pytest.raises(RuleError):
        rules.validate_wild_draw(a)


def test_win_rate_handles_no_matches():
    assert rules.win_rate(0, 0) is None
    assert rules.win_rate(3, 4) == 75
