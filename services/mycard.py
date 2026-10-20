"""My Card: a player's stats gathered from the database and drawn as a premium card."""
from __future__ import annotations

import asyncio

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Match, MatchStatus, Tournament, User
from services import cards, stats, titles

TIERS = [(50, "LEGEND"), (25, "MAIN EVENTER"), (10, "CONTENDER"), (0, "ROOKIE")]       # (min wins, tier)
THEME_UNLOCK = [(5, "royal"), (3, "ice"), (1, "fire"), (0, "gold")]                      # (min titles, theme)


def tier_for(wins: int) -> str:
    return next(name for need, name in TIERS if wins >= need)


def theme_for(titles: int) -> str:
    return next(name for need, name in THEME_UNLOCK if titles >= need)


async def _best_streak(session: AsyncSession, user_id: int) -> int:
    rows = (await session.execute(
        select(Match.winner_id).where(Match.status == MatchStatus.COMPLETED,
                                      or_(Match.challenger_id == user_id, Match.opponent_id == user_id))
        .order_by(Match.id))).scalars().all()
    best = run = 0
    for winner in rows:
        run = run + 1 if winner == user_id else 0
        best = max(best, run)
    return best


async def collect(session: AsyncSession, user_id: int) -> dict:
    data = await stats.detailed_stats(session, user_id) or {"wins": 0, "losses": 0, "knockouts": 0, "finishers": 0}
    titles_won = (await session.execute(select(func.count()).select_from(Tournament).where(
        Tournament.winner_id == user_id, Tournament.status == "completed"))).scalar_one()
    user = await session.get(User, user_id)
    data.update(titles=titles_won, belts=await titles.belts_of(session, user_id), streak=await _best_streak(session, user_id),
                since=user.registered_at.strftime("Member since %b %Y") if user else "")
    return data


async def render(session: AsyncSession, bot, user_id: int, name: str, username: str | None) -> bytes:
    from services import avatars
    data = await collect(session, user_id)
    avatar = await avatars.fetch_avatar(bot, user_id)
    card = cards.ProfileCard(
        player=cards.Person(user_id, name, avatar), tier=tier_for(data["wins"]), wins=data["wins"], losses=data["losses"],
        knockouts=data["knockouts"], titles=data["titles"], finishers=data["finishers"], streak=data["streak"],
        handle=f"@{username}" if username else "", since=data["since"], theme=theme_for(data["titles"]), held_belts=data["belts"])
    return await asyncio.to_thread(cards.render_profile_card, card)
