"""Tag Team (2v2) engine — database-backed, no Telegram calls (same contract as rumble_engine).

Lobby: 2 Lions + 2 Tigers. Each team has one wrestler in the ring and one resting on the apron.
Turns alternate between the teams; the team's ring wrestler either attacks the other team's ring
wrestler (who may Reversal) or TAGs the partner in. The apron partner recovers a little HP each turn.
A knocked-out wrestler is replaced by the partner; a team with no wrestlers left loses.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from models import RPhase, RStatus, TagMatch, TagPlayer, User, utcnow
from services import rules, runtime_settings
from services import tag_rules as tr
from services.activity import is_busy
from services.match_engine import Identity, upsert_user
from services.rules import Move, RuleError

_rng = random.Random()
DM_MSG = "📩 Open the bot privately and press Start first — moves are chosen in DM."
TAG = "tag"          # the "move" value of the TAG button: "tag" or "tag<user_id>" to pick a partner


def tag_move(user_id: int) -> str:
    return f"{TAG}{user_id}"


# --------------------------------------------------------------------------- data

@dataclass
class TPlayerSnap:
    user_id: int
    name: str
    team: int
    slot: int
    hp: int
    in_ring: bool
    eliminated: bool
    finishers_left: int
    reversals_left: int
    last_attack: str | None
    tags_made: int
    eliminations: int
    damage_dealt: int
    prompt_message_id: int | None
    dm_started: bool


@dataclass
class TagSnap:
    id: int
    group_chat_id: int
    group_title: str | None
    host_id: int
    team_size: int
    status: str
    phase: str | None
    turn_number: int
    active_team: int | None
    active_player_id: int | None
    pending_move: str | None
    pending_target_id: int | None
    t1_tag_lock: bool
    t2_tag_lock: bool
    deadline_at: datetime | None
    winner_team: int | None
    end_reason: str | None
    lobby_message_id: int | None
    board_message_id: int | None
    players: list[TPlayerSnap]

    def player(self, user_id: int | None) -> TPlayerSnap | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    def team(self, team: int) -> list[TPlayerSnap]:
        return sorted((p for p in self.players if p.team == team), key=lambda p: p.slot)

    def ring(self, team: int) -> TPlayerSnap | None:
        return next((p for p in self.team(team) if p.in_ring and not p.eliminated), None)

    def aprons(self, team: int) -> list[TPlayerSnap]:
        return [p for p in self.team(team) if not p.in_ring and not p.eliminated]

    def apron(self, team: int) -> TPlayerSnap | None:
        resting = self.aprons(team)
        return resting[0] if resting else None

    def alive(self, team: int) -> list[TPlayerSnap]:
        return [p for p in self.team(team) if not p.eliminated]

    def tag_locked(self, team: int) -> bool:
        return self.t1_tag_lock if team == 1 else self.t2_tag_lock

    def can_tag(self, user_id: int) -> bool:
        me = self.player(user_id)
        return bool(me and self.apron(me.team) and not self.tag_locked(me.team))


@dataclass
class TEvent:
    kind: str          # turn_start | turn | tag | eliminated | forced_in | handicap | skip | winner
    user_id: int | None = None
    other_id: int | None = None
    move: str | None = None
    reversed: bool = False
    damage: int = 0
    reflected: int = 0
    hp_attacker: int = 0
    hp_defender: int = 0
    finisher_kill: bool = False
    reason: str = ""
    auto: bool = False
    team: int | None = None


@dataclass
class TResult:
    ok: bool
    kind: str = ""
    error: str | None = None
    match: TagSnap | None = None
    events: list[TEvent] = field(default_factory=list)

    @classmethod
    def fail(cls, message: str) -> "TResult":
        return cls(ok=False, error=message)


# ------------------------------------------------------------------------ helpers

async def _lock(session: AsyncSession, match_id: int, skip_locked: bool = False) -> TagMatch | None:
    stmt = (select(TagMatch).where(TagMatch.id == match_id)
            .with_for_update(skip_locked=skip_locked).execution_options(populate_existing=True))
    return (await session.execute(stmt)).scalar_one_or_none()


def _p(m: TagMatch, user_id: int | None) -> TagPlayer | None:
    return next((p for p in m.players if p.user_id == user_id), None)


def _team(m: TagMatch, team: int) -> list[TagPlayer]:
    return sorted((p for p in m.players if p.team == team), key=lambda p: p.slot)


def _ring(m: TagMatch, team: int) -> TagPlayer | None:
    return next((p for p in _team(m, team) if p.in_ring and not p.eliminated), None)


def _aprons(m: TagMatch, team: int) -> list[TagPlayer]:
    return [p for p in _team(m, team) if not p.in_ring and not p.eliminated]


def _alive(m: TagMatch, team: int) -> list[TagPlayer]:
    return [p for p in _team(m, team) if not p.eliminated]


def _lock_flag(m: TagMatch, team: int) -> bool:
    return m.t1_tag_lock if team == 1 else m.t2_tag_lock


def _set_lock(m: TagMatch, team: int, value: bool) -> None:
    if team == 1:
        m.t1_tag_lock = value
    else:
        m.t2_tag_lock = value


async def snapshot(session: AsyncSession, m: TagMatch) -> TagSnap:
    players = []
    for p in m.players:
        user = await session.get(User, p.user_id)
        players.append(TPlayerSnap(
            user_id=p.user_id, name=user.display_name if user else "Unknown Superstar", team=p.team, slot=p.slot,
            hp=p.hp, in_ring=p.in_ring, eliminated=p.eliminated,
            finishers_left=rules.finishers_left(p), reversals_left=rules.reversals_left(p),
            last_attack=p.last_attack, tags_made=p.tags_made, eliminations=p.eliminations,
            damage_dealt=p.damage_dealt, prompt_message_id=p.prompt_message_id,
            dm_started=bool(user and user.dm_started)))
    return TagSnap(
        id=m.id, group_chat_id=m.group_chat_id, group_title=m.group_title, host_id=m.host_id,
        team_size=m.team_size, status=m.status,
        phase=m.phase, turn_number=m.turn_number, active_team=m.active_team, active_player_id=m.active_player_id,
        pending_move=m.pending_move, pending_target_id=m.pending_target_id, t1_tag_lock=m.t1_tag_lock,
        t2_tag_lock=m.t2_tag_lock, deadline_at=m.deadline_at, winner_team=m.winner_team, end_reason=m.end_reason,
        lobby_message_id=m.lobby_message_id, board_message_id=m.board_message_id, players=players)


async def get_snapshot(session: AsyncSession, match_id: int) -> TagSnap | None:
    m = await session.get(TagMatch, match_id, populate_existing=True)
    return await snapshot(session, m) if m else None


async def _ok(session: AsyncSession, m: TagMatch, kind: str, events: list[TEvent] | None = None) -> TResult:
    return TResult(True, kind, match=await snapshot(session, m), events=events or [])


async def _busy(session: AsyncSession, user_id: int) -> bool:
    """In a Tag match, a Rumble or an open 1v1 match?"""
    return await is_busy(session, user_id)


async def open_tag_id_in_chat(session: AsyncSession, chat_id: int) -> int | None:
    stmt = select(TagMatch.id).where(TagMatch.group_chat_id == chat_id, TagMatch.status.in_(RStatus.OPEN))
    return (await session.execute(stmt)).scalars().first()


def _free_slot(m: TagMatch, team: int) -> int:
    used = {p.slot for p in m.players if p.team == team}
    return next((i for i in range(m.team_size) if i not in used), len(used))


# ------------------------------------------------------------------------- lobby

async def create_tag(session: AsyncSession, ident: Identity, chat_id: int, title: str | None,
                     team_size: int = tr.TEAM_SIZE, now: datetime | None = None) -> TResult:
    now = now or utcnow()
    if not tr.MIN_TEAM_SIZE <= team_size <= tr.MAX_TEAM_SIZE:
        return TResult.fail(f"Team size must be {tr.MIN_TEAM_SIZE}, 3 or {tr.MAX_TEAM_SIZE} (example: /tag 3).")
    user = await upsert_user(session, ident)
    if not user.dm_started:
        return TResult.fail(DM_MSG)
    if await open_tag_id_in_chat(session, chat_id) is not None:
        return TResult.fail("🏟️ A Tag Team match is already open in this group.")
    if await _busy(session, ident.id):
        return TResult.fail("🥊 You're already in a match, Rumble or Tag Team. Finish it first.")
    m = TagMatch(group_chat_id=chat_id, group_title=(title or None) and title[:256], host_id=ident.id,
                 team_size=team_size, status=RStatus.LOBBY, created_at=now,
                 deadline_at=now + timedelta(seconds=get_settings().rumble_lobby_timeout),
                 players=[TagPlayer(user_id=ident.id, team=1, slot=0, hp=rules.MAX_HP)])
    try:
        async with session.begin_nested():
            session.add(m)
            await session.flush()
    except IntegrityError:
        return TResult.fail("🥊 You're already in a match.")
    return await _ok(session, m, "created")


async def join(session: AsyncSession, match_id: int, ident: Identity, chat_id: int, team: int) -> TResult:
    m = await _lock(session, match_id)
    if m is None or m.status != RStatus.LOBBY or m.group_chat_id != chat_id or team not in (1, 2):
        return TResult.fail("This Tag Team lobby is closed.")
    user = await upsert_user(session, ident)
    me = _p(m, ident.id)
    label = f"{tr.TEAM_EMOJI[team]} Team {tr.TEAMS[team].title()}"
    if me is not None:
        if me.team == team:
            return TResult.fail("You're already on that team!")
        if len(_team(m, team)) >= m.team_size:
            return TResult.fail(f"🚫 {label} is full.")
        me.team, me.slot = team, _free_slot(m, team)
        await session.flush()
        return await _ok(session, m, "switched")
    if not user.dm_started:
        return TResult.fail(DM_MSG)
    if len(_team(m, team)) >= m.team_size:
        return TResult.fail(f"🚫 {label} is full.")
    if await _busy(session, ident.id):
        return TResult.fail("🥊 You're already in a match, Rumble or Tag Team.")
    try:
        async with session.begin_nested():
            session.add(TagPlayer(match_id=m.id, user_id=ident.id, team=team, slot=_free_slot(m, team),
                                  hp=rules.MAX_HP))
            await session.flush()
    except IntegrityError:
        return TResult.fail("🥊 You're already in a match.")
    await session.refresh(m, attribute_names=["players"])
    return await _ok(session, m, "joined")


async def leave(session: AsyncSession, match_id: int, user_id: int) -> TResult:
    m = await _lock(session, match_id)
    if m is None or m.status != RStatus.LOBBY:
        return TResult.fail("This Tag Team lobby is closed.")
    p = _p(m, user_id)
    if p is None:
        return TResult.fail("You're not in this match.")
    await session.delete(p)
    await session.flush()
    await session.refresh(m, attribute_names=["players"])
    if not m.players:
        m.status, m.end_reason, m.deadline_at = RStatus.CANCELLED, "empty", None
        return await _ok(session, m, "cancelled")
    if m.host_id == user_id:
        m.host_id = m.players[0].user_id
    return await _ok(session, m, "left")


def _startable(m: TagMatch) -> bool:
    return tr.valid_teams(len(_team(m, 1)), len(_team(m, 2)))


def _begin_turn(m: TagMatch, now: datetime, team: int) -> list[TEvent]:
    actor = _ring(m, team)
    m.active_team, m.active_player_id, m.phase = team, actor.user_id, RPhase.AWAIT_MOVE
    m.turn_number += 1
    m.pending_move = m.pending_target_id = None
    m.deadline_at = now + timedelta(seconds=get_settings().rumble_turn_timeout)
    return [TEvent("turn_start", user_id=actor.user_id, team=team)]


def _start(m: TagMatch, now: datetime, rng: random.Random) -> list[TEvent]:
    sizes = {1: len(_team(m, 1)), 2: len(_team(m, 2))}
    bonus = tr.compensation(sizes[1], sizes[2])
    smaller = 1 if sizes[1] < sizes[2] else 2
    for team in (1, 2):
        for i, p in enumerate(_team(m, team)):
            p.slot, p.in_ring = i, i == 0
            p.bonus_reversals = bonus if team == smaller else 0     # the smaller team gets an extra Reversal each
    m.status, m.started_at = RStatus.ACTIVE, now
    return _begin_turn(m, now, rng.choice([1, 2]))


async def start(session: AsyncSession, match_id: int, user_id: int, now: datetime | None = None,
                rng: random.Random | None = None) -> TResult:
    now = now or utcnow()
    m = await _lock(session, match_id)
    if m is None or m.status != RStatus.LOBBY:
        return TResult.fail("This Tag Team lobby is closed.")
    if user_id != m.host_id and user_id != get_settings().owner_id:
        return TResult.fail("🚫 Only the host can start the match.")
    if not _startable(m):
        return TResult.fail("👥 Each team needs at least 2 wrestlers, and the teams can differ by one player at most.")
    return await _ok(session, m, "started", _start(m, now, rng or _rng))


async def cancel(session: AsyncSession, match_id: int, user_id: int) -> TResult:
    m = await _lock(session, match_id)
    if m is None or m.status not in RStatus.OPEN:
        return TResult.fail("There's no open Tag Team match to cancel.")
    if user_id != m.host_id and user_id != get_settings().owner_id:
        return TResult.fail("🚫 Only the host can cancel the match.")
    m.status, m.end_reason, m.phase, m.deadline_at = RStatus.CANCELLED, "cancelled", None, None
    for p in m.players:
        p.is_active = False
    return await _ok(session, m, "cancelled")


# ------------------------------------------------------------------------- turns

def _eliminate(m: TagMatch, victim: TagPlayer, killer: TagPlayer | None, reason: str,
               finisher_kill: bool = False) -> list[TEvent]:
    victim.in_ring, victim.eliminated, victim.is_active = False, True, False
    if killer is not None:
        killer.eliminations += 1
    events = [TEvent("eliminated", user_id=victim.user_id, other_id=killer.user_id if killer else None,
                     reason=reason, finisher_kill=finisher_kill, team=victim.team)]
    resting = _aprons(m, victim.team)
    if resting:                                          # the healthiest partner is dragged into the ring
        partner = max(resting, key=lambda p: p.hp)
        partner.in_ring = True
        events.append(TEvent("forced_in", user_id=partner.user_id, team=victim.team))
    if len(_alive(m, victim.team)) == 1 and len(_alive(m, tr.other_team(victim.team))) >= 2:
        events.append(TEvent("handicap", team=victim.team))
    return events


def _finish_step(m: TagMatch, now: datetime, acting_team: int, events: list[TEvent]) -> None:
    """Crown a winner, or let the apron partners rest and pass the turn to the other team."""
    m.phase, m.pending_move, m.pending_target_id, m.deadline_at = None, None, None, None
    for team in (1, 2):
        if not _alive(m, team):
            winner = tr.other_team(team)
            m.status, m.winner_team, m.end_reason, m.completed_at = RStatus.COMPLETED, winner, "knockout", now
            m.active_team = m.active_player_id = None
            for p in m.players:
                p.is_active = False
            events.append(TEvent("winner", team=winner))
            return
    for team in (1, 2):
        for resting in _aprons(m, team):
            resting.hp = tr.rest(resting.hp, rules.MAX_HP)
    events.extend(_begin_turn(m, now, tr.other_team(acting_team)))


def _resolve(m: TagMatch, now: datetime, reversed_: bool, auto: bool, rng: random.Random) -> list[TEvent]:
    attacker, defender, move = _p(m, m.active_player_id), _p(m, m.pending_target_id), Move(m.pending_move)
    acting_team = attacker.team
    succeeded = rules.reversal_succeeds(rng) if reversed_ else False
    res = rules.resolve_attack(attacker, defender, move, reversed_, reversal_succeeds=succeeded)
    attacker.damage_dealt += res.damage_to_defender
    defender.damage_dealt += res.damage_to_attacker
    events = [TEvent("turn", user_id=attacker.user_id, other_id=defender.user_id, move=move.value,
                     reversed=res.reversed, damage=res.damage_to_defender, reflected=res.damage_to_attacker,
                     hp_attacker=attacker.hp, hp_defender=defender.hp, auto=auto, team=acting_team)]
    if defender.hp <= 0:
        events += _eliminate(m, defender, attacker, "knocked_out", finisher_kill=move == Move.FINISHER)
    elif attacker.hp <= 0:
        events += _eliminate(m, attacker, defender, "reflected")
    _finish_step(m, now, acting_team, events)
    return events


def _check(m: TagMatch | None, user_id: int, turn: int, phase: str, who: str = "actor") -> str | None:
    if m is None or m.status != RStatus.ACTIVE:
        return "This match is not active."
    if turn != m.turn_number:
        return "⌛ That button is outdated."
    if m.phase != phase:
        return "⌛ That action isn't available right now."
    expected = m.active_player_id if who == "actor" else m.pending_target_id
    if user_id != expected:
        return "🚫 It's not your turn." if who == "actor" else "🚫 That attack isn't aimed at you."
    return None


async def submit_move(session: AsyncSession, match_id: int, user_id: int, turn: int, move: str,
                      now: datetime | None = None) -> TResult:
    """An attack (declared, then the defender answers) or the TAG button (resolved immediately)."""
    now = now or utcnow()
    m = await _lock(session, match_id)
    if (err := _check(m, user_id, turn, RPhase.AWAIT_MOVE)):
        return TResult.fail(err)
    me = _p(m, user_id)
    me.afk_strikes = 0
    if move.startswith(TAG):
        resting = _aprons(m, me.team)
        if not resting:
            return TResult.fail("🚫 You have no partner left to tag.")
        wanted = move[len(TAG):]
        if wanted:
            partner = next((p for p in resting if str(p.user_id) == wanted), None)
            if partner is None:
                return TResult.fail("🚫 That partner can't be tagged in.")
        elif len(resting) == 1:
            partner = resting[0]
        else:
            return TResult.fail("👥 Pick which partner to tag in.")
        if _lock_flag(m, me.team):
            return TResult.fail("🚫 You can't tag twice in a row — attack first!")
        me.in_ring, partner.in_ring = False, True
        me.tags_made += 1
        _set_lock(m, me.team, True)
        events = [TEvent("tag", user_id=me.user_id, other_id=partner.user_id, team=me.team)]
        _finish_step(m, now, me.team, events)
        return await _ok(session, m, "turn_resolved", events)
    try:
        mv = rules.parse_move(move)
        rules.validate_attack(me, mv)
    except RuleError as exc:
        return TResult.fail(str(exc))
    target = _ring(m, tr.other_team(me.team))
    window = await runtime_settings.get_value(session, "reversal_timeout")
    m.pending_move, m.pending_target_id, m.phase = mv.value, target.user_id, RPhase.AWAIT_REVERSAL
    m.deadline_at = now + timedelta(seconds=window)
    _set_lock(m, me.team, False)                         # attacking re-enables tagging
    return await _ok(session, m, "attack_declared")


async def submit_response(session: AsyncSession, match_id: int, user_id: int, turn: int, use_reversal: bool,
                          now: datetime | None = None, rng: random.Random | None = None) -> TResult:
    now = now or utcnow()
    m = await _lock(session, match_id)
    if (err := _check(m, user_id, turn, RPhase.AWAIT_REVERSAL, who="target")):
        return TResult.fail(err)
    if use_reversal:
        try:
            rules.validate_reversal(_p(m, user_id))
        except RuleError as exc:
            return TResult.fail(str(exc))
    events = _resolve(m, now, use_reversal, auto=False, rng=rng or _rng)
    return await _ok(session, m, "turn_resolved", events)


# ------------------------------------------------------------------ deadline loop

async def due_tag_ids(session: AsyncSession, now: datetime | None = None) -> list[int]:
    now = now or utcnow()
    stmt = select(TagMatch.id).where(
        TagMatch.status.in_(RStatus.OPEN), TagMatch.deadline_at <= now).limit(50)
    return list((await session.execute(stmt)).scalars().all())


async def handle_due(session: AsyncSession, match_id: int, now: datetime | None = None,
                     rng: random.Random | None = None) -> TResult | None:
    now = now or utcnow()
    rng = rng or _rng
    m = await _lock(session, match_id, skip_locked=True)
    if m is None or m.deadline_at is None or m.deadline_at > now:
        return None
    if m.status == RStatus.LOBBY:
        if _startable(m):
            return await _ok(session, m, "started", _start(m, now, rng))
        m.status, m.end_reason, m.deadline_at = RStatus.CANCELLED, "not_enough_players", None
        for p in m.players:
            p.is_active = False
        return await _ok(session, m, "lobby_expired")
    if m.status != RStatus.ACTIVE:
        return None
    if m.phase == RPhase.AWAIT_REVERSAL:                    # no Reversal in time: take the hit
        return await _ok(session, m, "turn_resolved", _resolve(m, now, False, auto=True, rng=rng))
    if m.phase == RPhase.AWAIT_MOVE:
        actor = _p(m, m.active_player_id)
        team = actor.team
        actor.afk_strikes += 1
        if actor.afk_strikes >= tr.AFK_LIMIT:
            events = _eliminate(m, actor, None, "afk")
        else:
            events = [TEvent("skip", user_id=actor.user_id, team=team)]
        _finish_step(m, now, team, events)
        return await _ok(session, m, "turn_skipped", events)
    return None


async def recover_deadlines(session: AsyncSession, now: datetime | None = None,
                            grace_seconds: int | None = None) -> int:
    now = now or utcnow()
    grace = get_settings().restart_grace if grace_seconds is None else grace_seconds
    res = await session.execute(update(TagMatch).where(
        TagMatch.status.in_(RStatus.OPEN), TagMatch.deadline_at.is_not(None), TagMatch.deadline_at < now
    ).values(deadline_at=now + timedelta(seconds=grace)))
    return res.rowcount or 0


# ------------------------------------------------------------- message bookkeeping

async def set_message_id(session: AsyncSession, match_id: int, column: str, message_id: int) -> None:
    assert column in ("lobby_message_id", "board_message_id")
    await session.execute(update(TagMatch).where(TagMatch.id == match_id).values({column: message_id}))


async def set_prompt_message(session: AsyncSession, match_id: int, user_id: int, message_id: int) -> None:
    await session.execute(update(TagPlayer).where(
        TagPlayer.match_id == match_id, TagPlayer.user_id == user_id).values(prompt_message_id=message_id))


async def mark_dm_unreachable(session: AsyncSession, user_id: int) -> None:
    await session.execute(update(User).where(User.id == user_id).values(dm_started=False))


async def tag_leaderboard(session: AsyncSession, limit: int = 10) -> list[tuple[str, int, int, int]]:
    """(name, tag wins, tag matches played, eliminations) over completed Tag Team matches."""
    wins = func.sum(case((TagPlayer.team == TagMatch.winner_team, 1), else_=0))
    stmt = (select(User.display_name, wins, func.count(TagPlayer.id), func.sum(TagPlayer.eliminations))
            .join(TagPlayer, TagPlayer.user_id == User.id)
            .join(TagMatch, TagMatch.id == TagPlayer.match_id)
            .where(TagMatch.status == RStatus.COMPLETED)
            .group_by(User.id, User.display_name)
            .order_by(wins.desc(), func.sum(TagPlayer.eliminations).desc()).limit(limit))
    return [(n, int(w or 0), int(c), int(e or 0)) for n, w, c, e in (await session.execute(stmt)).all()]
