"""Money in the Bank engine (database-backed, no Telegram calls; same contract as rumble/tag engines).

Flow
  lobby -> qualifier rounds (parallel 1v1 duels until <= 4 wrestlers are left) -> ladder match
  -> winner receives the briefcase (7 days) -> later: cash-in offer after a 1v1 match in the group.

Fairness (see services.mitb_rules): random seeding and first move, no repeated byes, identical stats for
every duel, time-limited duels/ladder decided on points, cash-in floor HP and the defender strikes first.

Lock order is always duel -> event (never the other way round) so concurrent duel results cannot deadlock.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from models import (Briefcase, CashinOffer, MitbDuel, MitbDuelPlayer, MitbEvent, MitbPlayer, RPhase, User, utcnow)
from services import mitb_rules as mr
from services import rules
from services.activity import is_busy
from services.match_engine import Identity, upsert_user
from services.rules import Move, RuleError

_rng = random.Random()
DM_MSG = "📩 Open the bot privately and press Start first — moves are chosen in DM."
CLIMB = "climb"


class MS:
    LOBBY, QUALIFIER, LADDER, COMPLETED, CANCELLED = "lobby", "qualifier", "ladder", "completed", "cancelled"
    OPEN = (LOBBY, QUALIFIER, LADDER)


# --------------------------------------------------------------------------- data

@dataclass
class DPlayerSnap:
    user_id: int
    name: str
    hp: int
    finishers_left: int
    reversals_left: int
    last_attack: str | None
    damage_dealt: int
    prompt_message_id: int | None
    dm_started: bool


@dataclass
class DuelSnap:
    id: int
    event_id: int | None
    group_chat_id: int
    kind: str
    mode: str
    round_no: int
    briefcase_id: int | None
    status: str
    phase: str | None
    turn_number: int
    active_user_id: int | None
    pending_move: str | None
    deadline_at: datetime | None
    cap_at: datetime | None
    winner_id: int | None
    end_reason: str | None
    players: list[DPlayerSnap]

    def player(self, user_id: int | None) -> DPlayerSnap | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    def other(self, user_id: int) -> DPlayerSnap | None:
        return next((p for p in self.players if p.user_id != user_id), None)


@dataclass
class MPlayerSnap:
    user_id: int
    name: str
    seed: int
    status: str
    had_bye: bool
    out_round: int | None
    rung: int
    hp: int
    finishers_left: int
    reversals_left: int
    last_attack: str | None
    stun: int
    damage_dealt: int
    ladder_order: int
    prompt_message_id: int | None
    dm_started: bool


@dataclass
class EventSnap:
    id: int
    group_chat_id: int
    group_title: str | None
    host_id: int
    mode: str
    capacity: int
    status: str
    round_no: int
    phase: str | None
    turn_number: int
    ladder_turns: int
    active_player_id: int | None
    pending_move: str | None
    pending_target_id: int | None
    deadline_at: datetime | None
    winner_id: int | None
    end_reason: str | None
    lobby_message_id: int | None
    board_message_id: int | None
    players: list[MPlayerSnap]
    duels: list[DuelSnap] = field(default_factory=list)      # duels of the current qualifier round

    def player(self, user_id: int | None) -> MPlayerSnap | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    @property
    def alive(self) -> list[MPlayerSnap]:
        return sorted((p for p in self.players if p.status == "alive"), key=lambda p: p.seed)

    @property
    def ladder(self) -> list[MPlayerSnap]:
        return sorted((p for p in self.players if p.status == "ladder"), key=lambda p: p.ladder_order)

    @property
    def total_rounds(self) -> int:
        return mr.rounds_needed(len(self.players))


@dataclass
class OfferSnap:
    id: int
    briefcase_id: int
    holder_id: int
    holder_name: str
    group_chat_id: int
    target_user_id: int
    target_name: str
    target_hp: int
    status: str
    expires_at: datetime
    message_id: int | None


@dataclass
class BriefcaseSnap:
    id: int
    user_id: int
    name: str
    group_chat_id: int
    status: str
    expires_at: datetime


@dataclass
class MEvent:
    kind: str
    user_id: int | None = None
    other_id: int | None = None
    move: str | None = None
    reversed: bool = False
    damage: int = 0
    reflected: int = 0
    hp_a: int = 0
    hp_d: int = 0
    reason: str = ""
    auto: bool = False
    rung: int = 0
    duel_id: int | None = None
    round_no: int = 0
    pairs: list = field(default_factory=list)       # round_start: [(duel_id, user_a, user_b)]
    bye: int | None = None
    flag: bool = False


@dataclass
class MResult:
    ok: bool
    kind: str = ""
    error: str | None = None
    event: EventSnap | None = None
    duel: DuelSnap | None = None
    offer: OfferSnap | None = None
    briefcase: BriefcaseSnap | None = None
    events: list[MEvent] = field(default_factory=list)

    @classmethod
    def fail(cls, message: str) -> "MResult":
        return cls(ok=False, error=message)


# ------------------------------------------------------------------------ DB helpers

async def _lock_event(session: AsyncSession, event_id: int, skip_locked: bool = False) -> MitbEvent | None:
    stmt = (select(MitbEvent).where(MitbEvent.id == event_id)
            .with_for_update(skip_locked=skip_locked).execution_options(populate_existing=True))
    return (await session.execute(stmt)).scalar_one_or_none()


async def _lock_duel(session: AsyncSession, duel_id: int, skip_locked: bool = False) -> MitbDuel | None:
    stmt = (select(MitbDuel).where(MitbDuel.id == duel_id)
            .with_for_update(skip_locked=skip_locked).execution_options(populate_existing=True))
    return (await session.execute(stmt)).scalar_one_or_none()


async def _add(session: AsyncSession, obj) -> None:
    session.add(obj)
    await session.flush()


async def _count_active_duels(session: AsyncSession, event_id: int) -> int:
    stmt = select(func.count()).select_from(MitbDuel).where(MitbDuel.event_id == event_id,
                                                           MitbDuel.status == "active")
    return int((await session.execute(stmt)).scalar_one())


async def _expire_active_briefcase(session: AsyncSession, user_id: int) -> None:
    await session.execute(update(Briefcase).where(Briefcase.user_id == user_id, Briefcase.status == "active")
                          .values(status="expired"))


async def _get_briefcase(session: AsyncSession, briefcase_id: int) -> Briefcase | None:
    return await session.get(Briefcase, briefcase_id, populate_existing=True)


async def _name(session: AsyncSession, user_id: int | None) -> str:
    user = await session.get(User, user_id) if user_id else None
    return user.display_name if user else "Unknown Superstar"


async def _dm_ok(session: AsyncSession, user_id: int) -> bool:
    user = await session.get(User, user_id)
    return bool(user and user.dm_started)


def _p(m: MitbEvent, user_id: int | None) -> MitbPlayer | None:
    return next((p for p in m.players if p.user_id == user_id), None)


def _dp(d: MitbDuel, user_id: int | None) -> MitbDuelPlayer | None:
    return next((p for p in d.players if p.user_id == user_id), None)


def _dother(d: MitbDuel, user_id: int) -> MitbDuelPlayer | None:
    return next((p for p in d.players if p.user_id != user_id), None)


def _ladder(m: MitbEvent) -> list[MitbPlayer]:
    return sorted((p for p in m.players if p.status == "ladder"), key=lambda p: p.ladder_order)


async def duel_snapshot(session: AsyncSession, d: MitbDuel) -> DuelSnap:
    players = []
    for p in d.players:
        user = await session.get(User, p.user_id)
        players.append(DPlayerSnap(
            user_id=p.user_id, name=user.display_name if user else "Unknown Superstar", hp=p.hp,
            finishers_left=rules.finishers_left(p), reversals_left=rules.reversals_left(p),
            last_attack=p.last_attack, damage_dealt=p.damage_dealt, prompt_message_id=p.prompt_message_id,
            dm_started=bool(user and user.dm_started)))
    return DuelSnap(
        id=d.id, event_id=d.event_id, group_chat_id=d.group_chat_id, kind=d.kind, mode=d.mode, round_no=d.round_no,
        briefcase_id=d.briefcase_id, status=d.status, phase=d.phase, turn_number=d.turn_number,
        active_user_id=d.active_user_id, pending_move=d.pending_move, deadline_at=d.deadline_at, cap_at=d.cap_at,
        winner_id=d.winner_id, end_reason=d.end_reason, players=players)


async def snapshot(session: AsyncSession, m: MitbEvent) -> EventSnap:
    players = []
    for p in m.players:
        user = await session.get(User, p.user_id)
        players.append(MPlayerSnap(
            user_id=p.user_id, name=user.display_name if user else "Unknown Superstar", seed=p.seed, status=p.status,
            had_bye=p.had_bye, out_round=p.out_round, rung=p.rung, hp=p.hp,
            finishers_left=rules.finishers_left(p), reversals_left=rules.reversals_left(p),
            last_attack=p.last_attack, stun=p.stun, damage_dealt=p.damage_dealt, ladder_order=p.ladder_order,
            prompt_message_id=p.prompt_message_id, dm_started=bool(user and user.dm_started)))
    duels: list[DuelSnap] = []
    if m.status in (MS.QUALIFIER, MS.LADDER, MS.COMPLETED) and m.round_no:
        stmt = (select(MitbDuel).where(MitbDuel.event_id == m.id, MitbDuel.kind == "bracket")
                .order_by(MitbDuel.round_no.desc(), MitbDuel.id).execution_options(populate_existing=True))
        rows = (await session.execute(stmt)).scalars().all()
        last_round = rows[0].round_no if rows else 0
        duels = [await duel_snapshot(session, d) for d in rows if d.round_no == last_round]
        duels.sort(key=lambda x: x.id)
    return EventSnap(
        id=m.id, group_chat_id=m.group_chat_id, group_title=m.group_title, host_id=m.host_id, mode=m.mode,
        capacity=m.capacity, status=m.status, round_no=m.round_no, phase=m.phase, turn_number=m.turn_number,
        ladder_turns=m.ladder_turns, active_player_id=m.active_player_id, pending_move=m.pending_move,
        pending_target_id=m.pending_target_id, deadline_at=m.deadline_at, winner_id=m.winner_id,
        end_reason=m.end_reason, lobby_message_id=m.lobby_message_id, board_message_id=m.board_message_id,
        players=players, duels=duels)


async def get_snapshot(session: AsyncSession, event_id: int) -> EventSnap | None:
    m = await session.get(MitbEvent, event_id, populate_existing=True)
    return await snapshot(session, m) if m else None


async def get_duel_snapshot(session: AsyncSession, duel_id: int) -> DuelSnap | None:
    d = await session.get(MitbDuel, duel_id, populate_existing=True)
    return await duel_snapshot(session, d) if d else None


async def offer_snapshot(session: AsyncSession, o: CashinOffer) -> OfferSnap:
    b = await session.get(Briefcase, o.briefcase_id)
    return OfferSnap(id=o.id, briefcase_id=o.briefcase_id, holder_id=b.user_id, holder_name=await _name(session, b.user_id),
                     group_chat_id=o.group_chat_id, target_user_id=o.target_user_id,
                     target_name=await _name(session, o.target_user_id), target_hp=o.target_hp, status=o.status,
                     expires_at=o.expires_at, message_id=o.message_id)


async def _ok_event(session: AsyncSession, m: MitbEvent, kind: str, events: list[MEvent] | None = None) -> MResult:
    return MResult(True, kind, event=await snapshot(session, m), events=events or [])


async def open_event_id_in_chat(session: AsyncSession, chat_id: int) -> int | None:
    stmt = select(MitbEvent.id).where(MitbEvent.group_chat_id == chat_id, MitbEvent.status.in_(MS.OPEN))
    return (await session.execute(stmt)).scalars().first()


# ------------------------------------------------------------------------- lobby

async def create(session: AsyncSession, ident: Identity, chat_id: int, title: str | None, capacity: int = 8,
                 mode: str = "fast", now: datetime | None = None) -> MResult:
    now = now or utcnow()
    if capacity not in mr.SIZES:
        return MResult.fail("Size must be 8, 16 or 32 (example: /mitb 16).")
    if mode not in mr.TIMINGS:
        return MResult.fail("Mode must be fast or slow (example: /mitb 16 slow).")
    user = await upsert_user(session, ident)
    if not user.dm_started:
        return MResult.fail(DM_MSG)
    if await open_event_id_in_chat(session, chat_id) is not None:
        return MResult.fail("🏟️ A Money in the Bank tournament is already open in this group.")
    if await is_busy(session, ident.id):
        return MResult.fail("🥊 You're already in a match, Rumble, Tag Team or tournament. Finish it first.")
    m = MitbEvent(group_chat_id=chat_id, group_title=(title or None) and title[:256], host_id=ident.id, mode=mode,
                  capacity=capacity, status=MS.LOBBY, created_at=now,
                  deadline_at=now + timedelta(seconds=mr.timing(mode).lobby),
                  players=[MitbPlayer(user_id=ident.id)])
    try:
        async with session.begin_nested():
            session.add(m)
            await session.flush()
    except IntegrityError:
        return MResult.fail("🥊 You're already in a match or tournament.")
    return await _ok_event(session, m, "created")


async def join(session: AsyncSession, event_id: int, ident: Identity, chat_id: int, now: datetime | None = None,
               rng: random.Random | None = None) -> MResult:
    now = now or utcnow()
    m = await _lock_event(session, event_id)
    if m is None or m.status != MS.LOBBY or m.group_chat_id != chat_id:
        return MResult.fail("This tournament lobby is closed.")
    user = await upsert_user(session, ident)
    if _p(m, ident.id) is not None:
        return MResult.fail("You're already in the tournament!")
    if not user.dm_started:
        return MResult.fail(DM_MSG)
    if len(m.players) >= m.capacity:
        return MResult.fail("🚫 The tournament is full.")
    if await is_busy(session, ident.id):
        return MResult.fail("🥊 You're already in a match, Rumble, Tag Team or tournament.")
    try:
        async with session.begin_nested():
            session.add(MitbPlayer(event_id=m.id, user_id=ident.id))
            await session.flush()
    except IntegrityError:
        return MResult.fail("🥊 You're already in a match or tournament.")
    await session.refresh(m, attribute_names=["players"])
    if len(m.players) >= m.capacity:                       # full: the bell rings straight away
        events = await _start_event(session, m, now, rng or _rng)
        return await _ok_event(session, m, "started", events)
    return await _ok_event(session, m, "joined")


async def leave(session: AsyncSession, event_id: int, user_id: int) -> MResult:
    m = await _lock_event(session, event_id)
    if m is None or m.status != MS.LOBBY:
        return MResult.fail("This tournament lobby is closed.")
    p = _p(m, user_id)
    if p is None:
        return MResult.fail("You're not in this tournament.")
    await session.delete(p)
    await session.flush()
    await session.refresh(m, attribute_names=["players"])
    if not m.players:
        m.status, m.end_reason, m.deadline_at = MS.CANCELLED, "empty", None
        return await _ok_event(session, m, "cancelled")
    if m.host_id == user_id:
        m.host_id = m.players[0].user_id
    return await _ok_event(session, m, "left")


async def start(session: AsyncSession, event_id: int, user_id: int, now: datetime | None = None,
                rng: random.Random | None = None) -> MResult:
    now = now or utcnow()
    m = await _lock_event(session, event_id)
    if m is None or m.status != MS.LOBBY:
        return MResult.fail("This tournament lobby is closed.")
    if user_id != m.host_id and user_id != get_settings().owner_id:
        return MResult.fail("🚫 Only the host can start the tournament early.")
    if len(m.players) < mr.MIN_PLAYERS:
        return MResult.fail(f"👥 At least {mr.MIN_PLAYERS} wrestlers are needed (now {len(m.players)}).")
    events = await _start_event(session, m, now, rng or _rng)
    return await _ok_event(session, m, "started", events)


async def cancel(session: AsyncSession, event_id: int, user_id: int) -> MResult:
    m = await _lock_event(session, event_id)
    if m is None or m.status not in MS.OPEN:
        return MResult.fail("There's no open tournament to cancel.")
    if user_id != m.host_id and user_id != get_settings().owner_id:
        return MResult.fail("🚫 Only the host can cancel the tournament.")
    m.status, m.end_reason, m.phase, m.deadline_at = MS.CANCELLED, "cancelled", None, None
    for p in m.players:
        p.is_active = False
    duels = (await session.execute(select(MitbDuel).where(MitbDuel.event_id == m.id, MitbDuel.status == "active")
                                   .execution_options(populate_existing=True))).scalars().all()
    for d in duels:
        d.status, d.end_reason, d.phase, d.deadline_at = "completed", "cancelled", None, None
        for dp in d.players:
            dp.is_active = False
    return await _ok_event(session, m, "cancelled")


# ----------------------------------------------------------------- bracket (duels)

def _duel_begin_turn(d: MitbDuel, user_id: int, now: datetime) -> None:
    d.active_user_id, d.phase, d.pending_move = user_id, RPhase.AWAIT_MOVE, None
    d.turn_number += 1
    d.deadline_at = now + timedelta(seconds=mr.timing(d.mode).turn)


async def _start_event(session: AsyncSession, m: MitbEvent, now: datetime, rng: random.Random) -> list[MEvent]:
    order = [p.user_id for p in m.players]
    rng.shuffle(order)
    for seed, uid in enumerate(order, start=1):
        _p(m, uid).seed = seed
    m.status, m.started_at, m.round_no, m.deadline_at = MS.QUALIFIER, now, 0, None
    return [MEvent("event_start")] + await _begin_round(session, m, now, rng)


async def _begin_round(session: AsyncSession, m: MitbEvent, now: datetime, rng: random.Random) -> list[MEvent]:
    alive = [p for p in m.players if p.status == "alive"]
    if len(alive) <= mr.LADDER_MAX:
        return _begin_ladder(m, now, rng)
    m.round_no += 1
    pairs, bye = mr.plan_round([p.user_id for p in alive], {p.user_id for p in alive if p.had_bye}, rng)
    t = mr.timing(m.mode)
    duels = []
    for a, b in pairs:
        d = MitbDuel(event_id=m.id, group_chat_id=m.group_chat_id, kind="bracket", mode=m.mode, round_no=m.round_no,
                     status="active", created_at=now, cap_at=now + timedelta(seconds=t.duel_cap),
                     players=[MitbDuelPlayer(user_id=a), MitbDuelPlayer(user_id=b)])
        await _add(session, d)
        _duel_begin_turn(d, rng.choice([a, b]), now)              # coin flip: nobody is favoured
        duels.append(d)
    if bye is not None:
        _p(m, bye).had_bye = True
    events = [MEvent("round_start", round_no=m.round_no, bye=bye,
                     pairs=[(d.id, d.players[0].user_id, d.players[1].user_id) for d in duels])]
    events += [MEvent("duel_turn_start", duel_id=d.id, user_id=d.active_user_id) for d in duels]
    return events


def _check_duel(d: MitbDuel | None, user_id: int, turn: int, phase: str, who: str = "actor") -> str | None:
    if d is None or d.status != "active":
        return "This match is over."
    if turn != d.turn_number:
        return "⌛ That button is outdated."
    if d.phase != phase:
        return "⌛ That action isn't available right now."
    expected = d.active_user_id if who == "actor" else getattr(_dother(d, d.active_user_id), "user_id", None)
    if user_id != expected:
        return "🚫 It's not your turn." if who == "actor" else "🚫 That attack isn't aimed at you."
    return None


async def _duel_result(session: AsyncSession, d: MitbDuel, kind: str, events: list[MEvent] | None = None) -> MResult:
    event = None
    if d.kind == "bracket" and d.event_id and any(e.kind == "duel_end" for e in (events or [])):
        event = await get_snapshot(session, d.event_id)
    return MResult(True, kind, duel=await duel_snapshot(session, d), event=event, events=events or [])


async def duel_submit_move(session: AsyncSession, duel_id: int, user_id: int, turn: int, move: str,
                           now: datetime | None = None) -> MResult:
    now = now or utcnow()
    d = await _lock_duel(session, duel_id)
    if (err := _check_duel(d, user_id, turn, RPhase.AWAIT_MOVE)):
        return MResult.fail(err)
    me = _dp(d, user_id)
    try:
        mv = rules.parse_move(move)
        rules.validate_attack(me, mv)
    except RuleError as exc:
        return MResult.fail(str(exc))
    me.afk_strikes = 0
    d.pending_move, d.phase = mv.value, RPhase.AWAIT_REVERSAL
    d.deadline_at = now + timedelta(seconds=mr.timing(d.mode).reversal)
    return await _duel_result(session, d, "attack_declared")


async def duel_submit_response(session: AsyncSession, duel_id: int, user_id: int, turn: int, use_reversal: bool,
                               now: datetime | None = None, rng: random.Random | None = None) -> MResult:
    now = now or utcnow()
    d = await _lock_duel(session, duel_id)
    if (err := _check_duel(d, user_id, turn, RPhase.AWAIT_REVERSAL, who="target")):
        return MResult.fail(err)
    if use_reversal:
        try:
            rules.validate_reversal(_dp(d, user_id))
        except RuleError as exc:
            return MResult.fail(str(exc))
    events = await _duel_resolve(session, d, now, use_reversal, False, rng or _rng)
    return await _duel_result(session, d, "turn_resolved", events)


async def _duel_resolve(session: AsyncSession, d: MitbDuel, now: datetime, reversed_: bool, auto: bool,
                        rng: random.Random) -> list[MEvent]:
    a = _dp(d, d.active_user_id)
    b = _dother(d, a.user_id)
    move = Move(d.pending_move)
    succeeded = rules.reversal_succeeds(rng) if reversed_ else False
    res = rules.resolve_attack(a, b, move, reversed_, reversal_succeeds=succeeded)
    a.damage_dealt += res.damage_to_defender
    b.damage_dealt += res.damage_to_attacker
    events = [MEvent("duel_turn", duel_id=d.id, user_id=a.user_id, other_id=b.user_id, move=move.value,
                     reversed=res.reversed, damage=res.damage_to_defender, reflected=res.damage_to_attacker,
                     hp_a=a.hp, hp_d=b.hp, auto=auto)]
    if b.hp <= 0:
        events += await _duel_finish(session, d, a.user_id, "knockout", now, rng)
    elif a.hp <= 0:
        events += await _duel_finish(session, d, b.user_id, "knockout", now, rng)
    else:
        d.phase, d.pending_move = None, None
        _duel_begin_turn(d, b.user_id, now)
        events.append(MEvent("duel_turn_start", duel_id=d.id, user_id=b.user_id))
    return events


async def _duel_finish(session: AsyncSession, d: MitbDuel, winner_id: int, reason: str, now: datetime,
                       rng: random.Random) -> list[MEvent]:
    loser = _dother(d, winner_id)
    d.status, d.winner_id, d.end_reason, d.completed_at = "completed", winner_id, reason, now
    d.phase, d.pending_move, d.deadline_at = None, None, None
    for p in d.players:
        p.is_active = False
    win = _dp(d, winner_id)
    events = [MEvent("duel_end", duel_id=d.id, user_id=winner_id, other_id=loser.user_id, reason=reason,
                     hp_a=win.hp, hp_d=loser.hp, round_no=d.round_no)]
    if d.kind == "bracket":
        events += await _after_bracket_duel(session, d, winner_id, loser.user_id, now, rng)
    else:
        events += await _after_cashin(session, d, winner_id, now)
    return events


async def _after_bracket_duel(session: AsyncSession, d: MitbDuel, winner_id: int, loser_id: int, now: datetime,
                              rng: random.Random) -> list[MEvent]:
    m = await _lock_event(session, d.event_id)                # lock order: duel -> event
    if m is None or m.status != MS.QUALIFIER:
        return []
    out = _p(m, loser_id)
    out.status, out.out_round, out.is_active = "out", d.round_no, False
    if await _count_active_duels(session, m.id) > 0:
        return []
    return await _begin_round(session, m, now, rng)


async def _after_cashin(session: AsyncSession, d: MitbDuel, winner_id: int, now: datetime) -> list[MEvent]:
    b = await _get_briefcase(session, d.briefcase_id)
    won = b is not None and winner_id == b.user_id
    if b is not None:
        b.status, b.used_at = ("won" if won else "lost"), now
    return [MEvent("cashin_end", user_id=winner_id, flag=won)]


# ------------------------------------------------------------------------- ladder

def _begin_ladder(m: MitbEvent, now: datetime, rng: random.Random) -> list[MEvent]:
    finalists = [p for p in m.players if p.status == "alive"]
    rng.shuffle(finalists)
    for i, p in enumerate(finalists, start=1):
        p.status, p.ladder_order, p.rung, p.hp = "ladder", i, 0, rules.MAX_HP
        p.finishers_used = p.reversals_used = p.afk_strikes = p.stun = p.damage_dealt = 0
        p.last_attack = None
    m.status, m.ladder_turns, m.active_player_id, m.phase = MS.LADDER, 0, None, None
    return [MEvent("ladder_start")] + _ladder_next_turn(m, now)


def _ladder_next_turn(m: MitbEvent, now: datetime) -> list[MEvent]:
    """Next wrestler in the fixed order; a stunned one loses the turn (and the stun wears off)."""
    lad = _ladder(m)
    prev = _p(m, m.active_player_id)
    prev_order = prev.ladder_order if prev else 0
    events: list[MEvent] = []
    actor = None
    for _ in range(len(lad) * 2 + 1):
        actor = next((p for p in lad if p.ladder_order > prev_order), lad[0])
        prev_order = actor.ladder_order
        if actor.stun > 0:
            actor.stun -= 1
            m.ladder_turns += 1
            events.append(MEvent("stunned", user_id=actor.user_id))
            continue
        break
    m.active_player_id, m.phase, m.pending_move, m.pending_target_id = actor.user_id, RPhase.AWAIT_MOVE, None, None
    m.turn_number += 1
    m.deadline_at = now + timedelta(seconds=mr.timing(m.mode).turn)
    events.append(MEvent("ladder_turn_start", user_id=actor.user_id))
    return events


async def _complete(session: AsyncSession, m: MitbEvent, winner: MitbPlayer, now: datetime, reason: str) -> list[MEvent]:
    m.status, m.winner_id, m.end_reason, m.completed_at = MS.COMPLETED, winner.user_id, reason, now
    m.phase = m.deadline_at = m.active_player_id = None
    winner.status = "champion"
    for p in m.players:
        p.is_active = False
    await _expire_active_briefcase(session, winner.user_id)
    await _add(session, Briefcase(user_id=winner.user_id, group_chat_id=m.group_chat_id, event_id=m.id,
                                  status="active", created_at=now,
                                  expires_at=now + timedelta(days=mr.BRIEFCASE_DAYS)))
    return [MEvent("winner", user_id=winner.user_id, reason=reason)]


async def _ladder_finish_turn(session: AsyncSession, m: MitbEvent, now: datetime, rng: random.Random,
                              events: list[MEvent]) -> None:
    m.phase, m.pending_move, m.pending_target_id, m.deadline_at = None, None, None, None
    m.ladder_turns += 1
    lad = _ladder(m)
    if len(lad) == 1:
        events += await _complete(session, m, lad[0], now, "last_standing")
    elif m.ladder_turns >= mr.LADDER_ROUNDS_CAP * len(lad):
        key = mr.pick_by_points([(p.user_id, (p.rung, p.hp, p.damage_dealt)) for p in lad], rng)
        events += await _complete(session, m, _p(m, key), now, "time_limit")
    else:
        events += _ladder_next_turn(m, now)


def _check_ladder(m: MitbEvent | None, user_id: int, turn: int, phases: tuple[str, ...], who: str = "actor") -> str | None:
    if m is None or m.status != MS.LADDER:
        return "The ladder match is not running."
    if turn != m.turn_number:
        return "⌛ That button is outdated."
    if m.phase not in phases:
        return "⌛ That action isn't available right now."
    expected = m.active_player_id if who == "actor" else m.pending_target_id
    if user_id != expected:
        return "🚫 It's not your turn." if who == "actor" else "🚫 That attack isn't aimed at you."
    return None


async def ladder_move(session: AsyncSession, event_id: int, user_id: int, turn: int, move: str,
                      now: datetime | None = None, rng: random.Random | None = None) -> MResult:
    """CLIMB (resolved at once) or pick an attack (then choose the target)."""
    now = now or utcnow()
    rng = rng or _rng
    m = await _lock_event(session, event_id)
    if (err := _check_ladder(m, user_id, turn, (RPhase.AWAIT_MOVE, RPhase.AWAIT_TARGET))):
        return MResult.fail(err)
    me = _p(m, user_id)
    if move == CLIMB:
        me.afk_strikes = 0
        me.rung += 1
        events = [MEvent("climb", user_id=user_id, rung=me.rung)]
        if me.rung >= mr.RUNGS:
            events += await _complete(session, m, me, now, "grabbed")
        else:
            await _ladder_finish_turn(session, m, now, rng, events)
        return await _ok_event(session, m, "turn_resolved", events)
    try:
        mv = rules.parse_move(move)
        rules.validate_attack(me, mv)
    except RuleError as exc:
        return MResult.fail(str(exc))
    me.afk_strikes = 0
    m.pending_move, m.phase = mv.value, RPhase.AWAIT_TARGET
    return await _ok_event(session, m, "move_chosen")


async def ladder_back(session: AsyncSession, event_id: int, user_id: int, turn: int) -> MResult:
    m = await _lock_event(session, event_id)
    if (err := _check_ladder(m, user_id, turn, (RPhase.AWAIT_TARGET,))):
        return MResult.fail(err)
    m.pending_move, m.phase = None, RPhase.AWAIT_MOVE
    return await _ok_event(session, m, "back")


async def ladder_target(session: AsyncSession, event_id: int, user_id: int, turn: int, target_id: int,
                        now: datetime | None = None) -> MResult:
    now = now or utcnow()
    m = await _lock_event(session, event_id)
    if (err := _check_ladder(m, user_id, turn, (RPhase.AWAIT_TARGET,))):
        return MResult.fail(err)
    target = _p(m, target_id)
    if target is None or target.status != "ladder" or target_id == user_id:
        return MResult.fail("🎯 Pick another wrestler who is still on the ladder.")
    m.pending_target_id, m.phase = target_id, RPhase.AWAIT_REVERSAL
    m.deadline_at = now + timedelta(seconds=mr.timing(m.mode).reversal)
    return await _ok_event(session, m, "attack_declared")


async def ladder_response(session: AsyncSession, event_id: int, user_id: int, turn: int, use_reversal: bool,
                          now: datetime | None = None, rng: random.Random | None = None) -> MResult:
    now = now or utcnow()
    m = await _lock_event(session, event_id)
    if (err := _check_ladder(m, user_id, turn, (RPhase.AWAIT_REVERSAL,), who="target")):
        return MResult.fail(err)
    if use_reversal:
        try:
            rules.validate_reversal(_p(m, user_id))
        except RuleError as exc:
            return MResult.fail(str(exc))
    events = await _ladder_resolve(session, m, now, use_reversal, False, rng or _rng)
    return await _ok_event(session, m, "turn_resolved", events)


def _knock_down(p: MitbPlayer) -> None:
    p.rung, p.hp, p.stun = 0, mr.STUN_HP, 1


async def _ladder_resolve(session: AsyncSession, m: MitbEvent, now: datetime, reversed_: bool, auto: bool,
                          rng: random.Random) -> list[MEvent]:
    a, d, move = _p(m, m.active_player_id), _p(m, m.pending_target_id), Move(m.pending_move)
    succeeded = rules.reversal_succeeds(rng) if reversed_ else False
    res = rules.resolve_attack(a, d, move, reversed_, reversal_succeeds=succeeded)
    a.damage_dealt += res.damage_to_defender
    d.damage_dealt += res.damage_to_attacker
    events = [MEvent("attack", user_id=a.user_id, other_id=d.user_id, move=move.value, reversed=res.reversed,
                     damage=res.damage_to_defender, reflected=res.damage_to_attacker, hp_a=a.hp, hp_d=d.hp,
                     auto=auto)]
    if res.reversed:                                          # the counter shakes the attacker off a rung
        if a.hp <= 0:
            _knock_down(a)
            events.append(MEvent("knockdown", user_id=a.user_id))
        elif a.rung > 0:
            a.rung -= 1
            events.append(MEvent("shaken", user_id=a.user_id, rung=a.rung, damage=1))
    else:
        drop = 2 if move == Move.FINISHER else 1              # a Finisher knocks you down two rungs
        if d.hp <= 0:
            _knock_down(d)
            events.append(MEvent("knockdown", user_id=d.user_id))
        elif d.rung > 0:
            d.rung = max(0, d.rung - drop)
            events.append(MEvent("shaken", user_id=d.user_id, rung=d.rung, damage=drop))
    await _ladder_finish_turn(session, m, now, rng, events)
    return events


# ------------------------------------------------------------------ deadline loop

async def due_ids(session: AsyncSession, now: datetime | None = None) -> tuple[list[int], list[int]]:
    """(event ids, duel ids) with something due."""
    now = now or utcnow()
    ev = select(MitbEvent.id).where(MitbEvent.status.in_((MS.LOBBY, MS.LADDER)), MitbEvent.deadline_at <= now).limit(50)
    du = select(MitbDuel.id).where(MitbDuel.status == "active",
                                   (MitbDuel.deadline_at <= now) | (MitbDuel.cap_at <= now)).limit(100)
    return (list((await session.execute(ev)).scalars().all()), list((await session.execute(du)).scalars().all()))


async def handle_due_event(session: AsyncSession, event_id: int, now: datetime | None = None,
                           rng: random.Random | None = None) -> MResult | None:
    now = now or utcnow()
    rng = rng or _rng
    m = await _lock_event(session, event_id, skip_locked=True)
    if m is None or m.deadline_at is None or m.deadline_at > now:
        return None
    if m.status == MS.LOBBY:
        if len(m.players) >= mr.MIN_PLAYERS:
            return await _ok_event(session, m, "started", await _start_event(session, m, now, rng))
        m.status, m.end_reason, m.deadline_at = MS.CANCELLED, "not_enough_players", None
        for p in m.players:
            p.is_active = False
        return await _ok_event(session, m, "lobby_expired")
    if m.status != MS.LADDER:
        return None
    if m.phase == RPhase.AWAIT_REVERSAL:
        return await _ok_event(session, m, "turn_resolved", await _ladder_resolve(session, m, now, False, True, rng))
    if m.phase in (RPhase.AWAIT_MOVE, RPhase.AWAIT_TARGET):
        actor = _p(m, m.active_player_id)
        actor.afk_strikes += 1
        if actor.afk_strikes >= mr.AFK_LIMIT:
            actor.status, actor.is_active = "dq", False
            events = [MEvent("dq", user_id=actor.user_id)]
        else:
            events = [MEvent("skip", user_id=actor.user_id)]
        await _ladder_finish_turn(session, m, now, rng, events)
        return await _ok_event(session, m, "turn_skipped", events)
    return None


async def handle_due_duel(session: AsyncSession, duel_id: int, now: datetime | None = None,
                          rng: random.Random | None = None) -> MResult | None:
    now = now or utcnow()
    rng = rng or _rng
    d = await _lock_duel(session, duel_id, skip_locked=True)
    if d is None or d.status != "active":
        return None
    if d.cap_at and d.cap_at <= now:                           # out of time: decided on points, never a stall
        key = mr.pick_by_points([(p.user_id, (p.hp, p.damage_dealt)) for p in d.players], rng)
        return await _duel_result(session, d, "duel_update", await _duel_finish(session, d, key, "time_limit", now, rng))
    if d.deadline_at and d.deadline_at <= now:
        if d.phase == RPhase.AWAIT_REVERSAL:
            return await _duel_result(session, d, "duel_update", await _duel_resolve(session, d, now, False, True, rng))
        if d.phase == RPhase.AWAIT_MOVE:
            actor = _dp(d, d.active_user_id)
            other = _dother(d, actor.user_id)
            actor.afk_strikes += 1
            if actor.afk_strikes >= mr.AFK_LIMIT:
                events = await _duel_finish(session, d, other.user_id, "forfeit", now, rng)
            else:
                events = [MEvent("duel_skip", duel_id=d.id, user_id=actor.user_id)]
                d.phase = None
                _duel_begin_turn(d, other.user_id, now)
                events.append(MEvent("duel_turn_start", duel_id=d.id, user_id=other.user_id))
            return await _duel_result(session, d, "duel_update", events)
    return None


async def recover_deadlines(session: AsyncSession, now: datetime | None = None,
                            grace_seconds: int | None = None) -> int:
    """After a restart, give everyone a short grace period for deadlines that lapsed while offline."""
    now = now or utcnow()
    grace = get_settings().restart_grace if grace_seconds is None else grace_seconds
    later = now + timedelta(seconds=grace)
    total = 0
    stmts = (
        update(MitbEvent).where(MitbEvent.status.in_((MS.LOBBY, MS.LADDER)), MitbEvent.deadline_at.is_not(None),
                                MitbEvent.deadline_at < now).values(deadline_at=later),
        update(MitbDuel).where(MitbDuel.status == "active", MitbDuel.deadline_at.is_not(None),
                               MitbDuel.deadline_at < now).values(deadline_at=later),
        update(MitbDuel).where(MitbDuel.status == "active", MitbDuel.cap_at.is_not(None),
                               MitbDuel.cap_at < now).values(cap_at=later + timedelta(minutes=5)),
    )
    for stmt in stmts:
        total += (await session.execute(stmt)).rowcount or 0
    return total


# ------------------------------------------------------------------------ cash-in

async def offer_cashin(session: AsyncSession, group_chat_id: int, match_player_ids: set[int], target_id: int,
                       target_hp: int, now: datetime | None = None) -> MResult:
    """After a 1v1 match in the group: offer the oldest valid briefcase a 20-second cash-in window."""
    now = now or utcnow()
    stmt = (select(Briefcase).where(Briefcase.group_chat_id == group_chat_id, Briefcase.status == "active",
                                    Briefcase.expires_at > now).order_by(Briefcase.created_at, Briefcase.id))
    chosen = None
    for b in (await session.execute(stmt)).scalars().all():
        if b.user_id in match_player_ids or await is_busy(session, b.user_id):
            continue
        chosen = b
        break
    if chosen is None:
        return MResult.fail("No briefcase available.")
    if await is_busy(session, target_id):
        return MResult.fail("The winner is busy.")
    o = CashinOffer(briefcase_id=chosen.id, group_chat_id=group_chat_id, target_user_id=target_id,
                    target_hp=max(1, target_hp), expires_at=now + timedelta(seconds=mr.CASHIN_WINDOW))
    await _add(session, o)
    return MResult(True, "offered", offer=await offer_snapshot(session, o))


async def take_cashin(session: AsyncSession, offer_id: int, user_id: int, now: datetime | None = None) -> MResult:
    now = now or utcnow()
    o = (await session.execute(select(CashinOffer).where(CashinOffer.id == offer_id).with_for_update()
                               .execution_options(populate_existing=True))).scalar_one_or_none()
    if o is None or o.status != "open" or o.expires_at <= now:
        return MResult.fail("⌛ The cash-in window has closed.")
    b = await _get_briefcase(session, o.briefcase_id)
    if b is None or b.status != "active" or b.expires_at <= now:
        return MResult.fail("💼 This briefcase is no longer valid.")
    if user_id != b.user_id:
        return MResult.fail("🚫 Only the briefcase holder can cash in.")
    if await is_busy(session, user_id) or await is_busy(session, o.target_user_id):
        return MResult.fail("🥊 One of the wrestlers is busy in another match.")
    d = MitbDuel(event_id=None, group_chat_id=o.group_chat_id, kind="cashin", mode="fast", round_no=0,
                 briefcase_id=b.id, status="active", created_at=now,
                 cap_at=now + timedelta(seconds=mr.timing("fast").duel_cap),
                 players=[MitbDuelPlayer(user_id=user_id),
                          MitbDuelPlayer(user_id=o.target_user_id, hp=mr.cashin_hp(o.target_hp))])
    try:
        async with session.begin_nested():
            session.add(d)
            await session.flush()
    except IntegrityError:
        return MResult.fail("🥊 One of the wrestlers is busy in another match.")
    o.status = "taken"
    _duel_begin_turn(d, o.target_user_id, now)                # the defender strikes first (fairness)
    events = [MEvent("cashin_start", duel_id=d.id, user_id=user_id, other_id=o.target_user_id,
                     hp_d=mr.cashin_hp(o.target_hp)),
              MEvent("duel_turn_start", duel_id=d.id, user_id=o.target_user_id)]
    return MResult(True, "cashin_started", duel=await duel_snapshot(session, d), offer=await offer_snapshot(session, o),
                   events=events)


async def expire_stale(session: AsyncSession, now: datetime | None = None) -> list[OfferSnap]:
    """Close cash-in offers whose 20 s are up and expire old briefcases. Returns the offers just expired."""
    now = now or utcnow()
    stmt = (select(CashinOffer).where(CashinOffer.status == "open", CashinOffer.expires_at <= now)
            .execution_options(populate_existing=True))
    expired = []
    for o in (await session.execute(stmt)).scalars().all():
        o.status = "expired"
        expired.append(await offer_snapshot(session, o))
    await session.execute(update(Briefcase).where(Briefcase.status == "active", Briefcase.expires_at <= now)
                          .values(status="expired"))
    return expired


async def active_briefcase(session: AsyncSession, user_id: int) -> BriefcaseSnap | None:
    stmt = select(Briefcase).where(Briefcase.user_id == user_id, Briefcase.status == "active",
                                   Briefcase.expires_at > utcnow())
    b = (await session.execute(stmt)).scalars().first()
    return BriefcaseSnap(b.id, b.user_id, await _name(session, b.user_id), b.group_chat_id, b.status,
                         b.expires_at) if b else None


# ------------------------------------------------------------- message bookkeeping

async def set_message_id(session: AsyncSession, event_id: int, column: str, message_id: int) -> None:
    assert column in ("lobby_message_id", "board_message_id")
    await session.execute(update(MitbEvent).where(MitbEvent.id == event_id).values({column: message_id}))


async def set_offer_message(session: AsyncSession, offer_id: int, message_id: int) -> None:
    await session.execute(update(CashinOffer).where(CashinOffer.id == offer_id).values(message_id=message_id))


async def set_duel_prompt(session: AsyncSession, duel_id: int, user_id: int, message_id: int) -> None:
    await session.execute(update(MitbDuelPlayer).where(
        MitbDuelPlayer.duel_id == duel_id, MitbDuelPlayer.user_id == user_id).values(prompt_message_id=message_id))


async def set_ladder_prompt(session: AsyncSession, event_id: int, user_id: int, message_id: int) -> None:
    await session.execute(update(MitbPlayer).where(
        MitbPlayer.event_id == event_id, MitbPlayer.user_id == user_id).values(prompt_message_id=message_id))


async def mark_dm_unreachable(session: AsyncSession, user_id: int) -> None:
    await session.execute(update(User).where(User.id == user_id).values(dm_started=False))


# ---------------------------------------------------------------------- statistics

async def mitb_leaderboard(session: AsyncSession, limit: int = 10) -> list[tuple[str, int, int, int]]:
    """(name, tournaments won, cash-in wins, cash-in losses)."""
    titles = (await session.execute(
        select(MitbEvent.winner_id, func.count()).where(MitbEvent.status == MS.COMPLETED,
                                                        MitbEvent.winner_id.is_not(None))
        .group_by(MitbEvent.winner_id))).all()
    cash = (await session.execute(
        select(Briefcase.user_id, func.sum(case((Briefcase.status == "won", 1), else_=0)),
               func.sum(case((Briefcase.status == "lost", 1), else_=0))).group_by(Briefcase.user_id))).all()
    table: dict[int, list[int]] = {}
    for uid, n in titles:
        table.setdefault(uid, [0, 0, 0])[0] = int(n)
    for uid, w, l in cash:
        row = table.setdefault(uid, [0, 0, 0])
        row[1], row[2] = int(w or 0), int(l or 0)
    ranked = sorted(table.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[1][2]))[:limit]
    return [(await _name(session, uid), t, w, l) for uid, (t, w, l) in ranked]
