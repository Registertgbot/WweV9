"""Royal Rumble engine (database-backed, no Telegram calls).

Same contract as ``match_engine``: every public function takes an open session inside a transaction,
locks the Rumble row (``SELECT ... FOR UPDATE``), re-validates status / phase / turn number and
returns an ``RResult``. The caller publishes the events *after* the commit.

Flow: lobby -> start (2 wrestlers in the ring, the rest enter on a timer) -> turns go round the ring:
  await_move -> await_target -> await_reversal -> resolved (or skipped when someone is idle).
The last wrestler standing, with nobody left to enter, wins.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from models import RPhase, RStatus, Rumble, RumblePlayer, TagPlayer, User, utcnow
from services import rules, runtime_settings
from services import rumble_rules as rr
from services.activity import is_busy
from services.match_engine import Identity, upsert_user
from services.rules import Move, RuleError

_rng = random.Random()
DM_MSG = "📩 Open the bot privately and press Start first — moves are chosen in DM."


# --------------------------------------------------------------------------- data

@dataclass
class RPlayerSnap:
    user_id: int
    name: str
    hp: int
    entry_order: int
    in_ring: bool
    eliminated: bool
    placement: int | None
    eliminations: int
    damage_dealt: int
    finishers_left: int
    reversals_left: int
    last_attack: str | None
    prompt_message_id: int | None
    dm_started: bool


@dataclass
class RumbleSnap:
    id: int
    group_chat_id: int
    group_title: str | None
    host_id: int
    status: str
    phase: str | None
    active_player_id: int | None
    turn_number: int
    pending_move: str | None
    pending_target_id: int | None
    deadline_at: datetime | None
    next_entry_at: datetime | None
    winner_id: int | None
    end_reason: str | None
    lobby_message_id: int | None
    board_message_id: int | None
    players: list[RPlayerSnap]

    def player(self, user_id: int | None) -> RPlayerSnap | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    @property
    def ring(self) -> list[RPlayerSnap]:
        return sorted((p for p in self.players if p.in_ring), key=lambda p: p.entry_order)

    @property
    def waiting(self) -> list[RPlayerSnap]:
        return sorted((p for p in self.players if not p.in_ring and not p.eliminated), key=lambda p: p.entry_order)

    @property
    def out(self) -> list[RPlayerSnap]:
        return sorted((p for p in self.players if p.eliminated), key=lambda p: p.placement or 0, reverse=True)

    @property
    def alive_count(self) -> int:
        return sum(1 for p in self.players if not p.eliminated)


@dataclass
class REvent:
    kind: str                      # entry | turn | skip | eliminated | final_two | winner | turn_start
    user_id: int | None = None
    other_id: int | None = None
    move: str | None = None
    reversed: bool = False
    damage: int = 0                # dealt to the defender
    reflected: int = 0             # reflected onto the attacker
    hp_attacker: int = 0
    hp_defender: int = 0
    healed: int = 0
    finisher_kill: bool = False
    reason: str = ""
    auto: bool = False
    placement: int | None = None
    number: int | None = None      # entry number


@dataclass
class RResult:
    ok: bool
    kind: str = ""
    error: str | None = None
    rumble: RumbleSnap | None = None
    events: list[REvent] = field(default_factory=list)

    @classmethod
    def fail(cls, message: str) -> "RResult":
        return cls(ok=False, error=message)


# ------------------------------------------------------------------------ helpers

async def _lock(session: AsyncSession, rumble_id: int, skip_locked: bool = False) -> Rumble | None:
    stmt = (select(Rumble).where(Rumble.id == rumble_id)
            .with_for_update(skip_locked=skip_locked)
            .execution_options(populate_existing=True))
    return (await session.execute(stmt)).scalar_one_or_none()


def _p(r: Rumble, user_id: int | None) -> RumblePlayer | None:
    return next((p for p in r.players if p.user_id == user_id), None)


def _ring(r: Rumble) -> list[RumblePlayer]:
    return sorted((p for p in r.players if p.in_ring), key=lambda p: p.entry_order)


def _waiting(r: Rumble) -> list[RumblePlayer]:
    return sorted((p for p in r.players if not p.in_ring and not p.eliminated), key=lambda p: p.entry_order)


def _alive(r: Rumble) -> list[RumblePlayer]:
    return [p for p in r.players if not p.eliminated]


async def snapshot(session: AsyncSession, r: Rumble) -> RumbleSnap:
    players = []
    for p in r.players:
        user = await session.get(User, p.user_id)
        players.append(RPlayerSnap(
            user_id=p.user_id, name=user.display_name if user else "Unknown Superstar", hp=p.hp,
            entry_order=p.entry_order, in_ring=p.in_ring, eliminated=p.eliminated, placement=p.placement,
            eliminations=p.eliminations, damage_dealt=p.damage_dealt,
            finishers_left=rules.finishers_left(p), reversals_left=rules.reversals_left(p),
            last_attack=p.last_attack, prompt_message_id=p.prompt_message_id,
            dm_started=bool(user and user.dm_started)))
    return RumbleSnap(
        id=r.id, group_chat_id=r.group_chat_id, group_title=r.group_title, host_id=r.host_id, status=r.status,
        phase=r.phase, active_player_id=r.active_player_id, turn_number=r.turn_number,
        pending_move=r.pending_move, pending_target_id=r.pending_target_id, deadline_at=r.deadline_at,
        next_entry_at=r.next_entry_at, winner_id=r.winner_id, end_reason=r.end_reason,
        lobby_message_id=r.lobby_message_id, board_message_id=r.board_message_id, players=players)


async def get_snapshot(session: AsyncSession, rumble_id: int) -> RumbleSnap | None:
    r = await session.get(Rumble, rumble_id, populate_existing=True)
    return await snapshot(session, r) if r else None


async def _ok(session: AsyncSession, r: Rumble, kind: str, events: list[REvent] | None = None) -> RResult:
    return RResult(True, kind, rumble=await snapshot(session, r), events=events or [])


async def _busy(session: AsyncSession, user_id: int) -> bool:
    """In another Rumble or in an open 1v1 match?"""
    return await is_busy(session, user_id)


async def open_rumble_id_in_chat(session: AsyncSession, chat_id: int) -> int | None:
    stmt = select(Rumble.id).where(Rumble.group_chat_id == chat_id, Rumble.status.in_(RStatus.OPEN))
    return (await session.execute(stmt)).scalars().first()


# ------------------------------------------------------------------------- lobby

async def create_rumble(session: AsyncSession, ident: Identity, chat_id: int, title: str | None,
                        now: datetime | None = None) -> RResult:
    now = now or utcnow()
    user = await upsert_user(session, ident)
    if not user.dm_started:
        return RResult.fail(DM_MSG)
    if await open_rumble_id_in_chat(session, chat_id) is not None:
        return RResult.fail("🏟️ A Royal Rumble is already running in this group.")
    if await _busy(session, ident.id):
        return RResult.fail("🥊 You're already in a match or Rumble. Finish it (or /cancelmatch) first.")
    s = get_settings()
    r = Rumble(group_chat_id=chat_id, group_title=(title or None) and title[:256], host_id=ident.id,
               status=RStatus.LOBBY, created_at=now,
               deadline_at=now + timedelta(seconds=s.rumble_lobby_timeout),
               players=[RumblePlayer(user_id=ident.id, hp=rr.RUMBLE_HP)])
    try:
        async with session.begin_nested():
            session.add(r)
            await session.flush()
    except IntegrityError:
        return RResult.fail("🥊 You're already in a match or Rumble.")
    return await _ok(session, r, "created")


async def join(session: AsyncSession, rumble_id: int, ident: Identity, chat_id: int) -> RResult:
    r = await _lock(session, rumble_id)
    if r is None or r.status != RStatus.LOBBY or r.group_chat_id != chat_id:
        return RResult.fail("This Rumble lobby is closed.")
    user = await upsert_user(session, ident)
    if _p(r, ident.id) is not None:
        return RResult.fail("You're already in the Rumble!")
    if not user.dm_started:
        return RResult.fail(DM_MSG)
    if len(r.players) >= get_settings().rumble_max_players:
        return RResult.fail("🚫 The Rumble is full.")
    if await _busy(session, ident.id):
        return RResult.fail("🥊 You're already in a match or Rumble.")
    try:
        async with session.begin_nested():
            session.add(RumblePlayer(rumble_id=r.id, user_id=ident.id, hp=rr.RUMBLE_HP))
            await session.flush()
    except IntegrityError:
        return RResult.fail("🥊 You're already in a match or Rumble.")
    await session.refresh(r, attribute_names=["players"])
    return await _ok(session, r, "joined")


async def leave(session: AsyncSession, rumble_id: int, user_id: int, now: datetime | None = None) -> RResult:
    r = await _lock(session, rumble_id)
    if r is None or r.status != RStatus.LOBBY:
        return RResult.fail("This Rumble lobby is closed.")
    p = _p(r, user_id)
    if p is None:
        return RResult.fail("You're not in this Rumble.")
    await session.delete(p)
    await session.flush()
    await session.refresh(r, attribute_names=["players"])
    if not r.players:
        r.status, r.end_reason, r.deadline_at = RStatus.CANCELLED, "empty", None
        return await _ok(session, r, "cancelled")
    if r.host_id == user_id:
        r.host_id = r.players[0].user_id
    return await _ok(session, r, "left")


def _enter_next(r: Rumble, now: datetime) -> REvent:
    p = _waiting(r)[0]
    p.in_ring, p.hp = True, rr.RUMBLE_HP
    return REvent("entry", user_id=p.user_id, number=p.entry_order)


def _schedule_entry(r: Rumble, now: datetime) -> None:
    r.next_entry_at = now + timedelta(seconds=get_settings().rumble_entry_interval) if _waiting(r) else None


def _ensure_turn(r: Rumble, now: datetime) -> list[REvent]:
    """Start the next turn if the Rumble is running, nobody is mid-turn and 2+ wrestlers are in the ring."""
    if r.status != RStatus.ACTIVE or r.phase is not None:
        return []
    ring = _ring(r)
    if len(ring) < 2:
        r.active_player_id, r.deadline_at = None, None
        return []
    actor_id = rr.next_actor([(p.entry_order, p.user_id) for p in ring], r.last_actor_order)
    r.last_actor_order = _p(r, actor_id).entry_order
    r.active_player_id, r.phase = actor_id, RPhase.AWAIT_MOVE
    r.turn_number += 1
    r.pending_move = r.pending_target_id = None
    r.deadline_at = now + timedelta(seconds=get_settings().rumble_turn_timeout)
    return [REvent("turn_start", user_id=actor_id)]


def _start(r: Rumble, now: datetime, rng: random.Random) -> list[REvent]:
    for i, uid in enumerate(rr.shuffle_order([p.user_id for p in r.players], rng), start=1):
        _p(r, uid).entry_order = i
    r.status, r.started_at, r.last_actor_order = RStatus.ACTIVE, now, 0
    events = [_enter_next(r, now) for _ in range(min(rr.START_RING, len(r.players)))]
    _schedule_entry(r, now)
    events += _ensure_turn(r, now)
    return events


async def start(session: AsyncSession, rumble_id: int, user_id: int, now: datetime | None = None,
                rng: random.Random | None = None) -> RResult:
    now = now or utcnow()
    r = await _lock(session, rumble_id)
    if r is None or r.status != RStatus.LOBBY:
        return RResult.fail("This Rumble lobby is closed.")
    if user_id != r.host_id and user_id != get_settings().owner_id:
        return RResult.fail("🚫 Only the host can start the Rumble early.")
    need = get_settings().rumble_min_players
    if len(r.players) < need:
        return RResult.fail(f"👥 At least {need} wrestlers are needed (now {len(r.players)}).")
    events = _start(r, now, rng or _rng)
    return await _ok(session, r, "started", events)


async def cancel(session: AsyncSession, rumble_id: int, user_id: int) -> RResult:
    r = await _lock(session, rumble_id)
    if r is None or r.status not in RStatus.OPEN:
        return RResult.fail("There's no open Rumble to cancel.")
    if user_id != r.host_id and user_id != get_settings().owner_id:
        return RResult.fail("🚫 Only the host can cancel the Rumble.")
    r.status, r.end_reason, r.phase, r.deadline_at, r.next_entry_at = RStatus.CANCELLED, "cancelled", None, None, None
    for p in r.players:
        p.is_active = False
    return await _ok(session, r, "cancelled")


# ------------------------------------------------------------------------- turns

def _eliminate(r: Rumble, victim: RumblePlayer, killer: RumblePlayer | None, reason: str,
               finisher_kill: bool = False) -> REvent:
    victim.placement = len(_alive(r))             # still counts the victim: 1st out of 6 finishes 6th
    victim.in_ring, victim.eliminated, victim.is_active = False, True, False
    healed = 0
    if killer is not None:
        killer.eliminations += 1
        before = killer.hp
        killer.hp = rr.heal(killer.hp)
        healed = killer.hp - before
    return REvent("eliminated", user_id=victim.user_id, other_id=killer.user_id if killer else None,
                  healed=healed, finisher_kill=finisher_kill, reason=reason, placement=victim.placement)


def _finish_step(r: Rumble, now: datetime, events: list[REvent]) -> None:
    """After any change: crown a winner, or speed up the next entrance, or start the next turn."""
    r.phase, r.pending_move, r.pending_target_id, r.deadline_at = None, None, None, None
    alive = _alive(r)
    if len(alive) == 1:
        w = alive[0]
        w.in_ring, w.placement = True, 1
        r.status, r.winner_id, r.end_reason, r.completed_at = RStatus.COMPLETED, w.user_id, "last_standing", now
        r.active_player_id = r.next_entry_at = None
        for p in r.players:
            p.is_active = False
        events.append(REvent("winner", user_id=w.user_id))
        return
    if len(alive) == 2 and any(e.kind == "eliminated" for e in events):
        events.append(REvent("final_two"))
    if len(_ring(r)) < 2 and _waiting(r):
        r.next_entry_at = now                      # nobody to fight: the next wrestler walks in right away
    events.extend(_ensure_turn(r, now))


def _resolve(r: Rumble, now: datetime, reversed_: bool, auto: bool, rng: random.Random) -> list[REvent]:
    attacker, defender, move = _p(r, r.active_player_id), _p(r, r.pending_target_id), Move(r.pending_move)
    succeeded = rules.reversal_succeeds(rng) if reversed_ else False
    res = rules.resolve_attack(attacker, defender, move, reversed_, reversal_succeeds=succeeded)
    attacker.damage_dealt += res.damage_to_defender
    defender.damage_dealt += res.damage_to_attacker
    events = [REvent("turn", user_id=attacker.user_id, other_id=defender.user_id, move=move.value,
                     reversed=res.reversed, damage=res.damage_to_defender, reflected=res.damage_to_attacker,
                     hp_attacker=attacker.hp, hp_defender=defender.hp, auto=auto)]
    if defender.hp <= 0:
        events.append(_eliminate(r, defender, attacker, "knocked_out", finisher_kill=move == Move.FINISHER))
    elif attacker.hp <= 0:
        events.append(_eliminate(r, attacker, defender, "reflected"))
    _finish_step(r, now, events)
    return events


def _check(r: Rumble | None, user_id: int, turn: int, phases: tuple[str, ...], who: str = "actor") -> str | None:
    if r is None or r.status != RStatus.ACTIVE:
        return "This Rumble is not active."
    if turn != r.turn_number:
        return "⌛ That button is outdated."
    if r.phase not in phases:
        return "⌛ Not your moment — that action isn't available now."
    expected = r.active_player_id if who == "actor" else r.pending_target_id
    if user_id != expected:
        return "🚫 It's not your turn." if who == "actor" else "🚫 That attack isn't aimed at you."
    return None


async def submit_move(session: AsyncSession, rumble_id: int, user_id: int, turn: int, move: str) -> RResult:
    r = await _lock(session, rumble_id)
    if (err := _check(r, user_id, turn, (RPhase.AWAIT_MOVE, RPhase.AWAIT_TARGET))):
        return RResult.fail(err)
    me = _p(r, user_id)
    try:
        mv = rules.parse_move(move)
        rules.validate_attack(me, mv)
    except RuleError as exc:
        return RResult.fail(str(exc))
    r.pending_move, r.phase = mv.value, RPhase.AWAIT_TARGET
    me.afk_strikes = 0
    return await _ok(session, r, "move_chosen")


async def back_to_moves(session: AsyncSession, rumble_id: int, user_id: int, turn: int) -> RResult:
    r = await _lock(session, rumble_id)
    if (err := _check(r, user_id, turn, (RPhase.AWAIT_TARGET,))):
        return RResult.fail(err)
    r.pending_move, r.phase = None, RPhase.AWAIT_MOVE
    return await _ok(session, r, "back")


async def submit_target(session: AsyncSession, rumble_id: int, user_id: int, turn: int, target_id: int,
                        now: datetime | None = None) -> RResult:
    now = now or utcnow()
    r = await _lock(session, rumble_id)
    if (err := _check(r, user_id, turn, (RPhase.AWAIT_TARGET,))):
        return RResult.fail(err)
    target = _p(r, target_id)
    if target is None or not target.in_ring or target_id == user_id:
        return RResult.fail("🎯 Pick another wrestler who is in the ring.")
    window = await runtime_settings.get_value(session, "reversal_timeout")
    r.pending_target_id, r.phase = target_id, RPhase.AWAIT_REVERSAL
    r.deadline_at = now + timedelta(seconds=window)
    return await _ok(session, r, "attack_declared")


async def submit_response(session: AsyncSession, rumble_id: int, user_id: int, turn: int, use_reversal: bool,
                          now: datetime | None = None, rng: random.Random | None = None) -> RResult:
    now = now or utcnow()
    r = await _lock(session, rumble_id)
    if (err := _check(r, user_id, turn, (RPhase.AWAIT_REVERSAL,), who="target")):
        return RResult.fail(err)
    if use_reversal:
        try:
            rules.validate_reversal(_p(r, user_id))
        except RuleError as exc:
            return RResult.fail(str(exc))
    events = _resolve(r, now, use_reversal, auto=False, rng=rng or _rng)
    return await _ok(session, r, "turn_resolved", events)


# ------------------------------------------------------------------ deadline loop

async def due_rumble_ids(session: AsyncSession, now: datetime | None = None) -> list[int]:
    now = now or utcnow()
    stmt = select(Rumble.id).where(or_(
        (Rumble.status == RStatus.LOBBY) & (Rumble.deadline_at <= now),
        (Rumble.status == RStatus.ACTIVE) & ((Rumble.deadline_at <= now) | (Rumble.next_entry_at <= now)),
    )).limit(50)
    return list((await session.execute(stmt)).scalars().all())


async def handle_due(session: AsyncSession, rumble_id: int, now: datetime | None = None,
                     rng: random.Random | None = None) -> RResult | None:
    """Process ONE due transition (lobby close, next entrant, idle skip, or reversal window closing)."""
    now = now or utcnow()
    rng = rng or _rng
    r = await _lock(session, rumble_id, skip_locked=True)
    if r is None:
        return None
    if r.status == RStatus.LOBBY and r.deadline_at and r.deadline_at <= now:
        if len(r.players) >= get_settings().rumble_min_players:
            return await _ok(session, r, "started", _start(r, now, rng))
        r.status, r.end_reason, r.deadline_at = RStatus.CANCELLED, "not_enough_players", None
        for p in r.players:
            p.is_active = False
        return await _ok(session, r, "lobby_expired")
    if r.status != RStatus.ACTIVE:
        return None
    if r.next_entry_at and r.next_entry_at <= now:
        if not _waiting(r):
            r.next_entry_at = None
            return None
        events = [_enter_next(r, now)]
        _schedule_entry(r, now)
        events += _ensure_turn(r, now)
        return await _ok(session, r, "entry", events)
    if r.deadline_at and r.deadline_at <= now:
        if r.phase == RPhase.AWAIT_REVERSAL:                 # no Reversal in time: take the hit
            return await _ok(session, r, "turn_resolved", _resolve(r, now, False, auto=True, rng=rng))
        if r.phase in (RPhase.AWAIT_MOVE, RPhase.AWAIT_TARGET):
            actor = _p(r, r.active_player_id)
            actor.afk_strikes += 1
            if actor.afk_strikes >= rr.AFK_LIMIT:
                events = [_eliminate(r, actor, None, "afk")]
            else:
                events = [REvent("skip", user_id=actor.user_id)]
            _finish_step(r, now, events)
            return await _ok(session, r, "turn_skipped", events)
    return None


async def recover_deadlines(session: AsyncSession, now: datetime | None = None,
                            grace_seconds: int | None = None) -> int:
    """After a restart, give everyone a short grace period for Rumble deadlines that lapsed offline."""
    now = now or utcnow()
    grace = get_settings().restart_grace if grace_seconds is None else grace_seconds
    later = now + timedelta(seconds=grace)
    total = 0
    for column in (Rumble.deadline_at, Rumble.next_entry_at):
        res = await session.execute(update(Rumble).where(Rumble.status.in_(RStatus.OPEN), column.is_not(None),
                                                          column < now).values({column.key: later}))
        total += res.rowcount or 0
    return total


# ------------------------------------------------------------- message bookkeeping

async def set_message_id(session: AsyncSession, rumble_id: int, column: str, message_id: int) -> None:
    assert column in ("lobby_message_id", "board_message_id")
    await session.execute(update(Rumble).where(Rumble.id == rumble_id).values({column: message_id}))


async def set_prompt_message(session: AsyncSession, rumble_id: int, user_id: int, message_id: int) -> None:
    await session.execute(update(RumblePlayer).where(
        RumblePlayer.rumble_id == rumble_id, RumblePlayer.user_id == user_id).values(prompt_message_id=message_id))


async def mark_dm_unreachable(session: AsyncSession, user_id: int) -> None:
    await session.execute(update(User).where(User.id == user_id).values(dm_started=False))


# ---------------------------------------------------------------------- statistics

async def rumble_leaderboard(session: AsyncSession, limit: int = 10) -> list[tuple[str, int, int, int]]:
    """(name, rumble wins, eliminations, rumbles played) over completed Rumbles."""
    wins = func.sum(case((RumblePlayer.placement == 1, 1), else_=0))
    elims = func.sum(RumblePlayer.eliminations)
    stmt = (select(User.display_name, wins, elims, func.count(RumblePlayer.id))
            .join(RumblePlayer, RumblePlayer.user_id == User.id)
            .join(Rumble, Rumble.id == RumblePlayer.rumble_id)
            .where(Rumble.status == RStatus.COMPLETED)
            .group_by(User.id, User.display_name)
            .order_by(wins.desc(), elims.desc()).limit(limit))
    return [(n, int(w or 0), int(e or 0), int(c)) for n, w, e, c in (await session.execute(stmt)).all()]
