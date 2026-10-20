"""Database-backed engine tests (SQLite in tests, PostgreSQL in production)."""
import asyncio

from sqlalchemy import func, select

from database import transaction
from models import Match, MatchTurn
from services import match_engine as eng
from services import runtime_settings, stats
from tests.helpers import (A, B, C, CHAT, FAIL, WIN, attack, live_match, play_to_knockout, register, respond,
                           roles, snap_of)


async def count_turns(match_id):
    async with transaction() as s:
        return (await s.execute(select(func.count()).select_from(MatchTurn).where(MatchTurn.match_id == match_id))).scalar_one()


async def test_both_players_start_at_100_hp(db):
    snap = await live_match()
    assert [p.hp for p in snap.players] == [100, 100]
    assert snap.status == "active" and snap.phase == "await_attack"


async def test_challenger_cannot_accept_own_challenge(db):
    await register(A)
    async with transaction() as s:
        c = await eng.create_challenge(s, A, CHAT, "Ring")
    async with transaction() as s:
        r = await eng.accept_challenge(s, c.match.id, A, CHAT)
    assert not r.ok


async def test_player_cannot_be_in_two_active_matches(db):
    snap = await live_match()
    async with transaction() as s:
        again = await eng.create_challenge(s, A, CHAT, "Ring")
    assert not again.ok
    await register(C)
    async with transaction() as s:
        other = await eng.create_challenge(s, C, CHAT, "Ring")
    async with transaction() as s:
        accept = await eng.accept_challenge(s, other.match.id, A, CHAT)   # A is already fighting
    assert not accept.ok


async def test_accept_must_come_from_the_original_group(db):
    await register(A, B)
    async with transaction() as s:
        c = await eng.create_challenge(s, A, CHAT, "Ring")
    async with transaction() as s:
        r = await eng.accept_challenge(s, c.match.id, B, CHAT - 1)
    assert not r.ok


async def test_match_waits_for_dm_start_then_goes_live(db):
    await register(A, dm=True)
    await register(B, dm=False)
    async with transaction() as s:
        c = await eng.create_challenge(s, A, CHAT, "Ring")
    async with transaction() as s:
        r = await eng.accept_challenge(s, c.match.id, B, CHAT)
    assert r.ok and r.match.status == "waiting_dm" and r.missing_dm == [B.id]
    async with transaction() as s:
        await eng.upsert_user(s, B, dm_started=True)
        started = await eng.try_start_waiting(s, B.id)
    assert started[0].kind == "started" and started[0].match.status == "active"


async def test_normal_hit_then_turn_passes_to_defender(db):
    snap = await live_match()
    att, dfn = roles(snap)
    res = await attack(snap.id, att, 1, "spear")
    assert res.kind == "attack_declared"
    res = await respond(snap.id, dfn, 1, False)
    assert res.ok and res.turn.damage_to_defender == 25
    now = await snap_of(snap.id)
    assert now.player(dfn).hp == 75 and now.player(att).hp == 100
    assert now.active_player_id == dfn and now.turn_number == 2


async def test_successful_reversal_reflects_once_and_turn_passes_to_the_reverser(db):
    snap = await live_match()
    att, dfn = roles(snap)
    await attack(snap.id, att, 1, "spear")
    res = await respond(snap.id, dfn, 1, True, rng=WIN)
    assert res.turn.reversed and res.turn.reversal_attempted and res.turn.damage_to_attacker == 25
    now = await snap_of(snap.id)
    assert now.player(att).hp == 75 and now.player(dfn).hp == 100
    assert now.player(dfn).reversals_left == 1
    assert now.active_player_id == dfn and now.turn_number == 2      # the reverser attacks next
    wrong = await attack(snap.id, att, now.turn_number, "f5")        # the reversed attacker must wait
    assert not wrong.ok


async def test_failed_reversal_deals_full_damage_costs_a_charge_and_turn_passes(db):
    snap = await live_match()
    att, dfn = roles(snap)
    await attack(snap.id, att, 1, "spear")
    res = await respond(snap.id, dfn, 1, True, rng=FAIL)
    assert res.ok and not res.turn.reversed and res.turn.reversal_attempted
    assert res.turn.damage_to_defender == 25 and res.turn.damage_to_attacker == 0
    now = await snap_of(snap.id)
    assert now.player(dfn).hp == 75 and now.player(att).hp == 100
    assert now.player(dfn).reversals_left == 1                       # the charge is spent anyway
    assert now.active_player_id == dfn and now.turn_number == 2


async def test_reversal_is_refused_once_both_charges_are_spent(db):
    snap = await live_match()
    att, dfn = roles(snap)
    await attack(snap.id, att, 1, "ddt")
    await respond(snap.id, dfn, 1, True, rng=FAIL)                   # dfn: 1 charge left
    await attack(snap.id, dfn, 2, "ddt")
    await respond(snap.id, att, 2, False)
    await attack(snap.id, att, 3, "f5")
    await respond(snap.id, dfn, 3, True, rng=FAIL)                   # dfn: 0 left
    await attack(snap.id, dfn, 4, "superkick")
    await respond(snap.id, att, 4, False)
    res = await attack(snap.id, att, 5, "spear")
    assert res.kind == "turn_resolved"                               # no response window: nothing to reverse with


async def test_reversal_is_not_a_playable_attack(db):
    snap = await live_match()
    att, _ = roles(snap)
    res = await attack(snap.id, att, 1, "reversal")
    assert not res.ok


async def test_duplicate_callbacks_resolve_a_turn_only_once(db):
    snap = await live_match()
    att, dfn = roles(snap)
    first = await attack(snap.id, att, 1, "f5")
    dup = await attack(snap.id, att, 1, "f5")
    assert first.ok and not dup.ok
    r1 = await respond(snap.id, dfn, 1, False)
    r2 = await respond(snap.id, dfn, 1, False)
    assert r1.ok and not r2.ok
    assert await count_turns(snap.id) == 1
    assert (await snap_of(snap.id)).player(dfn).hp == 70


async def test_simultaneous_responses_cannot_corrupt_hp(db):
    snap = await live_match()
    att, dfn = roles(snap)
    await attack(snap.id, att, 1, "spear")
    results = await asyncio.gather(respond(snap.id, dfn, 1, True), respond(snap.id, dfn, 1, False),
                                   return_exceptions=True)
    ok = [r for r in results if not isinstance(r, Exception) and r.ok]
    assert len(ok) == 1
    assert await count_turns(snap.id) == 1
    now = await snap_of(snap.id)
    assert sorted(p.hp for p in now.players) in ([75, 100],)       # exactly one 25-damage event


async def test_spectators_cannot_control_or_cancel_a_match(db):
    snap = await live_match()
    att, dfn = roles(snap)
    assert not (await attack(snap.id, C.id, 1, "spear")).ok
    await attack(snap.id, att, 1, "spear")
    assert not (await respond(snap.id, C.id, 1, True)).ok
    async with transaction() as s:
        assert not (await eng.resume_match(s, snap.id, C.id)).ok
        assert not (await eng.cancel_command(s, C.id)).ok
        assert not (await eng.respond_cancel(s, snap.id, C.id, True)).ok
    assert (await snap_of(snap.id)).status == "active"


async def test_attacker_cannot_answer_their_own_attack_and_wrong_player_cannot_attack(db):
    snap = await live_match()
    att, dfn = roles(snap)
    assert not (await attack(snap.id, dfn, 1, "ddt")).ok          # not their turn
    await attack(snap.id, att, 1, "ddt")
    assert not (await respond(snap.id, att, 1, False)).ok


async def test_knockout_completes_match_once_and_updates_stats(db):
    snap = await live_match()
    final = await play_to_knockout(snap.id)
    assert final.status == "completed" and final.winner_id is not None
    loser = final.other(final.winner_id)
    assert final.player(loser.user_id).hp == 0
    async with transaction() as s:
        wu = await stats.user_totals(s, final.winner_id)
        lu = await stats.user_totals(s, loser.user_id)
    assert (wu["wins"], wu["losses"], wu["total"], wu["knockouts"]) == (1, 0, 1, 1)
    assert (lu["wins"], lu["losses"], lu["total"]) == (0, 1, 1)
    async with transaction() as s:      # cannot act on a finished match
        assert not (await eng.submit_attack(s, snap.id, final.winner_id, 99, "spear")).ok


async def test_leaderboard_counts_only_completed_matches(db):
    snap = await live_match()
    async with transaction() as s:
        rows, _ = await stats.leaderboard_page(s, 0)
    assert rows == []                                              # match still active
    final = await play_to_knockout(snap.id)
    async with transaction() as s:
        rows, _ = await stats.leaderboard_page(s, 0)
    assert len(rows) == 2 and rows[0][1] == 1
    winner_name = final.player(final.winner_id).name
    assert rows[0][0] == winner_name


async def test_history_only_shows_own_completed_matches(db):
    snap = await live_match()
    await play_to_knockout(snap.id)
    async with transaction() as s:
        mine, _, _ = await stats.history_page(s, A.id, 0)
        spectator, _, _ = await stats.history_page(s, C.id, 0)
    assert len(mine) == 1 and mine[0]["turns"] >= 3
    assert spectator == []


async def test_cancel_rules(db):
    await register(A, B, C)
    async with transaction() as s:
        c = await eng.create_challenge(s, A, CHAT, "Ring")
    async with transaction() as s:
        assert not (await eng.cancel_command(s, C.id)).ok          # spectator
    async with transaction() as s:
        assert (await eng.cancel_command(s, A.id)).kind == "cancelled"
    snap = await live_match(seed=3)
    async with transaction() as s:
        req = await eng.cancel_command(s, A.id)                    # active match -> request only
    assert req.kind == "cancel_requested" and (await snap_of(snap.id)).status == "active"
    async with transaction() as s:
        done = await eng.respond_cancel(s, snap.id, B.id, True)
    assert done.kind == "cancelled" and (await snap_of(snap.id)).status == "cancelled"


async def test_maintenance_mode_blocks_new_challenges(db):
    await register(A)
    async with transaction() as s:
        await runtime_settings.set_value(s, "maintenance_mode", "on")
    async with transaction() as s:
        assert not (await eng.create_challenge(s, A, CHAT, "Ring")).ok
