"""Sending result pictures to the group and mirroring them to the results channel.

The channel is optional (``RESULTS_CHANNEL_ID``). Every function here is best effort: a missing channel,
a bot that is not an admin there, or a Telegram hiccup is logged and never breaks the tournament flow.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import BufferedInputFile

from config import get_settings
from services.tg import safe_send

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
_warned: set[str] = set()


def channel_target() -> int | str | None:
    """The configured results channel as Telegram expects it (int id or '@username'); None when not set."""
    raw = (get_settings().results_channel or "").strip()
    if not raw:
        return None
    return int(raw) if raw.lstrip("-").isdigit() else raw


def _warn_once(key: str, message: str, *args) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(message, *args)


async def send_photo(bot: Bot, chat_id: int | str, photo: bytes | str, caption: str) -> str | None:
    """Send a picture (raw JPEG bytes, or a Telegram file_id). Returns the file_id for re-use, None on failure."""
    media = BufferedInputFile(photo, filename="card.jpg") if isinstance(photo, bytes) else photo
    try:
        msg = await bot.send_photo(chat_id, media, caption=caption[:CAPTION_LIMIT])
        return msg.photo[-1].file_id if msg.photo else None
    except TelegramForbiddenError:
        _warn_once(f"forbidden:{chat_id}", "Not allowed to post in %s (is the bot an admin there?)", chat_id)
    except TelegramAPIError as exc:
        _warn_once(f"api:{chat_id}:{exc}", "send_photo to %s failed: %s", chat_id, exc)
    return None


async def post_everywhere(bot: Bot, group_chat_id: int | None, photo: bytes | None, caption: str,
                          to_channel: bool = True) -> bool:
    """Picture (or, when rendering failed, plain text) to the group and to the results channel.

    Returns True when the group copy went out. The group copy is sent first and its file_id is re-used for the
    channel so the image is uploaded once.
    """
    group_ok = False
    file_id: str | None = None
    if group_chat_id is not None:
        if photo is not None:
            file_id = await send_photo(bot, group_chat_id, photo, caption)
            group_ok = file_id is not None
        if not group_ok:
            group_ok = (await safe_send(bot, group_chat_id, caption)).ok
    target = channel_target() if to_channel else None
    if target is not None:
        if photo is not None:
            sent = await send_photo(bot, target, file_id or photo, caption)
            if sent is None and file_id is not None:          # a cached id can be refused: upload the bytes instead
                sent = await send_photo(bot, target, photo, caption)
            if sent is None:
                await _channel_text(bot, target, caption)
        else:
            await _channel_text(bot, target, caption)
    return group_ok


async def _channel_text(bot: Bot, target: int | str, text: str) -> None:
    try:
        await bot.send_message(target, text)
    except TelegramAPIError as exc:
        _warn_once(f"text:{target}:{exc}", "Text post to %s failed: %s", target, exc)


async def channel_check(bot: Bot) -> str:
    """Owner diagnostics for /channeltest: post a test line and describe the outcome."""
    target = channel_target()
    if target is None:
        return "⚙️ RESULTS_CHANNEL_ID is not set, so results are only posted in the group."
    try:
        await bot.send_message(target, "✅ WWE Arena is connected — tournament results will appear here.")
        return f"✅ Test message posted to <code>{target}</code>."
    except TelegramForbiddenError:
        return "🚫 The bot may not post there. Add it to the channel as an administrator with “Post messages”."
    except TelegramAPIError as exc:
        return f"⚠️ Telegram refused: <code>{str(exc)[:160]}</code>"
