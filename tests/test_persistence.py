from datetime import timedelta

import database
from database import transaction
from models import utcnow
from services import match_engine as eng
from tests.helpers import WIN, attack, live_match, respond, roles, snap_of


async def test_active_match_survives_restart(db_url):
    snap = await live_match()
    att, dfn = roles(snap)
    await attack(snap.id, att, 1, "finisher")
    await respond(snap.id, dfn, 1, False)
    await attack(snap.id, dfn, 2, "f5")
    before = await snap_of(snap.id)                                # reversal window is open

    await database.dispose_engine()                                # simulate a redeploy
    database.init_engine(db_url)

    after = await snap_of(snap.id)
    assert after.status == before.status == "active" and after.phase == "await_reversal"
    assert after.turn_number == before.turn_number
    assert after.deadline_at == before.deadline_at
    for p in before.players:
        q = after.player(p.user_id)
        assert (q.hp, q.finishers_left, q.reversals_left, q.last_attack) == (p.hp, p.finishers_left, p.reversals_left, p.last_attack)
    assert after.player(att).finishers_left == 0                   # limits still enforced after restart
    res = await respond(snap.id, att, 2, True, rng=WIN)
    assert res.ok and res.turn.reversed


async def test_recover_deadlines_grants_grace_after_downtime(db):
    snap = await live_match()
    far_future = utcnow() + timedelta(hours=2)
    async with transaction() as s:
        assert await eng.due_match_ids(s, now=far_future) == [snap.id]
        assert await eng.recover_deadlines(s, now=far_future, grace_seconds=30) == 1
    async with transaction() as s:
        assert await eng.due_match_ids(s, now=far_future) == []
        assert await eng.due_match_ids(s, now=far_future + timedelta(seconds=31)) == [snap.id]
