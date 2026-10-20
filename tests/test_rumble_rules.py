import random

from services import rumble_rules as rr


def test_heal_is_capped():
    assert rr.heal(100) == 125
    assert rr.heal(140) == rr.RUMBLE_HP == 150


def test_shuffle_keeps_everyone():
    order = rr.shuffle_order([1, 2, 3, 4, 5], random.Random(3))
    assert sorted(order) == [1, 2, 3, 4, 5]


def test_next_actor_goes_round_the_ring():
    ring = [(1, 10), (2, 20), (4, 40)]
    assert rr.next_actor(ring, 0) == 10
    assert rr.next_actor(ring, 1) == 20
    assert rr.next_actor(ring, 2) == 40
    assert rr.next_actor(ring, 4) == 10          # wraps


def test_next_actor_when_last_actor_was_eliminated():
    assert rr.next_actor([(1, 10), (4, 40)], 2) == 40    # #2 is gone: carry on with #4


def test_next_actor_empty_ring():
    assert rr.next_actor([], 0) is None


def test_hp_bar_scales_to_rumble_hp():
    assert rr.hp_bar(rr.RUMBLE_HP) == "▰" * 10
    assert rr.hp_bar(0) == "▱" * 10
