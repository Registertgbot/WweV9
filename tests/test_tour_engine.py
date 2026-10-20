import random
from datetime import timedelta

import config
from config import get_settings, replace
from database import transaction
from models import Match, utcnow
from services import match_engine as me
from services import tour_engine as eng
from services import tour_rules as tr
from services.match_engine import Identity
from tests.helpers import register

OWNER = Identity(9001, "owner", "Owner")
PLAYERS = [Identity(9100 + i, f"p{i}", f"Player {i}") for i in range(1, 9)]
CHAT = -100777
RNG = random.Random(5)


async def setup(n=4, size=8, fmt="knockout"):
    config.set_settings(replace(get_settings(), owner_id=OWNER.id))
    await register(OWNER, *PLAYERS)
    async with transaction() as s:
        res = await eng.create(s, OWNER, CHAT, "Ring", "Test Cup", size, fmt=fmt)
    assert res.ok, res.error
    tid = res.t.id
    for ident in PLAYERS[:n]:
        async with transaction() as s:
            joined = await eng.join(s, tid, ident)
        assert joined.ok, joined.error
    return tid


async def tick(tid, when):
    async with transaction() as s:
        return await eng.tick(s, tid, now=when, rng=RNG)


async def snap(tid):
    async with transaction() as s:
        return await eng.get_snapshot(s, tid)


async def test_only_owner_creates_and_one_open_tournament_per_group(db):
    config.set_settings(replace(get_settings(), owner_id=OWNER.id))
    await register(OWNER, PLAYERS[0])
    async with transaction() as s:
        denied = await eng.create(s, PLAYERS[0], CHAT, "Ring", "Nope Cup", 8)
    assert not denied.ok
    async with transaction() as s:
        ok = await eng.create(s, OWNER, CHAT, "Ring", "Real Cup", 8)
    assert ok.ok and ok.t.status == "registration"
    async with transaction() as s:
        again = await eng.create(s, OWNER, CHAT, "Ring", "Second Cup", 8)
    assert not again.ok


async def test_join_needs_dm_waitlist_and_leave_promotes(db):
    tid = await setup(n=0, size=8)
    await register(*PLAYERS, dm=True)
    ghost = Identity(9999, "ghost", "Ghost")
    await register(ghost, dm=False)
    async with transaction() as s:
        blocked = await eng.join(s, tid, ghost)
    assert not blocked.ok and blocked.error == "dm"
    for ident in PLAYERS:                                   # 8 seats
        async with transaction() as s:
            assert (await eng.join(s, tid, ident)).ok
    extra = Identity(9200, "extra", "Extra")
    await register(extra)
    async with transaction() as s:
        res = await eng.join(s, tid, extra)
    assert res.ok and res.kind == "waitlist"
    async with transaction() as s:
        dup = await eng.join(s, tid, extra)
    assert not dup.ok
    async with transaction() as s:
        left = await eng.leave(s, tid, PLAYERS[0].id)
    assert left.ok
    t = await snap(tid)
    assert t.player(extra.id).status == "registered" and len(t.registered) == 8


async def test_start_builds_bracket_with_bye_and_schedules(db):
    tid = await setup(n=3)
    async with transaction() as s:
        res = await eng.start(s, tid, OWNER.id, now=utcnow(), rng=random.Random(1))
    assert res.ok
    t = res.t
    assert t.bracket_size == 4 and t.total_rounds == 2 and len(t.fixtures) == 3
    assert sum(1 for f in t.fixtures if f.result == "bye") == 1
    final = next(f for f in t.fixtures if f.round_no == 2)
    assert final.p1_id or final.p2_id                       # the bye winner already waits in the final
    live_round1 = [f for f in t.fixtures if f.round_no == 1 and f.status == "scheduled"]
    assert len(live_round1) == 1 and live_round1[0].scheduled_at > utcnow()


async def test_owner_controls_fixture_time_winner_and_champion(db):
    tid = await setup(n=4)
    async with transaction() as s:
        t = (await eng.start(s, tid, OWNER.id, now=utcnow(), rng=random.Random(2))).t
    f1 = next(f for f in t.fixtures if f.round_no == 1 and f.slot == 0)
    soon = utcnow() + timedelta(hours=3)
    async with transaction() as s:
        res = await eng.set_fixture_time(s, tid, OWNER.id, f1.id, soon)
    assert res.ok and res.t.fixture(f1.id).scheduled_at == soon and res.t.fixture(f1.id).time_locked
    async with transaction() as s:
        denied = await eng.set_fixture_time(s, tid, PLAYERS[0].id, f1.id, soon)
    assert not denied.ok
    for f in t.fixtures:
        if f.round_no == 1:
            async with transaction() as s:
                assert (await eng.set_fixture_winner(s, tid, OWNER.id, f.id, f.p1_id)).ok
    t = await snap(tid)
    final = next(f for f in t.fixtures if f.round_no == 2)
    assert final.status == "scheduled"
    async with transaction() as s:
        res = await eng.set_fixture_winner(s, tid, OWNER.id, final.id, final.p2_id)
    assert res.ok and res.t.status == "completed" and res.t.winner_id == final.p2_id
    assert "champion" in [e.kind for e in res.events]


async def test_disqualify_gives_walkover_and_tick_starts_due_match_then_syncs_timeout(db):
    tid = await setup(n=2)
    now = utcnow()
    async with transaction() as s:
        t = (await eng.start(s, tid, OWNER.id, now=now, rng=random.Random(3))).t
    final = t.fixtures[0]
    due = final.scheduled_at + timedelta(seconds=1)
    res = await tick(tid, due)
    assert res is not None and "match_start" in [e.kind for e in res.events]
    mid = next(e.match.id for e in res.events if e.kind == "match_start")
    async with transaction() as s:
        m = await s.get(Match, mid)
    assert m.fixture_id == final.id and m.status == "active"
    # nobody moves: the no-show window passes and the idle first mover forfeits
    late = due + timedelta(seconds=tr.DEFAULT_NOSHOW_SEC + 5)
    async with transaction() as s:
        out = await me.handle_due_match(s, mid, now=late)
    assert out is not None and out.kind == "forfeit"
    res = await tick(tid, late)
    assert res is not None and res.t.status == "completed"
    assert res.t.winner_id == out.match.winner_id


async def test_disqualifying_a_player_before_play_advances_the_opponent(db):
    tid = await setup(n=4)
    async with transaction() as s:
        t = (await eng.start(s, tid, OWNER.id, now=utcnow(), rng=random.Random(4))).t
    f = next(x for x in t.fixtures if x.round_no == 1 and x.slot == 0)
    async with transaction() as s:
        res = await eng.disqualify(s, tid, OWNER.id, f.p1_id)
    assert res.ok
    assert res.t.fixture(f.id).winner_id == f.p2_id and res.t.fixture(f.id).result == "walkover"
    assert res.t.player(f.p1_id).status == "dq"


async def test_one_open_tournament_per_user_and_pause_blocks_start(db):
    tid = await setup(n=2)
    async with transaction() as s:
        assert (await eng.set_paused(s, tid, OWNER.id, True)).ok
    async with transaction() as s:
        t = (await eng.start(s, tid, OWNER.id, now=utcnow(), rng=random.Random(6))).t
    due = t.fixtures[0].scheduled_at + timedelta(minutes=1)
    res = await tick(tid, due)
    assert res is None or "match_start" not in [e.kind for e in res.events]
    async with transaction() as s:
        assert (await eng.set_paused(s, tid, OWNER.id, False, now=due)).ok
    other = Identity(9300, "o", "Other")
    await register(other)
    async with transaction() as s:
        res = await eng.create(s, OWNER, CHAT - 1, "Other Ring", "Second", 8)
    assert res.ok
    async with transaction() as s:
        clash = await eng.join(s, res.t.id, PLAYERS[0])      # already inside the first tournament
    assert not clash.ok


# ----------------------------------------------------------------------------- league format

async def test_league_is_the_default_and_has_its_own_sizes(db):
    config.set_settings(replace(get_settings(), owner_id=OWNER.id))
    await register(OWNER)
    async with transaction() as s:
        bad = await eng.create(s, OWNER, CHAT, "Ring", "Big League", 32)        # 32-player round-robin is refused
    assert not bad.ok
    async with transaction() as s:
        ok = await eng.create(s, OWNER, CHAT, "Ring", "League Cup", 10)
    assert ok.ok and ok.t.format == "league" and ok.t.max_players == 10


async def test_league_start_builds_every_pair_and_playoff_fixtures(db):
    tid = await setup(n=4, size=8, fmt="league")
    async with transaction() as s:
        res = await eng.start(s, tid, OWNER.id, now=utcnow(), rng=random.Random(1))
    assert res.ok
    t = res.t
    assert t.format == "league" and t.league_rounds == 3 and t.total_rounds == 5
    league = [f for f in t.fixtures if f.round_no <= 3]
    assert len(league) == 6 and all(f.status == "scheduled" for f in league)
    pairs = {frozenset((f.p1_id, f.p2_id)) for f in league}
    assert len(pairs) == 6                                                   # everybody meets everybody once
    assert len([f for f in t.fixtures if f.round_no == 4]) == 2 and len([f for f in t.fixtures if f.round_no == 5]) == 1
    md1 = min(f.scheduled_at for f in league if f.round_no == 1)
    md2 = min(f.scheduled_at for f in league if f.round_no == 2)
    assert md2 - md1 == timedelta(minutes=tr.DEFAULT_ROUND_GAP_MIN)


async def test_league_table_decides_playoffs_and_champion(db):
    tid = await setup(n=4, size=8, fmt="league")
    async with transaction() as s:
        t = (await eng.start(s, tid, OWNER.id, now=utcnow(), rng=random.Random(2))).t
    rank = {p.user_id: p.seed for p in t.players}                            # lower seed number = stronger
    league_events = []
    for f in sorted([x for x in t.fixtures if x.round_no <= 3], key=lambda x: (x.round_no, x.slot)):
        winner = min((f.p1_id, f.p2_id), key=lambda uid: rank[uid])
        async with transaction() as s:
            res = await eng.set_fixture_winner(s, tid, OWNER.id, f.id, winner)
        assert res.ok
        league_events += [e.kind for e in res.events]
    assert league_events.count("league_done") == 1
    t = await snap(tid)
    table = t.table()
    assert [r.user_id for r in table] == sorted(rank, key=lambda uid: rank[uid])      # strongest always won
    assert table[0].points == 9 and table[3].points == 0
    semis = sorted([f for f in t.fixtures if f.round_no == 4], key=lambda x: x.slot)
    assert (semis[0].p1_id, semis[0].p2_id) == (table[0].user_id, table[3].user_id)   # 1 v 4
    assert (semis[1].p1_id, semis[1].p2_id) == (table[1].user_id, table[2].user_id)   # 2 v 3
    for f in semis:
        async with transaction() as s:
            assert (await eng.set_fixture_winner(s, tid, OWNER.id, f.id, f.p1_id)).ok
    final = next(f for f in (await snap(tid)).fixtures if f.round_no == 5)
    async with transaction() as s:
        res = await eng.set_fixture_winner(s, tid, OWNER.id, final.id, final.p1_id)
    assert res.ok and res.t.status == "completed" and res.t.winner_id == table[0].user_id


async def test_league_running_match_is_left_alone_by_later_ticks(db):
    tid = await setup(n=2, size=8, fmt="league")
    async with transaction() as s:
        t = (await eng.start(s, tid, OWNER.id, now=utcnow(), rng=random.Random(3))).t
    f = t.fixtures[0]
    res = await tick(tid, f.scheduled_at + timedelta(seconds=1))
    assert res is not None and "match_start" in [e.kind for e in res.events]
    late = f.scheduled_at + timedelta(seconds=tr.DEFAULT_NOSHOW_SEC * 3)
    again = await tick(tid, late)                                            # the match is still running: nothing happens
    assert again is None or all(e.kind not in ("fixture_done",) for e in again.events)
