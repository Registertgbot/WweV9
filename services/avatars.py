"""Telegram profile photos for the image cards (cached, best effort: no photo -> the card draws initials)."""
from __future__ import annotations

import asyncio
import io
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from services import fighters
from services.cards import Person

log = logging.getLogger(__name__)

TTL_OK = 6 * 3600              # a fetched photo is reused for 6 hours
TTL_MISSING = 15 * 60          # "no photo / hidden by privacy settings" is re-checked after 15 minutes
MAX_CACHE = 256
WANTED_WIDTH = 240             # smallest size that is still sharp on a 270 px avatar circle after downscaling
_cache: dict[int, tuple[float, bytes | None]] = {}


def _remember(user_id: int, data: bytes | None) -> None:
    if len(_cache) >= MAX_CACHE:
        for key in sorted(_cache, key=lambda k: _cache[k][0])[:MAX_CACHE // 4]:
            _cache.pop(key, None)
    _cache[user_id] = (time.monotonic(), data)


async def fetch_avatar(bot: Bot, user_id: int) -> bytes | None:
    try:                                                   # a wrestler picture chosen with /fighter wins over the profile photo
        stem = await fighters.get(user_id)
        chosen = fighters.image_bytes(stem) if stem else None
        if chosen:
            return chosen
    except Exception:
        log.exception("Fighter lookup for %s failed", user_id)
    hit = _cache.get(user_id)
    if hit is not None:
        age = time.monotonic() - hit[0]
        if age < (TTL_OK if hit[1] else TTL_MISSING):
            return hit[1]
    data: bytes | None = None
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count and photos.photos:
            sizes = photos.photos[0]
            pick = next((p for p in sizes if p.width >= WANTED_WIDTH), sizes[-1])
            buf = io.BytesIO()
            await asyncio.wait_for(bot.download(pick.file_id, destination=buf), timeout=15)
            data = buf.getvalue() or None
    except (TelegramAPIError, asyncio.TimeoutError, OSError) as exc:
        log.info("Profile photo of %s unavailable: %s", user_id, exc)
    except Exception:                                      # never let a photo break a result post
        log.exception("Profile photo of %s failed", user_id)
    _remember(user_id, data)
    return data


async def people(bot: Bot, pairs: list[tuple[int, str]], limit: int = 5) -> list[Person]:
    """``[(user_id, name), ...]`` -> ``[Person, ...]`` with photos fetched a few at a time."""
    gate = asyncio.Semaphore(limit)

    async def one(uid: int, name: str) -> Person:
        async with gate:
            return Person(uid, name, await fetch_avatar(bot, uid))

    return list(await asyncio.gather(*(one(uid, name) for uid, name in pairs)))
