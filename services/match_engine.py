"""Database-backed match engine.

Every public function takes an open ``AsyncSession`` (inside a transaction) and
returns a ``Result``. No Telegram calls happen here; the caller publishes events
*after* the transaction commits. Match rows are locked with ``SELECT ... FOR UPDATE``
and every transition re-checks status / phase / turn number, so a turn can only
be resolved once even when callbacks arrive simultaneously. A unique constraint on
(match_id, turn_number) is the final safety net.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from models import Match, MatchPlayer, MatchStatus as S, MatchTurn, Phase, User, utcnow
from services import rules, runtime_settings
from services.rules import Move, RuleError

_rng = random.Random()


# --------------------------------------------------------------------------- data

@dataclass(frozen=True)
class Identity:
    id: int
    username: str | None
    name: str


@dataclass
class PlayerSnap:
    user_id: int
    name: str
    hp: int
    finishers_left: int
    reversals_left: int
    last_attack: str | None
    prompt_message_id: int | None
    dm_started: bool
    wild_draws_left: int = 0


@dataclass
class MatchSnap:
    id: int
    group_chat_id: int
    group_title: str | None
    status: str
    phase: str | None
    turn_number: int
    active_player_id: int | None
    deadline_at: datetime | None
    winner_id: int | None
    end_reason: str | None
    challenger_id: int
    opponent_id: int | None
    challenge_message_id: int | None
    scoreboard_message_id: int | None
    cancel_requested_by: int | None
    players: list[PlayerSnap]
    fixture_id: int | None = None          # set for tournament fixtures

    def player(self, user_id: int) -> PlayerSnap | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    def other(self, user_id: int) -> PlayerSnap | None:
        return next((p for p in self.players if p.user_id != user_id), None)


@dataclass
class TurnSnap:
    turn_number: int
    attacker_id: int
    defender_id: int
    move: str
    reversed: bool
    original_damage: int
    damage_to_defender: int
    damage_to_attacker: int
    finished: bool
    winner_id: int | None
    auto: bool = False
    summary: rules.MatchSummary | None = None
    reversal_attempted: bool = False      # `reversed` means the attempt succeeded
    wild: bool = False                    # the move came from a Wild Draw


@dataclass
class Result:
    ok: bool
    kind: str = ""
    error: str | None = None
    match: MatchSnap | None = None
    turn: TurnSnap | None = None
    missing_dm: list[int] = field(default_factory=list)
    move: str | None = None               # the move declared (set for Wild Draw results)

    @classmethod
    def fail(cls, message: str) -> "Result":
        return cls(ok=False, error=message)


# ------------------------------------------------------------------------ helpers

async def _lock_match(session: AsyncSession, match_id: int, skip_locked: bool = False) -> Match | None:
    stmt = (select(Match).where(Match.id == match_id)
            .with_for_update(skip_locked=skip_locked)
            .execution_options(populate_existing=True))
    return (await session.execute(stmt)).scalar_one_or_none()


async def _lock_user(session: AsyncSession, user_id: int) -> None:
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())


def _player(match: Match, user_id: int | None) -> MatchPlayer | None:
    return next((p for p in match.players if p.user_id == user_id), None)


def _other(match: Match, user_id: int) -> MatchPlayer | None:
    return next((p for p in match.players if p.user_id != user_id), None)


async def snapshot(session: AsyncSession, match: Match) -> MatchSnap:
    players = []
    for p in match.players:
        user = await session.get(User, p.user_id)
        players.append(PlayerSnap(
            user_id=p.user_id,
            name=(user.display_name if user else "Unknown Superstar"),
            hp=p.hp,
            finishers_left=rules.finishers_left(p),
            reversals_left=rules.reversals_left(p),
            last_attack=p.last_attack,
            prompt_message_id=p.prompt_message_id,
            dm_started=bool(user and user.dm_started),
            wild_draws_left=rules.wild_draws_left(p),
        ))
    return MatchSnap(
        id=match.id, group_chat_id=match.group_chat_id, group_title=match.group_title,
        status=match.status, phase=match.phase, turn_number=match.turn_number,
        active_player_id=match.active_player_id, deadline_at=match.deadline_at,
        winner_id=match.winner_id, end_reason=match.end_reason,
        challenger_id=match.challenger_id, opponent_id=match.opponent_id,
        challenge_message_id=match.challenge_message_id,
        scoreboard_message_id=match.scoreboard_message_id,
        cancel_requested_by=match.cancel_requested_by, players=players, fixture_id=match.fixture_id)


async def upsert_user(session: AsyncSession, ident: Identity, dm_started: bool | None = None) -> User:
    user = await session.get(User, ident.id)
    if user is None:
        try:
            async with session.begin_nested():
                user = User(id=ident.id, username=ident.username, display_name=ident.name[:128],
                            registered_at=utcnow(), dm_started=bool(dm_started))
                session.add(user)
                await session.flush()
        except IntegrityError:  # concurrent first-contact from another update
            user = await session.get(User, ident.id, populate_existing=True)
    else:
        user.username = ident.username
        user.display_name = ident.name[:128]
    if dm_started:
        user.dm_started = True
    return user


async def mark_dm_unreachable(session: AsyncSession, user_id: int) -> None:
    await session.execute(update(User).where(User.id == user_id).values(dm_started=False))


async def active_match_for(session: AsyncSession, user_id: int) -> Match | None:
    stmt = (select(Match).join(MatchPlayer, MatchPlayer.match_id == Match.id)
            .where(MatchPlayer.user_id == user_id, MatchPlayer.is_active.is_(True),
                   Match.status.in_(S.OPEN)))
    return (await session.execute(stmt)).scalars().first()


async def get_snapshot(session: AsyncSession, match_id: int) -> MatchSnap | None:
    match = (await session.execute(
        select(Match).where(Match.id == match_id).execution_options(populate_existing=True))).scalar_one_or_none()
    return await snapshot(session, match) if match else None


async def active_snapshot_for(session: AsyncSession, user_id: int) -> MatchSnap | None:
    match = await active_match_for(session, user_id)
    return await snapshot(session, match) if match else None


async def _check_creation_allowed(session: AsyncSession) -> str | None:
    if await runtime_settings.get_value(session, "maintenance_mode"):
        return "🛠️ The arena is under maintenance. Please try again soon."
    if not await runtime_settings.get_value(session, "matches_enabled"):
        return "🚧 New matches are currently disabled."
    return None


def _close(match: Match, status: str, reason: str, now: datetime) -> None:
    """Move a match to a terminal state and free both players."""
    match.status = status
    match.end_reason = reason
    match.completed_at = now
    match.phase = None
    match.pending_move = None
    match.pending_wild = False
    match.active_player_id = None
    match.deadline_at = None
    match.cancel_requested_by = None
    for p in match.players:
        p.is_active = False


async def _complete(session: AsyncSession, match: Match, winner_id: int, reason: str,
                    now: datetime) -> rules.MatchSummary:
    """Finish a match with a winner. Called at most once per match (status guard)."""
    await session.flush()
    turns = (await session.execute(select(MatchTurn).where(MatchTurn.match_id == match.id))).scalars().all()
    ids = [p.user_id for p in match.players]
    summary = rules.summarize_turns(turns, ids)
    _close(match, S.COMPLETED, reason, now)
    match.winner_id = winner_id
    for uid in ids:
        user = await session.get(User, uid)
        user.total_matches += 1
        user.damage_dealt += summary.damage_dealt.get(uid, 0)
        user.damage_received += summary.damage_received.get(uid, 0)
        if uid == winner_id:
            user.wins += 1
            if reason == "knockout":
                user.knockouts += 1
        else:
            user.losses += 1
    await session.flush()
    return summary


async def _activate(session: AsyncSession, match: Match, now: datetime, rng: random.Random) -> None:
    turn_timeout = await runtime_settings.get_value(session, "turn_timeout")
    match.status = S.ACTIVE
    match.phase = Phase.AWAIT_ATTACK
    match.turn_number = 1
    match.active_player_id = rng.choice([p.user_id for p in match.players])
    match.deadline_at = now + timedelta(seconds=turn_timeout)


# ---------------------------------------------------------------------- challenge

async def create_challenge(session: AsyncSession, ident: Identity, chat_id: int,
                           chat_title: str | None, now: datetime | None = None) -> Result:
    now = now or utcnow()
    await upsert_user(session, ident)
    await _lock_user(session, ident.id)          # serialises double-taps of /match
    blocked = await _check_creation_allowed(session)
    if blocked:
        return Result.fail(blocked)
    if await active_match_for(session, ident.id):
        return Result.fail("⚠️ You're already in a match or have an open challenge. Finish it or use /cancelmatch.")
    timeout = await runtime_settings.get_value(session, "challenge_timeout")
    match = Match(group_chat_id=chat_id, group_title=(chat_title or "")[:256] or None,
                  challenger_id=ident.id, status=S.PENDING, created_at=now,
                  deadline_at=now + timedelta(seconds=timeout),
                  players=[MatchPlayer(user_id=ident.id)])
    try:
        async with session.begin_nested():
            session.add(match)
            await session.flush()
    except IntegrityError:
        return Result.fail("⚠️ You're already in a match or have an open challenge.")
    return Result(True, "challenge_created", match=await snapshot(session, match))


async def accept_challenge(session: AsyncSession, match_id: int, ident: Identity, chat_id: int,
                           now: datetime | None = None, rng: random.Random | None = None) -> Result:
    now = now or utcnow()
    rng = rng or _rng
    match = await _lock_match(session, match_id)
    if match is None or match.status != S.PENDING or match.group_chat_id != chat_id:
        return Result.fail("This challenge is no longer available.")
    if match.deadline_at and match.deadline_at <= now:
        return Result.fail("⏳ This challenge has expired.")
    if ident.id == match.challenger_id:
        return Result.fail("🚫 You can't accept your own challenge.")
    await upsert_user(session, ident)
    await _lock_user(session, ident.id)
    if await active_match_for(session, ident.id):
        return Result.fail("⚠️ You're already in a match. Finish it before accepting another.")
    blocked = await _check_creation_allowed(session)
    if blocked:
        return Result.fail(blocked)
    try:
        async with session.begin_nested():
            match.players.append(MatchPlayer(user_id=ident.id))
            match.opponent_id = ident.id
            await session.flush()
    except IntegrityError:
        return Result.fail("⚠️ You're already in a match.")

    snap_users = [await session.get(User, p.user_id) for p in match.players]
    missing = [u.id for u in snap_users if not u.dm_started]
    if missing:
        match.status = S.WAITING_DM
        match.deadline_at = now + timedelta(seconds=get_settings().dm_setup_timeout)
    else:
        await _activate(session, match, now, rng)
    await session.flush()
    return Result(True, "accepted", match=await snapshot(session, match), missing_dm=missing)


async def create_tournament_match(session: AsyncSession, chat_id: int, chat_title: str | None, p1_id: int,
                                  p2_id: int, fixture_id: int, first_turn_seconds: int,
                                  now: datetime | None = None, rng: random.Random | None = None) -> Result:
    """Start a tournament fixture directly as an ACTIVE match (no challenge/accept step).

    Fails (``Result.fail``) when a fighter has not pressed Start in DM or is busy in another open match;
    the tournament manager retries until the no-show window runs out.
    """
    now = now or utcnow()
    rng = rng or _rng
    for uid in sorted((p1_id, p2_id)):
        await _lock_user(session, uid)
    for uid in (p1_id, p2_id):
        user = await session.get(User, uid)
        if user is None or not user.dm_started:
            return Result.fail(f"dm:{uid}")
    for uid in (p1_id, p2_id):
        if await active_match_for(session, uid):
            return Result.fail(f"busy:{uid}")
    match = Match(group_chat_id=chat_id, group_title=(chat_title or "")[:256] or None,
                  challenger_id=p1_id, opponent_id=p2_id, status=S.PENDING, created_at=now,
                  fixture_id=fixture_id,
                  players=[MatchPlayer(user_id=p1_id), MatchPlayer(user_id=p2_id)])
    try:
        async with session.begin_nested():
            session.add(match)
            await session.flush()
    except IntegrityError:
        return Result.fail("busy")
    await _activate(session, match, now, rng)
    match.deadline_at = now + timedelta(seconds=first_turn_seconds)      # no-show window for the first move
    await session.flush()
    return Result(True, "tournament_started", match=await snapshot(session, match))


async def cancel_tournament_match(session: AsyncSession, match_id: int, now: datetime | None = None) -> MatchSnap | None:
    """Owner override: close a running tournament match without a result (the tournament decides the winner)."""
    now = now or utcnow()
    match = await _lock_match(session, match_id)
    if match is None or match.status not in S.OPEN:
        return None
    _close(match, S.CANCELLED, "tournament_override", now)
    await session.flush()
    return await snapshot(session, match)


async def try_start_waiting(session: AsyncSession, user_id: int, now: datetime | None = None,
                            rng: random.Random | None = None) -> list[Result]:
    """Called when a user presses Start in DM: activate matches that were waiting for them."""
    now = now or utcnow()
    rng = rng or _rng
    ids = (await session.execute(
        select(Match.id).join(MatchPlayer, MatchPlayer.match_id == Match.id)
        .where(MatchPlayer.user_id == user_id, Match.status == S.WAITING_DM))).scalars().all()
    results = []
    for mid in ids:
        match = await _lock_match(session, mid)
        if match is None or match.status != S.WAITING_DM:
            continue
        users = [await session.get(User, p.user_id) for p in match.players]
        missing = [u.id for u in users if not u.dm_started]
        if missing:
            results.append(Result(True, "still_waiting", match=await snapshot(session, match), missing_dm=missing))
        else:
            await _activate(session, match, now, rng)
            await session.flush()
            results.append(Result(True, "started", match=await snapshot(session, match)))
    return results


# -------------------------------------------------------------------------- turns

def _validate_actor(match: Match | None, user_id: int, turn_number: int, phase: str) -> str | None:
    if match is None:
        return "This match no longer exists."
    if _player(match, user_id) is None:
        return "🚫 You are not a fighter in this match."
    if match.status == S.PAUSED:
        return "⏸️ The match is paused."
    if match.status != S.ACTIVE:
        return "This match is over."
    if match.turn_number != turn_number or match.phase != phase:
        return "⌛ That prompt is outdated."
    return None


async def submit_attack(session: AsyncSession, match_id: int, user_id: int, turn_number: int,
                        move_value: str, now: datetime | None = None) -> Result:
    now = now or utcnow()
    match = await _lock_match(session, match_id)
    err = _validate_actor(match, user_id, turn_number, Phase.AWAIT_ATTACK)
    if err:
        return Result.fail(err)
    if match.active_player_id != user_id:
        return Result.fail("⏳ It's not your turn.")
    attacker, defender = _player(match, user_id), _other(match, user_id)
    try:
        move = rules.parse_move(move_value)
        rules.validate_attack(attacker, move)
    except RuleError as exc:
        return Result.fail(str(exc))
    return await _declare_attack(session, match, defender, move, now, wild=False)


async def submit_wild_draw(session: AsyncSession, match_id: int, user_id: int, turn_number: int,
                           now: datetime | None = None, rng: random.Random | None = None) -> Result:
    """Spend the Wild Draw card: the result is a random move (weighted), or a bonus Reversal.

    * attack result: it is declared right away as this turn's attack and skips the
      no-consecutive-repeat rule (the opponent still gets their Reversal window)
    * Reversal result: the player gains one extra Reversal charge and still picks an attack this turn
    """
    now = now or utcnow()
    rng = rng or _rng
    match = await _lock_match(session, match_id)
    err = _validate_actor(match, user_id, turn_number, Phase.AWAIT_ATTACK)
    if err:
        return Result.fail(err)
    if match.active_player_id != user_id:
        return Result.fail("⏳ It's not your turn.")
    attacker, defender = _player(match, user_id), _other(match, user_id)
    try:
        rules.validate_wild_draw(attacker)
    except RuleError as exc:
        return Result.fail(str(exc))

    drawn = rules.draw_wild(attacker, rng)
    attacker.wild_draws_used += 1
    if drawn == Move.REVERSAL:
        attacker.bonus_reversals += 1
        match.deadline_at = now + timedelta(seconds=await runtime_settings.get_value(session, "turn_timeout"))
        await session.flush()
        return Result(True, "wild_reversal", match=await snapshot(session, match), move=drawn.value)
    return await _declare_attack(session, match, defender, drawn, now, wild=True)


async def _declare_attack(session: AsyncSession, match: Match, defender: MatchPlayer, move: Move,
                          now: datetime, wild: bool) -> Result:
    """Shared tail of a normal attack and a Wild Draw attack (the move is already validated)."""
    match.pending_move = move.value
    match.pending_wild = wild
    if rules.reversals_left(defender) <= 0:            # no response window needed
        turn = await _resolve_pending(session, match, reversed_=False, now=now)
        return Result(True, "turn_resolved", match=await snapshot(session, match), turn=turn, move=move.value)

    window = await runtime_settings.get_value(session, "reversal_timeout")
    match.phase = Phase.AWAIT_REVERSAL
    match.deadline_at = now + timedelta(seconds=window)
    await session.flush()
    return Result(True, "attack_declared", match=await snapshot(session, match), move=move.value)


async def submit_response(session: AsyncSession, match_id: int, user_id: int, turn_number: int,
                          use_reversal: bool, now: datetime | None = None,
                          rng: random.Random | None = None) -> Result:
    """The defender answers an incoming attack. A Reversal is a gamble: see ``rules.REVERSAL_CHANCE``."""
    now = now or utcnow()
    rng = rng or _rng
    match = await _lock_match(session, match_id)
    err = _validate_actor(match, user_id, turn_number, Phase.AWAIT_REVERSAL)
    if err:
        return Result.fail(err)
    if match.active_player_id == user_id:
        return Result.fail("🚫 You can't respond to your own attack.")
    if use_reversal:
        try:
            rules.validate_reversal(_player(match, user_id))
        except RuleError as exc:
            return Result.fail(str(exc))
    turn = await _resolve_pending(session, match, reversed_=use_reversal, now=now, rng=rng)
    return Result(True, "turn_resolved", match=await snapshot(session, match), turn=turn)


async def _resolve_pending(session: AsyncSession, match: Match, reversed_: bool, now: datetime,
                           auto: bool = False, rng: random.Random | None = None) -> TurnSnap:
    """Resolve the pending attack. Callers must hold the match lock and have validated phase.

    ``reversed_`` means the defender *attempted* a Reversal; the 50/50 roll happens here, once.
    """
    rng = rng or _rng
    attacker = _player(match, match.active_player_id)
    defender = _other(match, attacker.user_id)
    move = Move(match.pending_move)
    wild = bool(match.pending_wild)
    success = rules.reversal_succeeds(rng) if reversed_ else False
    res = rules.resolve_attack(attacker, defender, move, reversed_, reversal_succeeds=success, wild=wild)
    turn_number = match.turn_number
    session.add(MatchTurn(
        match_id=match.id, turn_number=turn_number, attacker_id=attacker.user_id,
        target_id=defender.user_id, move=move.value, original_damage=res.original_damage,
        reversed=res.reversed, reversal_attempted=res.reversal_attempted, wild=wild,
        damage_to_target=res.damage_to_defender,
        damage_to_attacker=res.damage_to_attacker, attacker_hp_after=attacker.hp,
        target_hp_after=defender.hp, created_at=now))
    match.pending_move = None
    match.pending_wild = False
    summary = None
    if res.winner_id is not None:
        summary = await _complete(session, match, res.winner_id, "knockout", now)
    else:
        turn_timeout = await runtime_settings.get_value(session, "turn_timeout")
        match.phase = Phase.AWAIT_ATTACK
        match.turn_number = turn_number + 1
        match.active_player_id = defender.user_id      # the turn always passes, Reversal or not
        match.deadline_at = now + timedelta(seconds=turn_timeout)
        await session.flush()
    return TurnSnap(
        turn_number=turn_number, attacker_id=attacker.user_id, defender_id=defender.user_id,
        move=move.value, reversed=res.reversed, original_damage=res.original_damage,
        damage_to_defender=res.damage_to_defender, damage_to_attacker=res.damage_to_attacker,
        finished=res.winner_id is not None, winner_id=res.winner_id, auto=auto, summary=summary,
        reversal_attempted=res.reversal_attempted, wild=wild)


# ---------------------------------------------------------------- pause / cancel

async def resume_match(session: AsyncSession, match_id: int, user_id: int,
                       now: datetime | None = None) -> Result:
    now = now or utcnow()
    match = await _lock_match(session, match_id)
    if match is None or _player(match, user_id) is None:
        return Result.fail("🚫 You are not a fighter in this match.")
    if match.status != S.PAUSED:
        return Result.fail("This match isn't paused.")
    match.status = S.ACTIVE
    match.phase = Phase.AWAIT_ATTACK
    match.deadline_at = now + timedelta(seconds=await runtime_settings.get_value(session, "turn_timeout"))
    await session.flush()
    return Result(True, "resumed", match=await snapshot(session, match))


async def cancel_command(session: AsyncSession, user_id: int, now: datetime | None = None) -> Result:
    """/cancelmatch: cancel a pending challenge directly; request mutual cancellation otherwise."""
    now = now or utcnow()
    current = await active_match_for(session, user_id)
    if current is None:
        return Result.fail("You have no open challenge or match to cancel.")
    match = await _lock_match(session, current.id)
    if match is None or match.status not in S.OPEN or _player(match, user_id) is None:
        return Result.fail("You have no open challenge or match to cancel.")
    if match.fixture_id:
        return Result.fail("🏆 Tournament matches can't be cancelled. Ask the tournament owner.")
    if match.status in (S.PENDING, S.WAITING_DM):
        _close(match, S.CANCELLED, "cancelled_by_player", now)
        await session.flush()
        return Result(True, "cancelled", match=await snapshot(session, match))
    if match.cancel_requested_by == user_id:
        return Result.fail("⏳ You already requested a cancellation. Waiting for your opponent.")
    if match.cancel_requested_by is not None:      # opponent already asked: both agree
        _close(match, S.CANCELLED, "mutual_cancel", now)
        await session.flush()
        return Result(True, "cancelled", match=await snapshot(session, match))
    match.cancel_requested_by = user_id
    await session.flush()
    return Result(True, "cancel_requested", match=await snapshot(session, match))


async def respond_cancel(session: AsyncSession, match_id: int, user_id: int, agree: bool,
                         now: datetime | None = None) -> Result:
    now = now or utcnow()
    match = await _lock_match(session, match_id)
    if match is None or _player(match, user_id) is None:
        return Result.fail("🚫 You are not a fighter in this match.")
    if match.fixture_id:
        return Result.fail("🏆 Tournament matches can't be cancelled. Ask the tournament owner.")
    if match.status not in (S.ACTIVE, S.PAUSED) or match.cancel_requested_by in (None, user_id):
        return Result.fail("There is no cancellation request to answer.")
    if agree:
        _close(match, S.CANCELLED, "mutual_cancel", now)
        kind = "cancelled"
    else:
        match.cancel_requested_by = None
        kind = "cancel_declined"
    await session.flush()
    return Result(True, kind, match=await snapshot(session, match))


# ----------------------------------------------------------------------- deadlines

async def due_match_ids(session: AsyncSession, now: datetime | None = None) -> list[int]:
    now = now or utcnow()
    stmt = select(Match.id).where(Match.status.in_(S.OPEN), Match.deadline_at.is_not(None),
                                  Match.deadline_at <= now).order_by(Match.deadline_at)
    return list((await session.execute(stmt)).scalars().all())


async def handle_due_match(session: AsyncSession, match_id: int, now: datetime | None = None) -> Result | None:
    """Process one expired deadline. Safe to call repeatedly: stale/duplicate calls return None."""
    now = now or utcnow()
    match = await _lock_match(session, match_id, skip_locked=True)
    if match is None or match.status not in S.OPEN or match.deadline_at is None or match.deadline_at > now:
        return None

    if match.status == S.PENDING:
        _close(match, S.EXPIRED, "challenge_expired", now)
        await session.flush()
        return Result(True, "challenge_expired", match=await snapshot(session, match))
    if match.status == S.WAITING_DM:
        _close(match, S.CANCELLED, "dm_setup_timeout", now)
        await session.flush()
        return Result(True, "dm_timeout", match=await snapshot(session, match))
    if match.status == S.PAUSED:
        _close(match, S.CANCELLED, "pause_expired", now)
        await session.flush()
        return Result(True, "pause_expired", match=await snapshot(session, match))

    if match.phase == Phase.AWAIT_REVERSAL:        # no Reversal in time: normal damage
        turn = await _resolve_pending(session, match, reversed_=False, now=now, auto=True)
        return Result(True, "turn_resolved", match=await snapshot(session, match), turn=turn)

    # attacker did not choose a move in time -> configured policy
    policy = "forfeit" if match.fixture_id else await runtime_settings.get_value(session, "timeout_policy")
    idle_id = match.active_player_id
    if policy == "forfeit":
        other = _other(match, idle_id)
        summary = await _complete(session, match, other.user_id, "timeout", now)
        snap = await snapshot(session, match)
        turn = TurnSnap(match.turn_number, other.user_id, idle_id, "", False, 0, 0, 0, True,
                        other.user_id, True, summary)
        return Result(True, "forfeit", match=snap, turn=turn)
    if policy == "cancel":
        _close(match, S.CANCELLED, "turn_timeout", now)
        await session.flush()
        return Result(True, "timeout_cancelled", match=await snapshot(session, match))
    match.status = S.PAUSED
    match.deadline_at = now + timedelta(seconds=get_settings().pause_expiry)
    await session.flush()
    snap = await snapshot(session, match)
    snap.active_player_id = idle_id
    return Result(True, "paused", match=snap)


async def recover_deadlines(session: AsyncSession, now: datetime | None = None,
                            grace_seconds: int | None = None) -> int:
    """After a restart, give players a short grace period for deadlines that lapsed while offline."""
    now = now or utcnow()
    grace = get_settings().restart_grace if grace_seconds is None else grace_seconds
    stmt = (update(Match)
            .where(Match.status.in_((S.PENDING, S.WAITING_DM, S.ACTIVE)),
                   Match.deadline_at.is_not(None), Match.deadline_at < now)
            .values(deadline_at=now + timedelta(seconds=grace)))
    result = await session.execute(stmt)
    return result.rowcount or 0


# ------------------------------------------------------------- message bookkeeping

async def set_message_id(session: AsyncSession, match_id: int, field_name: str, message_id: int) -> None:
    assert field_name in ("challenge_message_id", "scoreboard_message_id")
    await session.execute(update(Match).where(Match.id == match_id).values({field_name: message_id}))


async def set_prompt_message(session: AsyncSession, match_id: int, user_id: int, message_id: int | None) -> None:
    await session.execute(update(MatchPlayer)
                          .where(MatchPlayer.match_id == match_id, MatchPlayer.user_id == user_id)
                          .values(prompt_message_id=message_id))
