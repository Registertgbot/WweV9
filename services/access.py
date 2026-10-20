"""Group access control: the bot only works in DEFAULT_GROUP_ID groups and groups the owner approved."""
from __future__ import annotations

import time

from sqlalchemy import delete, select

from config import get_settings
from models import AllowedGroup, utcnow

_allowed: set[int] = set()
_notified: dict[int, float] = {}
NOTICE_COOLDOWN = 300


async def load(session) -> None:
    _allowed.clear()
    _allowed.update((await session.execute(select(AllowedGroup.chat_id))).scalars().all())


def is_allowed(chat_id: int) -> bool:
    return chat_id in _allowed or chat_id in get_settings().default_groups


def should_notice(chat_id: int) -> bool:
    now = time.monotonic()
    if now - _notified.get(chat_id, -1e9) < NOTICE_COOLDOWN:
        return False
    _notified[chat_id] = now
    return True


async def allow(session, chat_id: int, title: str | None, by: int) -> bool:
    """True when newly approved, False when it already was."""
    if await session.get(AllowedGroup, chat_id) is not None:
        _allowed.add(chat_id)
        return False
    session.add(AllowedGroup(chat_id=chat_id, title=(title or "")[:256] or None, approved_by=by, approved_at=utcnow()))
    _allowed.add(chat_id)
    return True


async def disallow(session, chat_id: int) -> bool:
    res = await session.execute(delete(AllowedGroup).where(AllowedGroup.chat_id == chat_id))
    _allowed.discard(chat_id)
    return bool(res.rowcount)


async def listing(session) -> list[tuple[int, str | None]]:
    rows = (await session.execute(select(AllowedGroup).order_by(AllowedGroup.approved_at))).scalars().all()
    return [(r.chat_id, r.title) for r in rows]
