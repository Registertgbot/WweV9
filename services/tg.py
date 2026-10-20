"""Small, defensive wrappers around Telegram calls (rate limits, blocked users, stale edits)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

log = logging.getLogger(__name__)


@dataclass
class Sent:
    message_id: int | None = None
    forbidden: bool = False     # user blocked the bot / never started it

    @property
    def ok(self) -> bool:
        return self.message_id is not None


async def safe_send(bot: Bot, chat_id: int, text: str, reply_markup=None) -> Sent:
    for attempt in (1, 2):
        try:
            msg = await bot.send_message(chat_id, text, reply_markup=reply_markup)
            return Sent(msg.message_id)
        except TelegramRetryAfter as exc:
            if attempt == 2:
                break
            await asyncio.sleep(min(exc.retry_after, 10))
        except TelegramForbiddenError:
            return Sent(forbidden=True)
        except TelegramAPIError as exc:
            log.warning("send_message to %s failed: %s", chat_id, exc)
            break
    return Sent()


async def safe_edit(bot: Bot, chat_id: int, message_id: int, text: str, reply_markup=None) -> bool:
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc).lower():
            log.info("edit failed (%s/%s): %s", chat_id, message_id, exc)
    except TelegramAPIError as exc:
        log.warning("edit failed (%s/%s): %s", chat_id, message_id, exc)
    return False


async def safe_clear_markup(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except TelegramAPIError:
        pass


async def safe_delete(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramAPIError:
        pass
