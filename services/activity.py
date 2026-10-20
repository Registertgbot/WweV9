"""One place that answers: is this user already busy in another mode?"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import MitbDuelPlayer, MitbPlayer, RumblePlayer, TagPlayer
from services.match_engine import active_match_for


async def is_busy(session: AsyncSession, user_id: int) -> bool:
    """In a Rumble, Tag Team match, MITB tournament/duel or an open 1v1 match?"""
    for model in (RumblePlayer, TagPlayer, MitbPlayer, MitbDuelPlayer):
        found = (await session.execute(select(model.id).where(model.user_id == user_id,
                                                              model.is_active.is_(True)))).first()
        if found:
            return True
    return (await active_match_for(session, user_id)) is not None
