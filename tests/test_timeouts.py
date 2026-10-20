from datetime import timedelta

from sqlalchemy import func, select

from database import transaction
from models import MatchTurn, User, utcnow
from services import match_engine as eng
from services import runtime_settings
from tests.helpers import A, B, CHAT, attack, live_match, register, respond, roles, snap_of


async def due(match_id, when):
    async with transaction() as s:
        return await eng.handle_due_match(s, match_id, now=when)


async def test_reversal_window_expiry_applies_normal_damage_once(db):
    snap = await live_match()
    att, dfn = roles(snap)
    await attack(snap.id, att, 1, "spear")
    later = utcnow() + timedelta(seconds=30)
    first = await due(snap.id, later)
    assert first.kind == "turn_resolved" and first.turn.auto and first.turn.damage_to_defender == 25
    assert await due(snap.id, later) is None                       # duplicate processing is a no-op
    now = await snap_of(snap.id)
    assert now.player(dfn).hp == 75
    async with transaction() as s:
        assert (await s.execute(select(func.count()).select_from(MatchTurn))).scalar_one() == 1


async def test_late_reversal_after_expiry_is_rejected(db):
    snap = await live_match()
    att, dfn = roles(snap)
    await attack(snap.id, att, 1, "spear")
    await due(snap.id, utcnow() + timedelta(seconds=30))
    assert not (await respond(snap.id, dfn, 1, True)).ok


async def test_not_due_yet_returns_none(db):
    snap = await live_match()
    assert await due(snap.id, utcnow()) is None


async def test_forfeit_policy_awards_exactly_one_win(db):
    snap = await live_match()
    att, dfn = roles(snap)
    async with transaction() as s:
        await runtime_settings.set_value(s, "timeout_policy", "forfeit")
    later = utcnow() + timedelta(seconds=120)
    first = await due(snap.id, later)
    assert first.kind == "forfeit" and first.turn.winner_id == dfn
    assert await due(snap.id, later) is None
    async with transaction() as s:
        users = (await s.execute(select(User).where(User.id.in_([A.id, B.id])))).scalars().all()
    assert sum(u.wins for u in users) == 1 and sum(u.losses for u in users) == 1
    assert sum(u.knockouts for u in users) == 0                    # a timeout win is not a knockout


async def test_pause_policy_pauses_then_resume_continues(db):
    snap = await live_match()
    att, dfn = roles(snap)
    first = await due(snap.id, utcnow() + timedelta(seconds=120))
    assert first.kind == "paused"
    assert not (await attack(snap.id, att, 1, "spear")).ok         # paused matches accept no moves
    async with transaction() as s:
        resumed = await eng.resume_match(s, snap.id, dfn)
    assert resumed.ok and (await snap_of(snap.id)).status == "active"
    assert (await attack(snap.id, att, 1, "spear")).ok


async def test_cancel_policy_cancels_without_stats(db):
    snap = await live_match()
    async with transaction() as s:
        await runtime_settings.set_value(s, "timeout_policy", "cancel")
    res = await due(snap.id, utcnow() + timedelta(seconds=120))
    assert res.kind == "timeout_cancelled"
    async with transaction() as s:
        assert all(u.total_matches == 0 for u in (await s.execute(select(User))).scalars())


async def test_open_challenge_expires_and_cannot_be_accepted(db):
    await register(A, B)
    async with transaction() as s:
        c = await eng.create_challenge(s, A, CHAT, "Ring")
    later = utcnow() + timedelta(seconds=120)
    async with transaction() as s:
        assert not (await eng.accept_challenge(s, c.match.id, B, CHAT, now=later)).ok
    assert (await due(c.match.id, later)).kind == "challenge_expired"
    assert (await snap_of(c.match.id)).status == "expired"
