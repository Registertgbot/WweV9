"""GIF delivery with Telegram file_id caching and a text fallback.

GIF files are NOT bundled: add them to assets/gifs/ yourself (see README). If a file is
missing, or Telegram rejects it, the same event is sent as a plain text message.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import FSInputFile


async def _send_media(bot, chat_id, media, caption, reply_markup, image: bool):
    if image:
        return await bot.send_photo(chat_id, photo=media, caption=caption, reply_markup=reply_markup)
    return await bot.send_animation(chat_id, animation=media, caption=caption, reply_markup=reply_markup)
from sqlalchemy import select

from config import get_settings
from database import transaction
from models import MediaAsset

log = logging.getLogger(__name__)

EVENT_FILES = {
    "spear": "spear.gif", "f5": "f5.gif", "finisher": "finisher.gif", "superkick": "superkick.gif",
    "ddt": "ddt.gif", "reversal": "reversal.gif", "knockout": "knockout.gif",
    # Royal Rumble (all optional: a missing file just falls back to text)
    "rumble_start": "rumble_start.gif", "rumble_entry": "rumble_entry.gif",
    "rumble_elimination": "rumble_elimination.gif", "rumble_over_the_top": "rumble_over_the_top.gif",
    "rumble_final_two": "rumble_final_two.gif", "rumble_winner": "rumble_winner.gif",
    # Tag Team (optional)
    "tag_start": "tag_start.gif", "tag_in": "tag_in.gif", "tag_ko": "tag_ko.gif",
    "tag_handicap": "tag_handicap.gif", "tag_winner": "tag_winner.gif",
    # Money in the Bank (optional)
    "mitb_start": "mitb_start.gif", "mitb_round": "mitb_round.gif", "mitb_duel_end": "mitb_duel_end.gif",
    "mitb_ladder": "mitb_ladder.gif", "mitb_climb": "mitb_climb.gif", "mitb_fall": "mitb_fall.gif",
    "mitb_winner": "mitb_winner.gif", "mitb_cashin": "mitb_cashin.gif", "mitb_champion": "mitb_champion.gif",
    # Tournament (optional)
    "tour_start": "tour_start.gif", "tour_match": "tour_match.gif", "tour_champion": "tour_champion.gif",
}
CAPTION_LIMIT = 1024
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
EXTENSIONS = (".gif", ".mp4") + IMAGE_EXT   # Telegram "GIFs" are normally silent H.264 mp4 files, so both work


class FileIdStore(Protocol):
    async def get(self, event_type: str) -> str | None: ...
    async def save(self, event_type: str, file_id: str | None) -> None: ...


class DbFileIdStore:
    async def get(self, event_type: str) -> str | None:
        async with transaction() as s:
            row = await s.get(MediaAsset, event_type)
            return row.telegram_file_id if row else None

    async def save(self, event_type: str, file_id: str | None) -> None:
        async with transaction() as s:
            row = await s.get(MediaAsset, event_type)
            if row is not None:
                row.telegram_file_id = file_id


async def ensure_assets(session) -> None:
    """Seed the media_assets table (one row per event type)."""
    existing = set((await session.execute(select(MediaAsset.event_type))).scalars().all())
    for event, filename in EVENT_FILES.items():
        if event not in existing:
            session.add(MediaAsset(event_type=event, local_path=f"assets/gifs/{filename}"))


def asset_path(event_type: str, assets_dir: Path | None = None) -> Path:
    """The animation file for an event: <name>.gif or <name>.mp4 (whichever exists; .gif if neither)."""
    base = (assets_dir or get_settings().assets_dir) / EVENT_FILES[event_type]
    for ext in EXTENSIONS:
        candidate = base.with_suffix(ext)
        if candidate.is_file():
            return candidate
    return base


def missing_assets(assets_dir: Path | None = None) -> list[str]:
    return [name for ev, name in EVENT_FILES.items() if not asset_path(ev, assets_dir).is_file()]


async def send_event(bot: Bot, chat_id: int, event_type: str, caption: str, reply_markup=None,
                     store: FileIdStore | None = None, assets_dir: Path | None = None) -> int | None:
    """Send an animation with caption; fall back to text. Returns the sent message_id (or None)."""
    store = store or DbFileIdStore()
    if event_type in EVENT_FILES and len(caption) <= CAPTION_LIMIT:
        is_image = asset_path(event_type, assets_dir).suffix.lower() in IMAGE_EXT
        file_id = await store.get(event_type)
        if file_id:
            try:
                msg = await _send_media(bot, chat_id, file_id, caption, reply_markup, is_image)
                return msg.message_id
            except TelegramForbiddenError:
                return None
            except (TelegramBadRequest, TelegramAPIError):
                log.warning("Cached file_id for %s rejected; re-uploading", event_type)
                await store.save(event_type, None)
        path = asset_path(event_type, assets_dir)
        if path.is_file():
            try:
                msg = await _send_media(bot, chat_id, FSInputFile(path), caption, reply_markup, is_image)
                anim = (msg.photo[-1] if getattr(msg, "photo", None) else
                        getattr(msg, "animation", None) or getattr(msg, "document", None))
                if anim is not None:
                    await store.save(event_type, anim.file_id)
                return msg.message_id
            except TelegramForbiddenError:
                return None
            except TelegramAPIError as exc:
                log.warning("Sending %s failed (%s); using text fallback", path.name, exc)
        else:
            log.info("GIF %s missing; using text fallback", path.name)
    try:
        msg = await bot.send_message(chat_id, caption, reply_markup=reply_markup)
        return msg.message_id
    except TelegramAPIError as exc:
        log.warning("Text fallback to %s failed: %s", chat_id, exc)
        return None
