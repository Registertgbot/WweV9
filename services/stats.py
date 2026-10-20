"""Read-only statistics queries (profile, leaderboard, history). Active matches are never counted."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Match, MatchPlayer, MatchStatus, MatchTurn, User
from services import rules

PAGE_SIZE = 10
HISTORY_PAGE_SIZE = 5


async def user_totals(session: AsyncSession, user_id: int) -> dict | None:
    user = await session.get(User, user_id)
    if user is None:
        return None
    return {"name": user.display_name, "wins": user.wins, "losses": user.losses, "total": user.total_matches,
            "knockouts": user.knockouts, "dealt": user.damage_dealt, "received": user.damage_received,
            "rate": rules.win_rate(user.wins, user.total_matches)}


async def detailed_stats(session: AsyncSession, user_id: int) -> dict | None:
    base = await user_totals(session, user_id)
    if base is None:
        return None
    done = select(Match.id).where(Match.status == MatchStatus.COMPLETED)
    finishers = (await session.execute(
        select(func.count()).select_from(MatchTurn).where(
            MatchTurn.match_id.in_(done), MatchTurn.attacker_id == user_id, MatchTurn.move == "finisher"))).scalar_one()
    reversals = (await session.execute(
        select(func.count()).select_from(MatchTurn).where(
            MatchTurn.match_id.in_(done), MatchTurn.target_id == user_id, MatchTurn.reversed.is_(True)))).scalar_one()
    base.update(finishers=finishers, reversals=reversals)
    return base


async def leaderboard_page(session: AsyncSession, page: int) -> tuple[list[tuple[str, int]], int]:
    """Ranked by wins, then win rate, then completed matches, then oldest account (id)."""
    where = User.total_matches > 0
    total = (await session.execute(select(func.count()).select_from(User).where(where))).scalar_one()
    pages = max(1, -(-total // PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    rate = cast(User.wins, Float) / User.total_matches
    stmt = (select(User.display_name, User.wins).where(where)
            .order_by(User.wins.desc(), rate.desc(), User.total_matches.desc(), User.id.asc())
            .limit(PAGE_SIZE).offset(page * PAGE_SIZE))
    rows = [(n, w) for n, w in (await session.execute(stmt)).all()]
    return rows, pages


async def history_page(session: AsyncSession, user_id: int, page: int) -> tuple[list[dict], int, int]:
    """Completed matches the requesting user took part in. Returns (items, page, total_pages)."""
    mine = (select(Match).join(MatchPlayer, MatchPlayer.match_id == Match.id)
            .where(MatchPlayer.user_id == user_id, Match.status == MatchStatus.COMPLETED))
    total = (await session.execute(select(func.count()).select_from(mine.subquery()))).scalar_one()
    pages = max(1, -(-total // HISTORY_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    matches = (await session.execute(
        mine.order_by(Match.completed_at.desc(), Match.id.desc())
        .limit(HISTORY_PAGE_SIZE).offset(page * HISTORY_PAGE_SIZE))).scalars().all()
    if not matches:
        return [], 0, pages
    turns = (await session.execute(select(MatchTurn).where(MatchTurn.match_id.in_([m.id for m in matches])))).scalars().all()
    by_match = defaultdict(list)
    for t in turns:
        by_match[t.match_id].append(t)
    items = []
    for m in matches:
        opp_id = next((p.user_id for p in m.players if p.user_id != user_id), None)
        opp = await session.get(User, opp_id) if opp_id else None
        summary = rules.summarize_turns(by_match[m.id], [p.user_id for p in m.players])
        items.append({
            "opponent": opp.display_name if opp else "Former Superstar",
            "won": m.winner_id == user_id, "when": m.completed_at, "reason": m.end_reason,
            "turns": summary.total_turns, "damage": summary.damage_dealt.get(user_id, 0),
            "finishers": summary.finishers.get(user_id, 0), "reversals": summary.reversals.get(user_id, 0)})
    return items, page, pages
