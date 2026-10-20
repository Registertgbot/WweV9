import random
from datetime import timedelta

import config
from database import transaction
from models import utcnow
from services import tag_engine as eng
from services.match_engine import Identity, upsert_user

CHAT = -100888
IDS = [Identity(3000 + i, f"t{i}", f"Tagger{i}") for i in range(1, 6)]


async def lobby(fill=True):
    async with transaction() as s:
        for i in IDS:
            await upsert_user(s, i, dm_started=True)
    async with transaction() as s:
        res = await eng.create_tag(s, IDS[0], CHAT, "Ring")
    assert res.ok, res.error
    mid = res.match.id
    if fill:
        for ident, team in ((IDS[1], 1), (IDS[2], 2), (IDS[3], 2)):
            async with transaction() as s:
                assert (await eng.join(s, mid, ident, CHAT, team)).ok
    return mid


async def started(seed=1):
    mid = await lobby()
    async with transaction() as s:
        res = await eng.start(s, mid, IDS[0].id, rng=random.Random(seed))
    assert res.ok, res.error
    return res.match


async def test_teams_are_limited_to_two_and_players_can_switch(db):
    mid = await lobby(fill=False)
    async with transaction() as s:
        assert (await eng.join(s, mid, IDS[1], CHAT, 1)).ok
    async with transaction() as s:
        assert not (await eng.join(s, mid, IDS[2], CHAT, 1)).ok        # Lions are full
    async with transaction() as s:
        assert (await eng.join(s, mid, IDS[1], CHAT, 2)).kind == "switched"


async def test_start_needs_two_vs_two(db):
    mid = await lobby(fill=False)
    async with transaction() as s:
        assert not (await eng.start(s, mid, IDS[0].id)).ok


async def test_start_puts_one_wrestler_per_team_in_the_ring(db):
    snap = await started()
    assert snap.status == "active" and snap.phase == "await_move"
    for team in (1, 2):
        assert snap.ring(team) is not None and snap.apron(team) is not None
    assert snap.active_player_id == snap.ring(snap.active_team).user_id


async def test_attack_then_reversal(db):
    snap = await started()
    a = snap.active_player_id
    d = snap.ring(3 - snap.active_team).user_id
    async with transaction() as s:
        assert (await eng.submit_move(s, snap.id, a, snap.turn_number, "spear")).kind == "attack_declared"
    async with transaction() as s:
        out = await eng.submit_response(s, snap.id, d, snap.turn_number, use_reversal=True)
    assert out.ok and next(e for e in out.events if e.kind == "turn").reversed
    assert out.match.active_team == 3 - snap.active_team            # turn passed to the other team


async def test_tag_swaps_wrestlers_and_cannot_be_repeated(db):
    snap = await started()
    team, a = snap.active_team, snap.active_player_id
    partner = snap.apron(team).user_id
    async with transaction() as s:
        out = await eng.submit_move(s, snap.id, a, snap.turn_number, eng.TAG)
    assert out.ok and out.match.ring(team).user_id == partner
    # the other team attacks and misses nothing; when the turn returns, a second tag is refused
    cur = out.match
    async with transaction() as s:
        await eng.submit_move(s, cur.id, cur.active_player_id, cur.turn_number, "f5")
    async with transaction() as s:
        cur2 = await eng.get_snapshot(s, cur.id)
    async with transaction() as s:
        out2 = await eng.submit_response(s, cur2.id, cur2.pending_target_id, cur2.turn_number, use_reversal=False)
    assert out2.ok
    again = out2.match
    assert again.active_team == team and again.tag_locked(team)
    async with transaction() as s:
        assert not (await eng.submit_move(s, again.id, again.active_player_id, again.turn_number, eng.TAG)).ok


async def test_only_active_player_can_act(db):
    snap = await started()
    other = snap.ring(3 - snap.active_team).user_id
    async with transaction() as s:
        assert not (await eng.submit_move(s, snap.id, other, snap.turn_number, "f5")).ok


async def test_idle_players_end_up_eliminated_and_a_team_wins(db):
    snap = await started()
    now = utcnow()
    for _ in range(300):
        async with transaction() as s:
            cur = await eng.get_snapshot(s, snap.id)
        if cur.status == "completed":
            break
        now += timedelta(seconds=config.get_settings().rumble_turn_timeout + 120)
        async with transaction() as s:
            await eng.handle_due(s, snap.id, now=now)
    assert cur.status == "completed" and cur.winner_team in (1, 2)


async def test_three_vs_three_lobby_and_pick_which_partner_to_tag(db):
    async with transaction() as s:
        for i in IDS:
            await upsert_user(s, i, dm_started=True)
    extra = [Identity(3100 + i, f"x{i}", f"Extra{i}") for i in range(1, 3)]
    async with transaction() as s:
        for e in extra:
            await upsert_user(s, e, dm_started=True)
    async with transaction() as s:
        mid = (await eng.create_tag(s, IDS[0], CHAT, "Ring", team_size=3)).match.id
    for ident, team in ((IDS[1], 1), (IDS[2], 1), (IDS[3], 2), (IDS[4], 2), (extra[0], 2)):
        async with transaction() as s:
            assert (await eng.join(s, mid, ident, CHAT, team)).ok
    async with transaction() as s:
        assert not (await eng.join(s, mid, extra[1], CHAT, 2)).ok          # Tigers full at 3
    async with transaction() as s:
        snap = (await eng.start(s, mid, IDS[0].id, rng=random.Random(2))).match
    team, a = snap.active_team, snap.active_player_id
    assert len(snap.aprons(team)) == 2
    async with transaction() as s:
        assert not (await eng.submit_move(s, snap.id, a, snap.turn_number, eng.TAG)).ok   # must pick one
    choice = snap.aprons(team)[1].user_id
    async with transaction() as s:
        out = await eng.submit_move(s, snap.id, a, snap.turn_number, eng.tag_move(choice))
    assert out.ok and out.match.ring(team).user_id == choice


async def test_uneven_teams_start_and_the_smaller_team_gets_a_bonus_reversal(db):
    async with transaction() as s:
        for i in IDS:
            await upsert_user(s, i, dm_started=True)
    async with transaction() as s:
        mid = (await eng.create_tag(s, IDS[0], CHAT, "Ring", team_size=3)).match.id
    for ident, team in ((IDS[1], 1), (IDS[2], 2), (IDS[3], 2), (IDS[4], 2)):
        async with transaction() as s:
            assert (await eng.join(s, mid, ident, CHAT, team)).ok
    async with transaction() as s:
        snap = (await eng.start(s, mid, IDS[0].id)).match            # 2 Lions vs 3 Tigers
    assert all(p.reversals_left == 3 for p in snap.team(1))
    assert all(p.reversals_left == 2 for p in snap.team(2))
