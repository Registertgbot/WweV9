"""Database-backed solo tournament engine (owner-run knockout bracket with IST fixtures).

Same contract as the other engines: every public function takes an open ``AsyncSession`` inside a
transaction and returns a ``Result``; no Telegram calls happen here. The tournament row is locked with
``SELECT ... FOR UPDATE`` for every change, so button taps and the background loop never race.
Tournament fixtures are normal 1v1 matches (``match_engine.create_tournament_match``) that the bot
starts by itself when the scheduled time arrives.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from models import (Match, MatchStatus as MS, MatchTurn, Tournament, TournamentFixture, TournamentPlayer,
                    TournamentReschedule, User, utcnow)
from services import match_engine as me
from services import rules
from services import tour_rules as tr
from services.match_engine import Identity, MatchSnap

EXTEND_MINUTES = 15          # registration is pushed back by this much when too few players joined


# --------------------------------------------------------------------------- data

@dataclass
class PSnap:
    user_id: int
    name: str
    status: str
    seed: int | None
    out_round: int | None
    wins: int
    losses: int
    joined_at: datetime


@dataclass
class FSnap:
    id: int
    round_no: int
    slot: int
    p1_id: int | None
    p2_id: int | None
    status: str
    winner_id: int | None
    result: str | None
    scheduled_at: datetime | None
    time_locked: bool
    match_id: int | None
    margin: int | None = None


@dataclass
class RSnap:
    id: int
    fixture_id: int
    requester_id: int
    opponent_id: int
    old_at: datetime
    new_at: datetime
    status: str
    expires_at: datetime


@dataclass
class TSnap:
    id: int
    group_chat_id: int
    group_title: str | None
    owner_id: int
    title: str
    status: str
    max_players: int
    bracket_size: int | None
    total_rounds: int | None
    reg_close_at: datetime | None
    start_at: datetime | None
    round_gap_min: int
    match_gap_min: int
    noshow_sec: int
    paused: bool
    winner_id: int | None
    registration_message_id: int | None
    bracket_message_id: int | None
    created_at: datetime
    completed_at: datetime | None
    players: list[PSnap]
    fixtures: list[FSnap]
    format: str = tr.LEAGUE
    league_rounds: int = 0
    reschedules: list[RSnap] = field(default_factory=list)

    def player(self, user_id: int | None) -> PSnap | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    def name(self, user_id: int | None) -> str:
        p = self.player(user_id)
        return p.name if p else "TBD"

    @property
    def registered(self) -> list[PSnap]:
        return [p for p in self.players if p.status == tr.P_REGISTERED]

    @property
    def waitlist(self) -> list[PSnap]:
        return [p for p in self.players if p.status == tr.P_WAITLIST]

    def fixture(self, fixture_id: int) -> FSnap | None:
        return next((f for f in self.fixtures if f.id == fixture_id), None)

    def round_fixtures(self, round_no: int) -> list[FSnap]:
        return [f for f in self.fixtures if f.round_no == round_no]

    def round_label(self, round_no: int) -> str:
        total = self.total_rounds or round_no
        if self.format == tr.LEAGUE:
            return tr.round_label(round_no, self.league_rounds, total)
        return tr.round_name(round_no, total)

    def round_short(self, round_no: int) -> str:
        total = self.total_rounds or round_no
        if self.format == tr.LEAGUE:
            return tr.round_short(round_no, self.league_rounds, total)
        return self.round_label(round_no)[:3]

    def my_fixtures(self, user_id: int) -> list[FSnap]:
        """A fighter's unfinished fixtures: the live one first, then by time (unknown opponents last)."""
        rank = {tr.LIVE: 0, tr.SCHEDULED: 1, tr.PENDING: 2}
        mine = [f for f in self.fixtures if f.status != tr.DONE and user_id in (f.p1_id, f.p2_id)]
        return sorted(mine, key=lambda f: (rank.get(f.status, 3), f.scheduled_at.timestamp() if f.scheduled_at else 1e18,
                                           f.round_no, f.slot))

    def pending_reschedule(self, fixture_id: int) -> RSnap | None:
        return next((r for r in self.reschedules if r.fixture_id == fixture_id and r.status == tr.RQ_PENDING), None)

    def reschedule(self, request_id: int) -> RSnap | None:
        return next((r for r in self.reschedules if r.id == request_id), None)

    def accepted_moves(self, fixture_id: int) -> int:
        return sum(1 for r in self.reschedules if r.fixture_id == fixture_id and r.status == tr.RQ_ACCEPTED)

    def table(self) -> list[tr.Row]:
        """League points table (empty for knockout tournaments)."""
        entries = [(p.user_id, p.seed or 999) for p in self.players if p.status != tr.P_WAITLIST]
        results = [(f.winner_id, f.p2_id if f.winner_id == f.p1_id else f.p1_id, f.margin or 0)
                   for f in self.fixtures
                   if f.round_no <= self.league_rounds and f.status == tr.DONE and f.winner_id]
        return tr.standings(entries, results)


@dataclass
class Event:
    kind: str                       # started|reminder|match_start|blocked|fixture_done|round_done|champion|
    fixture_id: int | None = None   # rescheduled|rescheduled_all|reg_extended|match_cancelled|cancelled|paused|resumed|
                                    # reschedule_request (user_id = opponent, note = request id)|
                                    # reschedule_answer (note = accepted|declined|expired|withdrawn; user_id = who to tell)
    user_id: int | None = None
    match: MatchSnap | None = None
    note: str = ""


@dataclass
class Result:
    ok: bool
    error: str | None = None
    t: TSnap | None = None
    events: list[Event] = field(default_factory=list)
    kind: str = ""

    @classmethod
    def fail(cls, message: str) -> "Result":
        return cls(ok=False, error=message)


# ------------------------------------------------------------------------- helpers

async def _lock(session: AsyncSession, tid: int, skip_locked: bool = False) -> Tournament | None:
    stmt = (select(Tournament).where(Tournament.id == tid)
            .with_for_update(skip_locked=skip_locked).execution_options(populate_existing=True))
    return (await session.execute(stmt)).scalar_one_or_none()


async def snapshot(session: AsyncSession, t: Tournament) -> TSnap:
    players = []
    for p in t.players:
        user = await session.get(User, p.user_id)
        players.append(PSnap(p.user_id, user.display_name if user else "Unknown Superstar", p.status, p.seed,
                             p.out_round, p.wins, p.losses, p.joined_at))
    fixtures = [FSnap(f.id, f.round_no, f.slot, f.p1_id, f.p2_id, f.status, f.winner_id, f.result,
                      f.scheduled_at, f.time_locked, f.match_id, f.margin) for f in t.fixtures]
    requests = [RSnap(r.id, r.fixture_id, r.requester_id, r.opponent_id, r.old_at, r.new_at, r.status, r.expires_at)
                for r in t.reschedules]
    return TSnap(t.id, t.group_chat_id, t.group_title, t.owner_id, t.title, t.status, t.max_players,
                 t.bracket_size, t.total_rounds, t.reg_close_at, t.start_at, t.round_gap_min, t.match_gap_min,
                 t.noshow_sec, t.paused, t.winner_id, t.registration_message_id, t.bracket_message_id,
                 t.created_at, t.completed_at, players, fixtures, t.format, t.league_rounds, requests)


def _owner_ok(t: Tournament, actor_id: int) -> bool:
    return bool(actor_id) and actor_id in (t.owner_id, get_settings().owner_id)


def _player(t: Tournament, user_id: int | None) -> TournamentPlayer | None:
    return next((p for p in t.players if p.user_id == user_id), None)


def _fx(t: Tournament, round_no: int, slot: int) -> TournamentFixture | None:
    return next((f for f in t.fixtures if f.round_no == round_no and f.slot == slot), None)


def _alive(t: Tournament, user_id: int | None) -> bool:
    p = _player(t, user_id)
    return p is not None and p.status == tr.P_REGISTERED


def _deactivate_all(t: Tournament) -> None:
    for p in t.players:
        p.is_active = False


async def _open_for(session: AsyncSession, tid: int, actor_id: int, *, skip_locked: bool = False
                    ) -> tuple[Tournament | None, Result | None]:
    t = await _lock(session, tid, skip_locked)
    if t is None or t.status not in tr.OPEN_STATES:
        return None, Result.fail("This tournament is no longer running.")
    if not _owner_ok(t, actor_id):
        return None, Result.fail("🔒 Only the tournament owner can do that.")
    return t, None


def _promote_waitlist(t: Tournament) -> None:
    free = t.max_players - sum(1 for p in t.players if p.status == tr.P_REGISTERED)
    for p in sorted((p for p in t.players if p.status == tr.P_WAITLIST), key=lambda x: (x.joined_at, x.id)):
        if free <= 0:
            break
        p.status = tr.P_REGISTERED
        free -= 1


# ------------------------------------------------------------------- create / register

async def create(session: AsyncSession, ident: Identity, chat_id: int, chat_title: str | None, name: str,
                 max_players: int, now: datetime | None = None, fmt: str = tr.LEAGUE) -> Result:
    now = now or utcnow()
    owner_id = get_settings().owner_id
    if not owner_id:
        return Result.fail("⚙️ OWNER_ID is not configured, so nobody can create tournaments yet.")
    if ident.id != owner_id:
        return Result.fail("🔒 Only the bot owner can create tournaments.")
    if fmt not in (tr.LEAGUE, tr.KNOCKOUT) or max_players not in tr.sizes_for(fmt):
        return Result.fail("Pick one of: " + ", ".join(str(n) for n in tr.sizes_for(fmt)) + " players.")
    title = tr.clean_name(name)
    if title is None:
        return Result.fail("Please send a name with at least 2 characters.")
    await me.upsert_user(session, ident)
    t = Tournament(group_chat_id=chat_id, group_title=(chat_title or "")[:256] or None, owner_id=ident.id,
                   title=title, status=tr.REGISTRATION, max_players=max_players, created_at=now, format=fmt, league_rounds=0,
                   round_gap_min=tr.DEFAULT_ROUND_GAP_MIN, match_gap_min=tr.DEFAULT_MATCH_GAP_MIN,
                   noshow_sec=tr.DEFAULT_NOSHOW_SEC, players=[], fixtures=[], reschedules=[])
    try:
        async with session.begin_nested():
            session.add(t)
            await session.flush()
    except IntegrityError:
        return Result.fail("⚠️ A tournament is already open in this group.")
    return Result(True, t=await snapshot(session, t), kind="created")


async def join(session: AsyncSession, tid: int, ident: Identity, now: datetime | None = None) -> Result:
    now = now or utcnow()
    t = await _lock(session, tid)
    if t is None or t.status != tr.REGISTRATION:
        return Result.fail("🔒 Registration is closed.")
    user = await me.upsert_user(session, ident)
    if not user.dm_started:
        return Result.fail("dm")
    existing = _player(t, ident.id)
    if existing is not None:
        where = "on the waiting list" if existing.status == tr.P_WAITLIST else "registered"
        return Result.fail(f"✅ You're already {where}.")
    status = tr.P_REGISTERED if sum(1 for p in t.players if p.status == tr.P_REGISTERED) < t.max_players \
        else tr.P_WAITLIST
    try:
        async with session.begin_nested():
            t.players.append(TournamentPlayer(user_id=ident.id, status=status, joined_at=now))
            await session.flush()
    except IntegrityError:
        return Result.fail("⚠️ You're already in another tournament. Finish it first.")
    return Result(True, t=await snapshot(session, t), kind=status)


async def leave(session: AsyncSession, tid: int, user_id: int) -> Result:
    t = await _lock(session, tid)
    if t is None or t.status not in tr.OPEN_STATES:
        return Result.fail("This tournament is no longer running.")
    p = _player(t, user_id)
    if p is None:
        return Result.fail("You aren't registered in this tournament.")
    if t.status != tr.REGISTRATION:
        return Result.fail("🚫 The bracket is locked. Ask the owner if you need to withdraw.")
    t.players.remove(p)
    await session.flush()
    _promote_waitlist(t)
    await session.flush()
    return Result(True, t=await snapshot(session, t), kind="left")


# ------------------------------------------------------------------------- owner: setup

async def set_max_players(session: AsyncSession, tid: int, actor_id: int, size: int) -> Result:
    t, err = await _open_for(session, tid, actor_id)
    if err:
        return err
    if t.status != tr.REGISTRATION:
        return Result.fail("The bracket is locked.")
    if size not in tr.sizes_for(t.format):
        return Result.fail("Pick one of: " + ", ".join(str(n) for n in tr.sizes_for(t.format)) + " players.")
    if size < sum(1 for p in t.players if p.status == tr.P_REGISTERED):
        return Result.fail("More players are already registered than that.")
    t.max_players = size
    _promote_waitlist(t)
    await session.flush()
    return Result(True, t=await snapshot(session, t))


async def set_reg_close(session: AsyncSession, tid: int, actor_id: int, when: datetime | None,
                        now: datetime | None = None) -> Result:
    now = now or utcnow()
    t, err = await _open_for(session, tid, actor_id)
    if err:
        return err
    if t.status != tr.REGISTRATION:
        return Result.fail("Registration is already closed.")
    if when is not None and when <= now:
        return Result.fail("⏰ That time has already passed (IST). Send a future time.")
    t.reg_close_at = when
    await session.flush()
    return Result(True, t=await snapshot(session, t))


async def set_start_time(session: AsyncSession, tid: int, actor_id: int, when: datetime,
                         now: datetime | None = None) -> Result:
    """First-round start. Before the bracket exists it is stored; afterwards all unlocked fixtures are re-timed."""
    now = now or utcnow()
    t, err = await _open_for(session, tid, actor_id)
    if err:
        return err
    if when <= now:
        return Result.fail("⏰ That time has already passed (IST). Send a future time.")
    t.start_at = when
    events: list[Event] = []
    if t.status == tr.ACTIVE:
        _apply_timetable(t, now)
        events.append(Event("rescheduled_all"))
    await session.flush()
    return Result(True, t=await snapshot(session, t), events=events)


async def kick(session: AsyncSession, tid: int, actor_id: int, user_id: int, now: datetime | None = None) -> Result:
    now = now or utcnow()
    t, err = await _open_for(session, tid, actor_id)
    if err:
        return err
    p = _player(t, user_id)
    if p is None:
        return Result.fail("That player isn't in this tournament.")
    events: list[Event] = []
    if t.status == tr.REGISTRATION or p.status == tr.P_WAITLIST:
        t.players.remove(p)
        await session.flush()
        _promote_waitlist(t)
        events.append(Event("kicked", user_id=user_id))
    else:
        await _disqualify(session, t, p, now, events)
    await session.flush()
    return Result(True, t=await snapshot(session, t), events=events)


# ------------------------------------------------------------------------- bracket

def _timetable_base(t: Tournament, now: datetime) -> datetime:
    earliest = now + timedelta(seconds=tr.REST_SECONDS)
    return t.start_at if t.start_at and t.start_at >= earliest else earliest


async def _build(session: AsyncSession, t: Tournament, now: datetime, rng: random.Random | None,
                 events: list[Event]) -> None:
    regs = [p for p in t.players if p.status == tr.P_REGISTERED]
    if t.format == tr.LEAGUE:
        await _build_league(session, t, regs, now, rng)
        return
    size = tr.bracket_size(len(regs))
    rounds = tr.total_rounds(size)
    order = tr.draw([p.user_id for p in regs], rng)
    for seed, uid in enumerate(order, start=1):
        _player(t, uid).seed = seed
    t.bracket_size, t.total_rounds = size, rounds
    t.start_at = _timetable_base(t, now)
    for r in range(1, rounds + 1):
        for slot in range(tr.fixture_count(size, r)):
            t.fixtures.append(TournamentFixture(
                round_no=r, slot=slot, status=tr.PENDING,
                scheduled_at=tr.fixture_time(t.start_at, r, slot, t.round_gap_min, t.match_gap_min)))
    await session.flush()
    for slot, (a, b) in enumerate(tr.first_round_pairs(order, size)):
        f = _fx(t, 1, slot)
        f.p1_id, f.p2_id = a, b
        if a is None or b is None:                                   # bye: the other player walks into round 2
            _set_winner(t, f, a or b, "bye", now, events)
        else:
            _ready_check(f, now)
    await session.flush()


async def _build_league(session: AsyncSession, t: Tournament, regs: list[TournamentPlayer], now: datetime,
                        rng: random.Random | None) -> None:
    """Round-robin matchdays, then (still empty) playoff fixtures: Semifinals + Final, or just a Final."""
    order = tr.draw([p.user_id for p in regs], rng)
    for seed, uid in enumerate(order, start=1):
        _player(t, uid).seed = seed
    n = len(order)
    rounds = tr.league_rounds_count(n)
    t.bracket_size, t.league_rounds, t.total_rounds = n, rounds, rounds + tr.playoff_rounds(n)
    t.start_at = _timetable_base(t, now)
    for r, pairs in enumerate(tr.round_robin(order), start=1):
        for slot, (a, b) in enumerate(pairs):
            t.fixtures.append(TournamentFixture(
                round_no=r, slot=slot, p1_id=a, p2_id=b, status=tr.PENDING,
                scheduled_at=tr.fixture_time(t.start_at, r, slot, t.round_gap_min, t.match_gap_min)))
    extra = tr.playoff_rounds(n)
    for k in range(extra):
        r = rounds + 1 + k
        for slot in range(2 if (extra == 2 and k == 0) else 1):
            t.fixtures.append(TournamentFixture(
                round_no=r, slot=slot, status=tr.PENDING,
                scheduled_at=tr.fixture_time(t.start_at, r, slot, t.round_gap_min, t.match_gap_min)))
    await session.flush()
    for f in t.fixtures:
        if f.round_no <= rounds:
            _ready_check(f, now)
    await session.flush()


async def start(session: AsyncSession, tid: int, actor_id: int, now: datetime | None = None,
                rng: random.Random | None = None) -> Result:
    now = now or utcnow()
    t, err = await _open_for(session, tid, actor_id)
    if err:
        return err
    return await _start_locked(session, t, now, rng)


async def _start_locked(session: AsyncSession, t: Tournament, now: datetime, rng: random.Random | None) -> Result:
    if t.status != tr.REGISTRATION:
        return Result.fail("The bracket is already locked.")
    if sum(1 for p in t.players if p.status == tr.P_REGISTERED) < tr.MIN_PLAYERS:
        return Result.fail(f"👥 At least {tr.MIN_PLAYERS} players are needed to start.")
    events: list[Event] = []
    t.status = tr.ACTIVE
    t.started_at = now
    t.reg_close_at = None
    await _build(session, t, now, rng, events)
    await _settle(session, t, now, events)
    events.insert(0, Event("started"))
    await session.flush()
    return Result(True, t=await snapshot(session, t), events=events, kind="started")


async def redraw(session: AsyncSession, tid: int, actor_id: int, now: datetime | None = None,
                 rng: random.Random | None = None) -> Result:
    now = now or utcnow()
    t, err = await _open_for(session, tid, actor_id)
    if err:
        return err
    if t.status != tr.ACTIVE:
        return Result.fail("The bracket doesn't exist yet.")
    if any(f.status == tr.LIVE or (f.status == tr.DONE and f.result != "bye") for f in t.fixtures):
        return Result.fail("🚫 Matches have already been played, so the draw can't be redone.")
    t.fixtures.clear()
    await session.flush()
    for p in t.players:
        if p.status == tr.P_REGISTERED:
            p.out_round = None
    events: list[Event] = []
    await _build(session, t, now, rng, events)
    await _settle(session, t, now, events)
    events.insert(0, Event("redrawn"))
    await session.flush()
    return Result(True, t=await snapshot(session, t), events=events)


# ------------------------------------------------------------- fixture state machine

def _apply_timetable(t: Tournament, now: datetime) -> None:
    """Re-time every fixture that has not started and was not hand-edited."""
    base = t.start_at or _timetable_base(t, now)
    earliest = now + timedelta(seconds=tr.REST_SECONDS)
    for f in t.fixtures:
        if f.status in (tr.LIVE, tr.DONE) or f.time_locked:
            continue
        new = tr.fixture_time(base, f.round_no, f.slot, t.round_gap_min, t.match_gap_min)
        if f.status == tr.SCHEDULED and new < earliest:
            new = earliest
        if f.scheduled_at != new:
            f.scheduled_at = new
            f.reminded = False


def _ready_check(f: TournamentFixture, now: datetime) -> None:
    """A fixture whose two players are known becomes SCHEDULED (never earlier than a short rest)."""
    if f.status != tr.PENDING or not (f.p1_id and f.p2_id):
        return
    f.status = tr.SCHEDULED
    earliest = now + timedelta(seconds=tr.REST_SECONDS)
    if f.scheduled_at is None or f.scheduled_at < earliest:
        f.scheduled_at = earliest
    f.reminded = False


def _table(t: Tournament) -> list[tr.Row]:
    entries = [(p.user_id, p.seed or 999) for p in t.players if p.status != tr.P_WAITLIST]
    results = [(f.winner_id, f.p2_id if f.winner_id == f.p1_id else f.p1_id, f.margin or 0)
               for f in t.fixtures if f.round_no <= t.league_rounds and f.status == tr.DONE and f.winner_id]
    return tr.standings(entries, results)


def _crown(t: Tournament, winner_id: int | None, now: datetime, events: list[Event]) -> None:
    winner = _player(t, winner_id)
    if winner:
        winner.status = tr.P_CHAMPION
    t.winner_id, t.status, t.completed_at = winner_id, tr.COMPLETED, now
    _deactivate_all(t)
    events.append(Event("champion", user_id=winner_id))


def _finish_league(t: Tournament, now: datetime, events: list[Event]) -> None:
    """All matchdays are done: the top of the table goes to the playoffs, everybody else is out."""
    events.append(Event("league_done"))
    table = _table(t)
    eligible = [r for r in table if getattr(_player(t, r.user_id), 'status', None) == tr.P_REGISTERED]
    k = tr.playoff_size(t.bracket_size or len(eligible))
    if k == 0 or len(eligible) < 2:                          # no playoffs: the table decides
        _crown(t, eligible[0].user_id if eligible else None, now, events)
        return
    qualified = [r.user_id for r in eligible[:k]]
    for r in table:
        p = _player(t, r.user_id)
        if p is not None and p.status == tr.P_REGISTERED and r.user_id not in qualified:
            p.status, p.out_round, p.is_active = tr.P_OUT, t.league_rounds, False
    padded = qualified + [None] * (k - len(qualified))
    pairs = [(padded[0], padded[3]), (padded[1], padded[2])] if k == 4 else [(padded[0], padded[1])]
    for slot, (a, b) in enumerate(pairs):
        f = _fx(t, t.league_rounds + 1, slot)
        f.p1_id, f.p2_id = a, b
        if a is None or b is None:                           # fewer than 4 eligible: the seed advances unopposed
            _set_winner(t, f, a or b, "bye", now, events)
        else:
            _ready_check(f, now)


def _set_winner(t: Tournament, f: TournamentFixture, winner_id: int | None, result: str, now: datetime,
                events: list[Event], margin: int | None = None) -> None:
    f.winner_id, f.result, f.status, f.finished_at = winner_id, result, tr.DONE, now
    f.blocked_since = None
    winner = _player(t, winner_id)
    loser_id = f.p2_id if winner_id == f.p1_id else f.p1_id
    loser = _player(t, loser_id)
    if t.format == tr.LEAGUE and f.round_no <= t.league_rounds:           # league fixture: only the table changes
        f.margin = margin if margin is not None else tr.WALKOVER_MARGIN
        if winner:
            winner.wins += 1
        if loser:
            loser.losses += 1
        events.append(Event("fixture_done", fixture_id=f.id, user_id=winner_id, note=result))
        if all(x.status == tr.DONE for x in t.fixtures if x.round_no == f.round_no):
            events.append(Event("round_done", note=str(f.round_no)))
        if all(x.status == tr.DONE for x in t.fixtures if x.round_no <= t.league_rounds):
            _finish_league(t, now, events)
        return
    if result != "bye":
        if winner:
            winner.wins += 1
        if loser:
            loser.losses += 1
        events.append(Event("fixture_done", fixture_id=f.id, user_id=winner_id, note=result))
    if loser is not None and result != "bye":
        if loser.status == tr.P_REGISTERED:
            loser.status = tr.P_OUT
        loser.out_round = f.round_no
        loser.is_active = False
    if f.round_no == (t.total_rounds or 0):                          # the final decides the champion
        events.append(Event("round_done", note=str(f.round_no)))
        _crown(t, winner_id, now, events)
        return
    nf = _fx(t, f.round_no + 1, f.slot // 2)
    if nf is not None:
        if f.slot % 2 == 0:
            nf.p1_id = winner_id
        else:
            nf.p2_id = winner_id
        _ready_check(nf, now)
    if all(x.status == tr.DONE for x in t.fixtures if x.round_no == f.round_no) and result != "bye":
        events.append(Event("round_done", note=str(f.round_no)))


async def _settle(session: AsyncSession, t: Tournament, now: datetime, events: list[Event]) -> None:
    """Walkovers for fixtures whose player was disqualified/removed; repeats because winners propagate."""
    for _ in range(len(t.fixtures) + 2):
        changed = False
        for f in sorted(t.fixtures, key=lambda x: (x.round_no, x.slot)):
            if f.status != tr.SCHEDULED or not (f.p1_id and f.p2_id):
                continue
            a, b = _alive(t, f.p1_id), _alive(t, f.p2_id)
            if a and b:
                continue
            side = tr.resolve_unavailable(a, b)
            winner = f.p1_id if side == 1 else f.p2_id if side == 2 else min(
                (f.p1_id, f.p2_id), key=lambda uid: (_player(t, uid).seed or 99))
            _set_winner(t, f, winner, "walkover", now, events)
            changed = True
            if t.status != tr.ACTIVE:
                break
        if not changed or t.status != tr.ACTIVE:
            break
    await session.flush()


async def _end_live_match(session: AsyncSession, f: TournamentFixture, now: datetime, events: list[Event]) -> None:
    if f.status == tr.LIVE and f.match_id:
        snap = await me.cancel_tournament_match(session, f.match_id, now)
        if snap is not None:
            events.append(Event("match_cancelled", fixture_id=f.id, match=snap))
        f.status = tr.SCHEDULED


async def _disqualify(session: AsyncSession, t: Tournament, p: TournamentPlayer, now: datetime,
                      events: list[Event]) -> None:
    p.status = tr.P_DQ
    p.is_active = False
    events.append(Event("disqualified", user_id=p.user_id))
    for f in t.fixtures:
        if f.status == tr.LIVE and p.user_id in (f.p1_id, f.p2_id):
            await _end_live_match(session, f, now, events)
    await _settle(session, t, now, events)


async def disqualify(session: AsyncSession, tid: int, actor_id: int, user_id: int, now: datetime | None = None) -> Result:
    now = now or utcnow()
    t, err = await _open_for(session, tid, actor_id)
    if err:
        return err
    p = _player(t, user_id)
    if p is None or p.status not in (tr.P_REGISTERED, tr.P_WAITLIST):
        return Result.fail("That player can't be disqualified.")
    events: list[Event] = []
    if p.status == tr.P_WAITLIST or t.status == tr.REGISTRATION:
        t.players.remove(p)
        await session.flush()
        _promote_waitlist(t)
        events.append(Event("kicked", user_id=user_id))
    else:
        await _disqualify(session, t, p, now, events)
    await session.flush()
    return Result(True, t=await snapshot(session, t), events=events)


async def set_fixture_time(session: AsyncSession, tid: int, actor_id: int, fixture_id: int, when: datetime,
                           now: datetime | None = None) -> Result:
    now = now or utcnow()
    t, err = await _open_for(session, tid, actor_id)
    if err:
        return err
    f = next((x for x in t.fixtures if x.id == fixture_id), None)
    if f is None or f.status in (tr.LIVE, tr.DONE):
        return Result.fail("That fixture can't be re-timed any more.")
    if when <= now:
        return Result.fail("⏰ That time has already passed (IST). Send a future time.")
    f.scheduled_at, f.time_locked, f.reminded = when, True, False
    await session.flush()
    return Result(True, t=await snapshot(session, t), events=[Event("rescheduled", fixture_id=f.id)])


async def set_fixture_winner(session: AsyncSession, tid: int, actor_id: int, fixture_id: int, winner_id: int,
                             now: datetime | None = None) -> Result:
    now = now or utcnow()
    t, err = await _open_for(session, tid, actor_id)
    if err:
        return err
    f = next((x for x in t.fixtures if x.id == fixture_id), None)
    if f is None or f.status == tr.DONE or not (f.p1_id and f.p2_id) or winner_id not in (f.p1_id, f.p2_id):
        return Result.fail("That result can't be set.")
    events: list[Event] = []
    await _end_live_match(session, f, now, events)
    _set_winner(t, f, winner_id, "owner", now, events)
    if t.status == tr.ACTIVE:
        await _settle(session, t, now, events)
    await session.flush()
    return Result(True, t=await snapshot(session, t), events=events)


async def set_gaps(session: AsyncSession, tid: int, actor_id: int, *, round_gap: int | None = None,
                   match_gap: int | None = None, noshow: int | None = None, now: datetime | None = None) -> Result:
    now = now or utcnow()
    t, err = await _open_for(session, tid, actor_id)
    if err:
        return err
    if round_gap is not None:
        if round_gap not in tr.ROUND_GAP_CHOICES:
            return Result.fail("Invalid round gap.")
        t.round_gap_min = round_gap
    if match_gap is not None:
        if match_gap not in tr.MATCH_GAP_CHOICES:
            return Result.fail("Invalid match gap.")
        t.match_gap_min = match_gap
    if noshow is not None:
        if noshow not in tr.NOSHOW_CHOICES:
            return Result.fail("Invalid no-show window.")
        t.noshow_sec = noshow
    events: list[Event] = []
    if t.status == tr.ACTIVE and (round_gap is not None or match_gap is not None):
        _apply_timetable(t, now)
        events.append(Event("rescheduled_all"))
    await session.flush()
    return Result(True, t=await snapshot(session, t), events=events)


async def set_paused(session: AsyncSession, tid: int, actor_id: int, paused: bool, now: datetime | None = None) -> Result:
    now = now or utcnow()
    t, err = await _open_for(session, tid, actor_id)
    if err:
        return err
    events: list[Event] = []
    if t.paused != paused:
        t.paused = paused
        if not paused and t.status == tr.ACTIVE:                     # nothing may start "in the past" after a pause
            earliest = now + timedelta(seconds=tr.REST_SECONDS)
            for f in t.fixtures:
                if f.status == tr.SCHEDULED and f.scheduled_at and f.scheduled_at < earliest:
                    f.scheduled_at, f.reminded = earliest, False
        events.append(Event("paused" if paused else "resumed"))
    await session.flush()
    return Result(True, t=await snapshot(session, t), events=events)


async def cancel(session: AsyncSession, tid: int, actor_id: int, now: datetime | None = None) -> Result:
    now = now or utcnow()
    t, err = await _open_for(session, tid, actor_id)
    if err:
        return err
    events: list[Event] = []
    for f in t.fixtures:
        await _end_live_match(session, f, now, events)
    t.status, t.completed_at = tr.CANCELLED, now
    _deactivate_all(t)
    events.append(Event("cancelled"))
    await session.flush()
    return Result(True, t=await snapshot(session, t), events=events, kind="cancelled")


# ------------------------------------------------------------------- reschedule requests

def _clash_times(t: Tournament, f: TournamentFixture, user_id: int) -> list[datetime | None]:
    """Times of a fighter's other unfinished fixtures (a new time may not land on top of them)."""
    return [x.scheduled_at for x in t.fixtures
            if x.id != f.id and x.status in (tr.PENDING, tr.SCHEDULED, tr.LIVE) and user_id in (x.p1_id, x.p2_id)]


def _accepted_moves(t: Tournament, fixture_id: int) -> int:
    return sum(1 for r in t.reschedules if r.fixture_id == fixture_id and r.status == tr.RQ_ACCEPTED)


async def request_reschedule(session: AsyncSession, tid: int, user_id: int, fixture_id: int, when: datetime,
                             now: datetime | None = None) -> Result:
    """A fighter asks to move his scheduled match; the opponent (or the owner) has to accept."""
    now = now or utcnow()
    t = await _lock(session, tid)
    if t is None or t.status != tr.ACTIVE:
        return Result.fail("This tournament is not running.")
    f = next((x for x in t.fixtures if x.id == fixture_id), None)
    if f is None or user_id not in (f.p1_id, f.p2_id):
        return Result.fail("That match isn't yours.")
    if not (f.p1_id and f.p2_id):
        return Result.fail("⏳ Your opponent isn't known yet.")
    if f.status != tr.SCHEDULED or f.scheduled_at is None:
        return Result.fail("⚔️ This match can't be moved any more.")
    opponent = f.p2_id if user_id == f.p1_id else f.p1_id
    if not (_alive(t, user_id) and _alive(t, opponent)):
        return Result.fail("⚔️ This match can't be moved any more.")
    if any(r.fixture_id == f.id and r.status == tr.RQ_PENDING for r in t.reschedules):
        return Result.fail("⏳ A reschedule request is already waiting for an answer.")
    err = tr.check_reschedule_time(now, f.scheduled_at, when, _accepted_moves(t, f.id))
    if err:
        return Result.fail(err)
    for uid in (user_id, opponent):
        if tr.clashes(when, _clash_times(t, f, uid)):
            who = "You have" if uid == user_id else "Your opponent has"
            return Result.fail(f"⚔️ {who} another match around that time. Pick a different time.")
    request = TournamentReschedule(
        tournament_id=t.id, fixture_id=f.id, requester_id=user_id, opponent_id=opponent, old_at=f.scheduled_at,
        new_at=when, status=tr.RQ_PENDING, created_at=now,
        expires_at=min(now + timedelta(minutes=tr.RESCHEDULE_TTL_MIN), f.scheduled_at - timedelta(minutes=2)))
    try:
        async with session.begin_nested():
            t.reschedules.append(request)
            await session.flush()
    except IntegrityError:
        if request in t.reschedules:
            t.reschedules.remove(request)
        return Result.fail("⏳ A reschedule request is already waiting for an answer.")
    event = Event("reschedule_request", fixture_id=f.id, user_id=opponent, note=str(request.id))
    return Result(True, t=await snapshot(session, t), events=[event], kind="requested")


async def answer_reschedule(session: AsyncSession, request_id: int, actor_id: int, accept: bool,
                            now: datetime | None = None) -> Result:
    """The opponent (or the owner) accepts or declines. Accepting moves the fixture and locks its time."""
    now = now or utcnow()
    row = await session.get(TournamentReschedule, request_id)
    if row is None:
        return Result.fail("This request no longer exists.")
    t = await _lock(session, row.tournament_id)
    if t is None or t.status != tr.ACTIVE:
        return Result.fail("This tournament is no longer running.")
    request = next((r for r in t.reschedules if r.id == request_id), None)
    if request is None or request.status != tr.RQ_PENDING:
        return Result.fail("This request is already closed.")
    by_owner = _owner_ok(t, actor_id)
    if actor_id != request.opponent_id and not by_owner:
        return Result.fail("Only the other fighter (or the owner) can answer this.")
    f = next((x for x in t.fixtures if x.id == request.fixture_id), None)
    if f is None or f.status != tr.SCHEDULED or f.scheduled_at != request.old_at:
        return Result.fail("⌛ That match changed in the meantime, so the request is void.")   # the clock closes it

    def close(status: str) -> None:
        request.status, request.decided_at, request.decided_by = status, now, actor_id

    if not accept:
        close(tr.RQ_DECLINED)
        await session.flush()
        event = Event("reschedule_answer", fixture_id=f.id, user_id=request.requester_id, note=tr.RQ_DECLINED)
        return Result(True, t=await snapshot(session, t), events=[event], kind="declined")

    err = tr.check_reschedule_time(now, f.scheduled_at, request.new_at, 0)
    if err:                                                       # answered too late: the proposal is stale now
        close(tr.RQ_EXPIRED)
        await session.flush()
        event = Event("reschedule_answer", fixture_id=f.id, user_id=request.requester_id, note=tr.RQ_EXPIRED)
        return Result(False, error="⌛ Too late — the proposed time is too close now.", t=await snapshot(session, t),
                      events=[event])
    for uid in (request.requester_id, request.opponent_id):
        if tr.clashes(request.new_at, _clash_times(t, f, uid)):
            return Result.fail("⚔️ One of you has another match around that time now. Decline and propose another.")
    f.scheduled_at, f.time_locked, f.reminded = request.new_at, True, False
    close(tr.RQ_ACCEPTED)
    await session.flush()
    events = [Event("rescheduled", fixture_id=f.id),
              Event("reschedule_answer", fixture_id=f.id, user_id=request.requester_id, note=tr.RQ_ACCEPTED)]
    return Result(True, t=await snapshot(session, t), events=events, kind="accepted")


async def withdraw_reschedule(session: AsyncSession, request_id: int, user_id: int,
                              now: datetime | None = None) -> Result:
    now = now or utcnow()
    row = await session.get(TournamentReschedule, request_id)
    if row is None:
        return Result.fail("This request no longer exists.")
    t = await _lock(session, row.tournament_id)
    request = next((r for r in t.reschedules if r.id == request_id), None) if t else None
    if request is None or request.status != tr.RQ_PENDING:
        return Result.fail("This request is already closed.")
    if request.requester_id != user_id:
        return Result.fail("Only the fighter who asked can withdraw it.")
    request.status, request.decided_at, request.decided_by = tr.RQ_WITHDRAWN, now, user_id
    await session.flush()
    event = Event("reschedule_answer", fixture_id=request.fixture_id, user_id=request.opponent_id, note=tr.RQ_WITHDRAWN)
    return Result(True, t=await snapshot(session, t), events=[event], kind="withdrawn")


async def fixture_card_stats(session: AsyncSession, match_id: int | None, winner_id: int | None) -> dict:
    """Numbers for the winner card (HP left, damage, finishers, turns); empty when there was no ring action."""
    if not match_id or not winner_id:
        return {}
    m = (await session.execute(select(Match).where(Match.id == match_id)
                               .execution_options(populate_existing=True))).scalar_one_or_none()
    if m is None or m.status != MS.COMPLETED:
        return {}
    win = next((p for p in m.players if p.user_id == winner_id), None)
    lose = next((p for p in m.players if p.user_id != winner_id), None)
    if win is None or lose is None:
        return {}
    turns = (await session.execute(select(MatchTurn).where(MatchTurn.match_id == m.id))).scalars().all()
    summary = rules.summarize_turns(turns, [p.user_id for p in m.players])
    return {"winner_hp": win.hp, "loser_hp": lose.hp, "turns": summary.total_turns,
            "finishers": summary.finishers.get(winner_id, 0), "damage": summary.damage_dealt.get(winner_id, 0)}


# ----------------------------------------------------------------------- bookkeeping

async def set_message_id(session: AsyncSession, tid: int, field_name: str, message_id: int) -> None:
    assert field_name in ("registration_message_id", "bracket_message_id")
    t = await session.get(Tournament, tid)
    if t is not None:
        setattr(t, field_name, message_id)


async def open_ids(session: AsyncSession) -> list[int]:
    stmt = select(Tournament.id).where(Tournament.status.in_(tr.OPEN_STATES)).order_by(Tournament.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_snapshot(session: AsyncSession, tid: int) -> TSnap | None:
    t = (await session.execute(select(Tournament).where(Tournament.id == tid)
                               .execution_options(populate_existing=True))).scalar_one_or_none()
    return await snapshot(session, t) if t else None


async def open_snapshot_for_chat(session: AsyncSession, chat_id: int) -> TSnap | None:
    t = (await session.execute(select(Tournament).where(Tournament.group_chat_id == chat_id,
                                                        Tournament.status.in_(tr.OPEN_STATES))
                               .execution_options(populate_existing=True))).scalars().first()
    return await snapshot(session, t) if t else None


# --------------------------------------------------------------------------- the clock

def _margin(m: Match) -> int | None:
    """Winner HP minus loser HP of a finished match (league tiebreak)."""
    win = next((p for p in m.players if p.user_id == m.winner_id), None)
    lose = next((p for p in m.players if p.user_id != m.winner_id), None)
    return win.hp - lose.hp if win and lose else None


async def tick(session: AsyncSession, tid: int, now: datetime | None = None,
               rng: random.Random | None = None) -> Result | None:
    """Advance one tournament: auto-start, sync finished matches, reminders, start due fixtures.

    Idempotent and restart-safe: everything is derived from database state, so a crash or restart
    simply continues where it stopped (finished matches are picked up by the sync step).
    """
    now = now or utcnow()
    t = await _lock(session, tid, skip_locked=True)
    if t is None or t.status not in tr.OPEN_STATES:
        return None
    events: list[Event] = []

    if t.status == tr.REGISTRATION:
        if t.reg_close_at and t.reg_close_at <= now:
            if sum(1 for p in t.players if p.status == tr.P_REGISTERED) >= tr.MIN_PLAYERS:
                res = await _start_locked(session, t, now, rng)
                return res if res.ok else None
            t.reg_close_at = now + timedelta(minutes=EXTEND_MINUTES)
            events.append(Event("reg_extended"))
            await session.flush()
        return Result(True, t=await snapshot(session, t), events=events) if events else None

    # 1) finished matches -> fixture results
    for f in sorted(t.fixtures, key=lambda x: (x.round_no, x.slot)):
        if f.status != tr.LIVE or not f.match_id or t.status != tr.ACTIVE:
            continue
        m = (await session.execute(select(Match).where(Match.id == f.match_id)
                                   .execution_options(populate_existing=True))).scalar_one_or_none()
        if m is None or m.status in (MS.CANCELLED, MS.EXPIRED):
            f.status, f.match_id, f.scheduled_at, f.reminded = tr.SCHEDULED, None, now + timedelta(
                seconds=tr.REST_SECONDS), False
            events.append(Event("match_reset", fixture_id=f.id))
        elif m.status == MS.COMPLETED and m.winner_id in (f.p1_id, f.p2_id):
            _set_winner(t, f, m.winner_id, "timeout" if m.end_reason == "timeout" else "knockout", now, events,
                        margin=_margin(m))
    await _settle(session, t, now, events)

    # 1b) reschedule requests die when unanswered, or when their match started / was re-timed / was removed
    for request in t.reschedules:
        if request.status != tr.RQ_PENDING:
            continue
        fx = next((x for x in t.fixtures if x.id == request.fixture_id), None)
        if fx is None or fx.status != tr.SCHEDULED or fx.scheduled_at != request.old_at or request.expires_at <= now:
            request.status, request.decided_at = tr.RQ_EXPIRED, now
            events.append(Event("reschedule_answer", fixture_id=request.fixture_id, user_id=request.requester_id,
                                note=tr.RQ_EXPIRED))

    if t.status == tr.ACTIVE and not t.paused:
        # 2) reminders
        lead = timedelta(minutes=tr.REMINDER_MINUTES)
        for f in t.fixtures:
            if f.status == tr.SCHEDULED and not f.reminded and f.scheduled_at and f.scheduled_at - lead <= now:
                f.reminded = True
                events.append(Event("reminder", fixture_id=f.id))
        # 3) start what is due
        for f in sorted(t.fixtures, key=lambda x: (x.scheduled_at or now, x.round_no, x.slot)):
            if f.status != tr.SCHEDULED or not f.scheduled_at or f.scheduled_at > now:
                continue
            res = await me.create_tournament_match(session, t.group_chat_id, t.group_title, f.p1_id, f.p2_id,
                                                   f.id, t.noshow_sec, now=now, rng=rng)
            if res.ok:
                f.status, f.match_id, f.started_at, f.blocked_since = tr.LIVE, res.match.id, now, None
                events.append(Event("match_start", fixture_id=f.id, match=res.match))
                continue
            blamed = int(res.error.split(":")[1]) if res.error and ":" in res.error else None
            if blamed and any(x is not f and x.status == tr.LIVE and blamed in (x.p1_id, x.p2_id) for x in t.fixtures):
                f.blocked_since = None                           # still in an earlier match here: just wait, no penalty
                continue
            if f.blocked_since is None:
                f.blocked_since = now
                events.append(Event("blocked", fixture_id=f.id, user_id=blamed, note=res.error or ""))
            elif (now - f.blocked_since).total_seconds() >= t.noshow_sec:
                other = (f.p2_id if blamed == f.p1_id else f.p1_id) if blamed else None
                if other:                                            # the blocked fighter loses by no-show
                    _set_winner(t, f, other, "walkover", now, events)
        await _settle(session, t, now, events)

    await session.flush()
    return Result(True, t=await snapshot(session, t), events=events) if events else None


# ----------------------------------------------------------- history, stats, backup

async def history(session: AsyncSession, chat_id: int, limit: int = 8) -> list[dict]:
    stmt = (select(Tournament).where(Tournament.group_chat_id == chat_id, Tournament.status == tr.COMPLETED)
            .order_by(Tournament.completed_at.desc()).limit(limit))
    rows = []
    for t in (await session.execute(stmt)).scalars().all():
        winner = await session.get(User, t.winner_id) if t.winner_id else None
        rows.append({"title": t.title, "winner": winner.display_name if winner else "—",
                     "players": sum(1 for p in t.players if p.status != tr.P_WAITLIST),
                     "when": t.completed_at})
    return rows


async def user_stats(session: AsyncSession, user_id: int) -> dict:
    stmt = select(TournamentPlayer).where(TournamentPlayer.user_id == user_id,
                                          TournamentPlayer.status != tr.P_WAITLIST)
    rows = (await session.execute(stmt)).scalars().all()
    titles = sum(1 for p in rows if p.status == tr.P_CHAMPION)
    wins, losses = sum(p.wins for p in rows), sum(p.losses for p in rows)
    finals = 0
    recent = []
    for p in sorted(rows, key=lambda x: -x.id)[:5]:
        t = await session.get(Tournament, p.tournament_id)
        if t is None:
            continue
        if p.status == tr.P_CHAMPION:
            label = "Champion"
        elif t.status in tr.OPEN_STATES:
            label = "In progress"
        elif p.status == tr.P_DQ:
            label = "Disqualified"
        elif t.league_rounds and p.out_round and p.out_round <= t.league_rounds:
            label = "League stage"
        else:
            label = tr.placement_label(p.out_round, t.total_rounds or 1)
        recent.append((t.title, label))
    for p in rows:
        t = await session.get(Tournament, p.tournament_id)
        if t and t.total_rounds and (p.status == tr.P_CHAMPION or p.out_round == t.total_rounds):
            finals += 1
    return {"played": len(rows), "titles": titles, "finals": finals, "wins": wins, "losses": losses,
            "recent": recent}


async def leaderboard(session: AsyncSession, limit: int = 10) -> list[tuple[str, int, int, int]]:
    """(name, titles, tournaments played, match wins) ordered by titles then wins."""
    rows = (await session.execute(select(TournamentPlayer).where(TournamentPlayer.status != tr.P_WAITLIST))
            ).scalars().all()
    agg: dict[int, list[int]] = {}
    for p in rows:
        a = agg.setdefault(p.user_id, [0, 0, 0])
        a[0] += 1 if p.status == tr.P_CHAMPION else 0
        a[1] += 1
        a[2] += p.wins
    out = []
    for uid, (titles, played, wins) in agg.items():
        user = await session.get(User, uid)
        out.append((user.display_name if user else "Unknown", titles, played, wins))
    out.sort(key=lambda r: (-r[1], -r[3], -r[2]))
    return out[:limit]


def export_dict(t: TSnap) -> dict:
    """Plain-data backup of a tournament (sent to the owner as a JSON file)."""
    return {
        "id": t.id, "title": t.title, "status": t.status, "format": t.format, "league_rounds": t.league_rounds,
        "table": [{"rank": i, "name": t.name(r.user_id), "played": r.played, "wins": r.wins, "points": r.points,
                   "margin": r.margin} for i, r in enumerate(t.table(), start=1)],
        "group_chat_id": t.group_chat_id,
        "max_players": t.max_players, "bracket_size": t.bracket_size, "total_rounds": t.total_rounds,
        "start_at_ist": tr.fmt_ist(t.start_at), "round_gap_min": t.round_gap_min, "match_gap_min": t.match_gap_min,
        "noshow_sec": t.noshow_sec, "paused": t.paused, "winner": t.name(t.winner_id) if t.winner_id else None,
        "players": [{"user_id": p.user_id, "name": p.name, "status": p.status, "seed": p.seed,
                     "out_round": p.out_round, "wins": p.wins, "losses": p.losses} for p in t.players],
        "fixtures": [{"round": f.round_no, "slot": f.slot, "round_name": t.round_label(f.round_no),
                      "p1": t.name(f.p1_id), "p2": t.name(f.p2_id), "winner": t.name(f.winner_id) if f.winner_id else None,
                      "status": f.status, "result": f.result, "time_ist": tr.fmt_ist(f.scheduled_at)}
                     for f in t.fixtures],
    }
