import random
from datetime import timedelta

from database import transaction
from models import utcnow
from services import mitb_engine as eng
from services import mitb_rules as mr
from services.match_engine import Identity, upsert_user

CHAT = -100999
IDS = [Identity(4000 + i, f"m{i}", f"Mitb{i}") for i in range(1, 9)]


async def lobby(n):
    async with transaction() as s:
        for i in IDS:
            await upsert_user(s, i, dm_started=True)
    async with transaction() as s:
        res = await eng.create(s, IDS[0], CHAT, "Ring", capacity=8)
    assert res.ok, res.error
    for ident in IDS[1:n]:
        async with transaction() as s:
            assert (await eng.join(s, res.event.id, ident, CHAT)).ok
    return res.event.id


async def test_four_players_go_straight_to_the_ladder(db):
    eid = await lobby(4)
    async with transaction() as s:
        res = await eng.start(s, eid, IDS[0].id, rng=random.Random(1))
    assert res.ok and res.event.status == "ladder" and len(res.event.ladder) == 4


async def test_eight_players_play_four_duels_then_the_ladder(db):
    eid = await lobby(8)                      # 8 = capacity, so the last join already started the tournament
    async with transaction() as s:
        snap = await eng.get_snapshot(s, eid)
    assert snap.status == "qualifier" and len(snap.duels) == 4 and snap.round_no == 1


async def test_a_duel_forfeit_advances_the_round(db):
    eid = await lobby(8)
    async with transaction() as s:
        snap = await eng.get_snapshot(s, eid)
    now = utcnow()
    for d in snap.duels:                       # everybody idles until each duel is forfeited
        for step in range(4):
            now += timedelta(seconds=mr.timing("fast").turn + 5)
            async with transaction() as s:
                await eng.handle_due_duel(s, d.id, now=now)
    async with transaction() as s:
        after = await eng.get_snapshot(s, eid)
    assert after.status == "ladder" and len(after.ladder) == 4


async def test_wrong_player_cannot_move_in_a_duel(db):
    eid = await lobby(8)
    async with transaction() as s:
        snap = await eng.get_snapshot(s, eid)
    d = snap.duels[0]
    other = d.other(d.active_user_id).user_id
    async with transaction() as s:
        assert not (await eng.duel_submit_move(s, d.id, other, d.turn_number, "f5")).ok
