"""The /play hub: one welcome picture + caption with a button for every game mode.

Drop the picture at ``assets/welcome.jpg`` (or .jpeg/.png/.webp, or an animated .mp4/.gif).
Without a file the hub is sent as a plain text message with the same buttons.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import FSInputFile

from config import get_settings
from services import fancy as F
from services import mitb_rules as mr
from services import rumble_rules as rr
from services import tag_rules as tr
from services.scoreboard import LINE
from services.tg import safe_send

log = logging.getLogger(__name__)

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
ANIM_EXT = (".mp4", ".gif")
CAPTION_LIMIT = 1024
_cache: dict[tuple[str, int], tuple[str, str]] = {}      # (path, mtime) -> (kind, telegram file_id)


@dataclass
class HubStatus:
    """Which group-wide modes are running right now (a second lobby of the same kind is refused)."""
    rumble: bool = False
    tag: bool = False
    mitb: bool = False


def find_welcome(assets_dir: Path | None = None) -> Path | None:
    """assets/welcome.<ext>, also accepted inside assets/gifs/."""
    base = assets_dir or get_settings().assets_dir
    for folder in (base.parent, base):
        for ext in IMAGE_EXT + ANIM_EXT:
            candidate = folder / f"welcome{ext}"
            if candidate.is_file():
                return candidate
    return None


def welcome_caption(status: HubStatus | None = None) -> str:
    """Title + subtitle (+ one line only while a group-wide mode is running). The buttons list the modes."""
    st = status or HubStatus()
    live = [name for name, on in (("ʀᴏʏᴀʟ ʀᴜᴍʙʟᴇ", st.rumble), ("ᴛᴀɢ ᴛᴇᴀᴍ", st.tag), ("ᴍɪᴛʙ", st.mitb)) if on]
    running = f"\n⏳ {F.small_caps('running')}: {' • '.join(live)}" if live else ""
    return (f"🏟️ {F.sans_bold('WWE ARENA')}\n{F.small_caps('choose your battle, superstar')}{running}\n\n"
            f"📩 {F.small_caps('first press start in the bot dm')} • 👇 {F.small_caps('tap a mode')}")


def tag_menu_caption() -> str:
    return (f"🏷️ {F.sans_bold('TAG TEAM')}\n\n{F.small_caps('pick the team size')}:\n"
            f"🦁 {F.small_caps('lions')} vs 🐯 {F.small_caps('tigers')} • {tr.MIN_PER_TEAM}+ {F.small_caps('per team')}")


def mitb_menu_caption() -> str:
    return (f"💼 {F.sans_bold('MONEY IN THE BANK')}\n\n{F.small_caps('pick the size and speed')}:\n"
            f"⚡ {F.small_caps('fast')} ≈ 40 min • 🐢 {F.small_caps('slow')} = {F.small_caps('all day')}")


def leaderboard_menu_caption() -> str:
    return f"🏆 {F.sans_bold('LEADERBOARDS')}\n\n{F.small_caps('which board do you want to see?')}"


def help_menu_caption() -> str:
    return f"📖 {F.sans_bold('HOW TO PLAY')}\n\n{F.small_caps('which mode do you want to learn?')}"


async def send_welcome(bot: Bot, chat_id: int, caption: str, reply_markup=None) -> int | None:
    """Send the welcome picture/animation with caption + buttons; fall back to plain text."""
    path = find_welcome()
    if path is not None and len(caption) <= CAPTION_LIMIT:
        kind = "photo" if path.suffix.lower() in IMAGE_EXT else "animation"
        key = (str(path), path.stat().st_mtime_ns)
        send = bot.send_photo if kind == "photo" else bot.send_animation
        arg = "photo" if kind == "photo" else "animation"
        cached = _cache.get(key)
        if cached:
            try:
                msg = await send(chat_id, **{arg: cached[1]}, caption=caption, reply_markup=reply_markup)
                return msg.message_id
            except TelegramForbiddenError:
                return None
            except TelegramBadRequest:
                _cache.pop(key, None)
        try:
            msg = await send(chat_id, **{arg: FSInputFile(path)}, caption=caption, reply_markup=reply_markup)
            media = (msg.photo[-1] if getattr(msg, "photo", None) else
                     getattr(msg, "animation", None) or getattr(msg, "video", None) or getattr(msg, "document", None))
            if media is not None:
                _cache[key] = (kind, media.file_id)
            return msg.message_id
        except TelegramForbiddenError:
            return None
        except TelegramAPIError as exc:
            log.warning("Sending %s failed (%s); using text fallback", path.name, exc)
    sent = await safe_send(bot, chat_id, caption, reply_markup=reply_markup)
    return sent.message_id
