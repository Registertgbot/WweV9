import random

from services import mitb_rules as mr


def test_rounds_needed():
    assert [mr.rounds_needed(n) for n in (4, 5, 8, 9, 16, 32)] == [0, 1, 1, 2, 2, 3]


def test_plan_round_even_has_no_bye_and_pairs_everyone_once():
    pairs, bye = mr.plan_round(list(range(8)), set(), random.Random(1))
    assert bye is None and len(pairs) == 4
    assert sorted(x for pair in pairs for x in pair) == list(range(8))


def test_plan_round_odd_gives_a_bye_to_someone_who_has_not_had_one():
    for seed in range(50):
        pairs, bye = mr.plan_round(list(range(7)), {0, 1, 2}, random.Random(seed))
        assert bye in {3, 4, 5, 6}
        assert bye not in {x for pair in pairs for x in pair}


def test_plan_round_everyone_had_a_bye_still_works():
    pairs, bye = mr.plan_round(list(range(5)), set(range(5)), random.Random(2))
    assert bye is not None and len(pairs) == 2


def test_pick_by_points_prefers_higher_score_and_flips_a_coin_on_ties():
    assert mr.pick_by_points([("a", (3, 50)), ("b", (3, 60))], random.Random(1)) == "b"
    seen = {mr.pick_by_points([("a", (1,)), ("b", (1,))], random.Random(s)) for s in range(40)}
    assert seen == {"a", "b"}


def test_cashin_hp_has_a_floor():
    assert mr.cashin_hp(5) == mr.CASHIN_FLOOR_HP and mr.cashin_hp(80) == 80


def test_timings():
    assert mr.timing("slow").turn > mr.timing("fast").turn
    assert mr.timing("unknown") == mr.timing("fast")
