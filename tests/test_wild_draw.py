"""Wild Draw flow through the database-backed engine."""
from services.rules import Move
from tests.helpers import StubRng, WIN, attack, live_match, respond, roles, snap_of, wild


async def test_wild_attack_skips_the_repeat_rule_and_is_flagged(db):
    snap = await live_match()
    att, dfn = roles(snap)
    await attack(snap.id, att, 1, "spear")
    await respond(snap.id, dfn, 1, False)
    await attack(snap.id, dfn, 2, "ddt")
    await respond(snap.id, att, 2, False)
    assert not (await attack(snap.id, att, 3, "spear")).ok           # a normal repeat is refused
    res = await wild(snap.id, att, 3, rng=StubRng(pick=Move.SPEAR))
    assert res.ok and res.kind == "attack_declared" and res.move == "spear"
    out = await respond(snap.id, dfn, 3, False)
    assert out.turn.wild and out.turn.damage_to_defender == 25
    assert (await snap_of(snap.id)).player(att).wild_draws_left == 0


async def test_wild_draw_reversal_gives_a_bonus_charge_and_keeps_the_turn(db):
    snap = await live_match()
    att, _ = roles(snap)
    res = await wild(snap.id, att, 1, rng=StubRng(pick=Move.REVERSAL))
    assert res.ok and res.kind == "wild_reversal"
    now = await snap_of(snap.id)
    assert now.player(att).reversals_left == 3 and now.player(att).wild_draws_left == 0
    assert now.active_player_id == att and now.turn_number == 1 and now.phase == "await_attack"
    assert (await attack(snap.id, att, 1, "f5")).ok                  # still attacks this turn


async def test_wild_draw_can_only_be_used_once_per_match(db):
    snap = await live_match()
    att, _ = roles(snap)
    assert (await wild(snap.id, att, 1, rng=StubRng(pick=Move.REVERSAL))).ok
    again = await wild(snap.id, att, 1, rng=StubRng(pick=Move.F5))
    assert not again.ok
    assert (await snap_of(snap.id)).player(att).reversals_left == 3   # no second bonus


async def test_wild_draw_only_for_the_active_player_before_attacking(db):
    snap = await live_match()
    att, dfn = roles(snap)
    assert not (await wild(snap.id, dfn, 1)).ok                      # not their turn
    await attack(snap.id, att, 1, "ddt")
    assert not (await wild(snap.id, att, 1)).ok                      # already attacked this turn


async def test_wild_attack_resolves_immediately_if_defender_has_no_reversals(db):
    snap = await live_match()
    att, dfn = roles(snap)
    await attack(snap.id, att, 1, "ddt")
    await respond(snap.id, dfn, 1, True, rng=WIN)                    # dfn: 1 left
    await attack(snap.id, dfn, 2, "f5")
    await respond(snap.id, att, 2, False)
    await attack(snap.id, att, 3, "spear")
    await respond(snap.id, dfn, 3, True, rng=WIN)                    # dfn: 0 left
    await attack(snap.id, dfn, 4, "ddt")
    await respond(snap.id, att, 4, False)
    res = await wild(snap.id, att, 5, rng=StubRng(pick=Move.F5))
    assert res.ok and res.kind == "turn_resolved" and res.turn.wild and res.turn.damage_to_defender == 30
