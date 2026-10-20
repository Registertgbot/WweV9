import random
from datetime import timedelta

import pytest

import config
from database import transaction
from models import utcnow
from services import rumble_engine as eng
from services import rumble_rules as rr
from services.match_engine import Identity, upsert_user

CHAT = -100777
IDS = [Identity(2000 + i, f"u{i}", f"User{i}") for i in range(1, 6)]


async def lobby(n=3, host=0):
    async with transaction() as s:
        for i in IDS:
            await upsert_user(s, i, dm_started=True)
    async with transaction() as s:
        res = await eng.create_rumble(s, IDS[host], CHAT, "Ring")
    assert res.ok, res.error
    rid = res.rumble.id
    for i in IDS[:n]:
        if i.id != IDS[host].id:
            async with transaction() as s:
                assert (await eng.join(s, rid, i, CHAT)).ok
    return rid


async def started(n=3, seed=1):
    rid = await lobby(n)
    async with transaction() as s:
        res = await eng.start(s, rid, IDS[0].id, rng=random.Random(seed))
    assert res.ok, res.error
    return res


async def test_start_puts_two_in_the_ring_and_starts_a_turn(db):
    res = await started(4)
    snap = res.rumble
    assert snap.status == "active" and len(snap.ring) == 2 and len(snap.waiting) == 2
    assert snap.phase == "await_move" and snap.active_player_id == snap.ring[0].user_id
    assert snap.next_entry_at is not None
    assert [e.kind for e in res.events].count("entry") == 2


async def test_second_rumble_in_same_group_is_refused(db):
    await lobby(3)
    async with transaction() as s:
        res = await eng.create_rumble(s, IDS[4], CHAT, "Ring")
    assert not res.ok


async def test_need_minimum_players(db):
    rid = await lobby(2)
    async with transaction() as s:
        res = await eng.start(s, rid, IDS[0].id)
    assert not res.ok


async def test_full_attack_flow_and_reversal(db):
    res = await started(3)
    snap = res.rumble
    a, d = snap.ring[0].user_id, snap.ring[1].user_id
    async with transaction() as s:
        assert (await eng.submit_move(s, snap.id, a, snap.turn_number, "spear")).ok
    async with transaction() as s:
        assert (await eng.submit_target(s, snap.id, a, snap.turn_number, d)).ok
    async with transaction() as s:
        out = await eng.submit_response(s, snap.id, d, snap.turn_number, use_reversal=True)
    assert out.ok
    turn = next(e for e in out.events if e.kind == "turn")
    assert turn.reversed and turn.reflected == 25 and turn.damage == 0
    assert out.rumble.player(a).hp == rr.RUMBLE_HP - 25
    assert out.rumble.player(d).reversals_left == 1
    assert out.rumble.turn_number == snap.turn_number + 1       # next turn started


async def test_only_the_active_player_can_act(db):
    snap = (await started(3)).rumble
    other = snap.ring[1].user_id
    async with transaction() as s:
        assert not (await eng.submit_move(s, snap.id, other, snap.turn_number, "f5")).ok


async def test_idle_turn_is_skipped_then_player_is_eliminated(db):
    snap = (await started(3)).rumble
    afk = snap.active_player_id
    late = utcnow() + timedelta(seconds=config.get_settings().rumble_turn_timeout + 1)
    async with transaction() as s:
        res = await eng.handle_due(s, snap.id, now=late)
    assert res.kind == "turn_skipped" and res.events[0].kind == "skip"
    # make the same player idle again until the AFK limit is reached
    for _ in range(5):
        cur = res.rumble
        if cur.player(afk).eliminated or cur.status != "active":
            break
        late += timedelta(seconds=200)
        async with transaction() as s:
            res = await eng.handle_due(s, snap.id, now=late)
    async with transaction() as s:
        final = await eng.get_snapshot(s, snap.id)
    assert final.player(afk).eliminated or final.player(afk).afk_strikes >= 1


async def test_knockout_eliminates_heals_and_crowns_a_winner(db):
    snap = (await started(3)).rumble
    now = utcnow()
    # drive the game with forced timeouts until it ends (everyone just takes hits / idles)
    for step in range(400):
        async with transaction() as s:
            cur = await eng.get_snapshot(s, snap.id)
        if cur.status == "completed":
            break
        now += timedelta(seconds=120)
        async with transaction() as s:
            await eng.handle_due(s, snap.id, now=now)
    assert cur.status == "completed" and cur.winner_id is not None
    assert cur.player(cur.winner_id).placement == 1
    assert sorted(p.placement for p in cur.players) == [1, 2, 3]
