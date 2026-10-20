"""Championship belts: title matches, awarding belts, the title-holder board and the belt cards."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import transaction
from models import Championship, Match, MatchStatus, TagMatch, TitleMatch, User, utcnow
from services import avatars, cards, publisher
from services import tour_rules as tr

log = logging.getLogger(__name__)
BELT_DIR = Path(__file__).resolve().parent.parent / "assets" / "belts"
POLL_SECONDS = 5
CHAMPION_REPLY_HOURS = 24          # a challenger waits this long for the champion to accept


@dataclass(frozen=True)
class Belt:
    key: str
    name: str
    short: str
    kind: str            # solo (1v1 match) | tag (2v2 tag match)
    emoji: str = "🏆"


BELTS: dict[str, Belt] = {b.key: b for b in (
    Belt("undisputed", "Undisputed WWE Championship", "UNDISPUTED", "solo", "👑"),
    Belt("worldhw", "World Heavyweight Championship", "WORLD HEAVYWEIGHT", "solo", "🌍"),
    Belt("ic", "Intercontinental Championship", "INTERCONTINENTAL", "solo", "🏆"),
    Belt("us", "United States Championship", "UNITED STATES", "solo", "🇺🇸"),
    Belt("tag", "World Tag Team Championship", "TAG TEAM", "tag", "🏷️"),
)}


def belt_image(key: str) -> bytes | None:
    for ext in (".png", ".webp", ".jpg", ".jpeg"):
        path = BELT_DIR / f"{key}{ext}"
        if path.is_file():
            return path.read_bytes()
    return None


# --------------------------------------------------------------------------------------- database

async def _row(session: AsyncSession, key: str) -> Championship:
    row = await session.get(Championship, key)
    if row is None:
        row = Championship(key=key, reigns=0, defenses=0)
        session.add(row)
        await session.flush()
    return row


def holders(row: Championship | None) -> list[int]:
    return [h for h in ((row.holder1_id, row.holder2_id) if row else ()) if h]


async def is_contested(session: AsyncSession, key: str) -> bool:
    return (await session.execute(select(TitleMatch.id).where(TitleMatch.belt == key, TitleMatch.resolved.is_(False))
                                  .limit(1))).first() is not None


async def link(session: AsyncSession, key: str, kind: str, match_id: int, chat_id: int) -> None:
    session.add(TitleMatch(belt=key, kind=kind, match_id=match_id, chat_id=chat_id))


async def prepare(session: AsyncSession, belt: Belt, match_id: int, challenger_id: int) -> str:
    """Rules text for a freshly opened title match. A challenge to a champion waits for HIM for 24 hours."""
    row = await _row(session, belt.key)
    ids = holders(row)
    if not ids:
        return "🆓 The belt is vacant — anyone can take part."
    names = await _names(session, ids)
    champs = " & ".join(f"<b>{names.get(i, 'the champion')}</b>" for i in ids)
    if belt.kind == "tag":
        return f"🛡️ Champions {champs} must be in this match, otherwise the belt will not change."
    if challenger_id in ids:
        return "🛡️ The champion issued an open challenge — anyone can accept."
    match = await session.get(Match, match_id)
    if match is not None:
        match.deadline_at = utcnow() + timedelta(hours=CHAMPION_REPLY_HOURS)
    return f"🛡️ Only the champion {champs} can accept — they have {CHAMPION_REPLY_HOURS}h, then the challenge expires."


async def accept_error(session: AsyncSession, match_id: int, user_id: int) -> str | None:
    """Title-match rule for the Accept button: while a belt is held, only its champion may accept a challenge."""
    tm = (await session.execute(select(TitleMatch).where(TitleMatch.kind == "solo", TitleMatch.match_id == match_id,
                                                         TitleMatch.resolved.is_(False)))).scalars().first()
    if tm is None:
        return None
    ids = holders(await session.get(Championship, tm.belt))
    match = await session.get(Match, match_id)
    if not ids or match is None or match.challenger_id in ids or user_id in ids:
        return None
    names = await _names(session, ids)
    return f"🛡️ Only the champion ({names.get(ids[0], 'the holder')}) can accept this title match."


async def belts_of(session: AsyncSession, user_id: int) -> list[str]:
    """Full names of the belts a fighter holds right now (for his card)."""
    rows = (await session.execute(select(Championship))).scalars().all()
    return [BELTS[r.key].name for r in rows if r.key in BELTS and user_id in holders(r)]


async def give(session: AsyncSession, key: str, ids: list[int]) -> Championship:
    """Owner action: put a belt on somebody (a new reign)."""
    row = await _row(session, key)
    row.holder1_id, row.holder2_id = (ids + [None, None])[:2]
    row.since, row.reigns, row.defenses = utcnow(), row.reigns + 1, 0
    return row


async def vacate(session: AsyncSession, key: str) -> None:
    row = await _row(session, key)
    row.holder1_id = row.holder2_id = row.since = None
    row.defenses = 0


async def _names(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    users = (await session.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    return {u.id: u.display_name for u in users}


# -------------------------------------------------------------------------------- resolving matches

@dataclass
class Outcome:
    belt: Belt
    chat_id: int
    winners: list[int]
    losers: list[int]
    retained: bool
    reign: int
    defenses: int
    names: dict[int, str]
    unchanged: bool = False


async def _finish(session: AsyncSession, tm: TitleMatch) -> tuple[list[int], list[int]] | None:
    """(winners, losers) once the underlying match is decided; None while it is still going."""
    if tm.kind == "solo":
        m = await session.get(Match, tm.match_id, populate_existing=True)
        if m is None or m.status in (MatchStatus.CANCELLED, MatchStatus.EXPIRED):
            return [], []
        if m.status == MatchStatus.COMPLETED and m.winner_id:
            return [m.winner_id], [x for x in (m.challenger_id, m.opponent_id) if x and x != m.winner_id]
        return None
    m = await session.get(TagMatch, tm.match_id, populate_existing=True)
    if m is None or m.status == "cancelled":
        return [], []
    if m.status == "completed" and m.winner_team:
        return ([p.user_id for p in m.players if p.team == m.winner_team],
                [p.user_id for p in m.players if p.team != m.winner_team])
    return None


async def collect_outcomes() -> list[Outcome]:
    out: list[Outcome] = []
    async with transaction() as s:
        pending = (await s.execute(select(TitleMatch).where(TitleMatch.resolved.is_(False)))).scalars().all()
        for tm in pending:
            result = await _finish(s, tm)
            if result is None:
                continue
            tm.resolved = True
            winners, losers = result
            belt = BELTS.get(tm.belt)
            if not winners or belt is None:
                continue
            row = await _row(s, tm.belt)
            current = holders(row)
            if current and not set(current) & set(winners + losers):          # the champion never fought: no change
                out.append(Outcome(belt, tm.chat_id, winners, losers, False, row.reigns, row.defenses, {}, unchanged=True))
                continue
            retained = set(current) == set(winners) or (bool(set(current) & set(winners)) and belt.kind == "tag")
            if retained:
                row.defenses += 1
            else:
                row.holder1_id, row.holder2_id = (winners + [None, None])[:2]
                row.since, row.reigns, row.defenses = utcnow(), row.reigns + 1, 0
            out.append(Outcome(belt, tm.chat_id, winners, losers, retained, row.reigns, row.defenses,
                               await _names(s, winners + losers)))
    return out


async def publish_outcome(bot: Bot, o: Outcome) -> None:
    if o.unchanged:
        await publisher.post_everywhere(bot, o.chat_id, None,
                                        f"⚠️ {o.belt.emoji} The champion was not part of that match, so the <b>{o.belt.name}</b> did not change hands.",
                                        to_channel=False)
        return
    people = await avatars.people(bot, [(u, o.names.get(u, "Superstar")) for u in o.winners + o.losers])
    win, lose = people[:len(o.winners)], people[len(o.winners):]
    card = cards.BeltCard(headline="TITLE RETAINED" if o.retained else "NEW CHAMPION", belt_name=o.belt.name, champions=win,
                          beaten=lose, belt_image=belt_image(o.belt.key), reign=o.reign, defenses=o.defenses,
                          date_text=tr.fmt_ist(utcnow()))
    try:
        photo = await asyncio.to_thread(cards.render_belt_card, card)
    except Exception:
        log.exception("Belt card failed")
        photo = None
    names = " & ".join(f"<b>{p.name}</b>" for p in win)
    caption = (f"{o.belt.emoji} <b>{o.belt.name}</b>\n" + (f"🛡️ {names} retained the title!" if o.retained
                                                        else f"🎉 And the NEW champion{'s' if len(win) > 1 else ''}: {names}!"))
    await publisher.post_everywhere(bot, o.chat_id, photo, caption)


async def resolve_once(bot: Bot) -> None:
    for outcome in await collect_outcomes():
        try:
            await publish_outcome(bot, outcome)
        except Exception:
            log.exception("Posting the title result failed")


async def deadline_loop(bot: Bot) -> None:
    while True:
        try:
            await resolve_once(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Title loop error")
        await asyncio.sleep(POLL_SECONDS)


# ------------------------------------------------------------------------------------------ board

async def board_photo(bot: Bot) -> bytes | None:
    async with transaction() as s:
        rows = {r.key: r for r in (await s.execute(select(Championship))).scalars().all()}
        names = await _names(s, [h for r in rows.values() for h in holders(r)])
    now = utcnow()
    trows = []
    for belt in BELTS.values():
        row = rows.get(belt.key)
        ids = holders(row)
        people = await avatars.people(bot, [(u, names.get(u, "Superstar")) for u in ids])
        days = (now - row.since).days if row and row.since else 0
        detail = (f"Reign #{row.reigns} • {days} day{'s' if days != 1 else ''}" + (f" • {row.defenses} defenses" if row.defenses else "")) if ids else "VACANT"
        trows.append(cards.TitleRow(belt.name, belt.short, people, detail, belt_image(belt.key)))
    try:
        return await asyncio.to_thread(cards.render_titles_card, trows)
    except Exception:
        log.exception("Title board failed")
        return None
