"""Group "it's your turn" pings.

Moves are chosen in the bot's DM, so players often don't notice it's their turn. This posts a short
message in the group that @-mentions them (Telegram notifies them) with a button that opens the bot.
One ping slot per game: a new ping replaces the previous one, and it is deleted when the turn is resolved.
The slots live in memory only, so after a restart an old ping may simply stay in the chat.
"""
from __future__ import annotations

from datetime import datetime

from aiogram import Bot

from keyboards.main import open_bot_kb
from models import utcnow
from services.scoreboard import esc
from services.tg import safe_delete, safe_send

_slots: dict[str, int] = {}


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{esc(name)}</a>'


def seconds_left(deadline: datetime | None, now: datetime | None = None) -> int | None:
    if deadline is None:
        return None
    return max(0, int((deadline - (now or utcnow())).total_seconds()))


def move_ping_text(user_id: int, name: str, seconds: int | None) -> str:
    timer = f"\n⏱️ {seconds}s left" if seconds else ""
    return (f"🎯 {mention(user_id, name)} — <b>IT'S YOUR TURN!</b>\n"
            f"📩 Open the bot and choose your move.{timer}")


def defend_ping_text(user_id: int, name: str, seconds: int | None) -> str:
    timer = f"\n⏱️ {seconds}s left" if seconds else ""
    return (f"⚠️ {mention(user_id, name)} — <b>YOU'RE BEING ATTACKED!</b>\n"
            f"🛡️ Open the bot: use your Reversal or take the hit.{timer}")


async def clear(bot: Bot, chat_id: int, key: str) -> None:
    await safe_delete(bot, chat_id, _slots.pop(key, None))


async def _post(bot: Bot, chat_id: int, key: str, text: str, bot_url: str, button: str) -> None:
    await clear(bot, chat_id, key)
    sent = await safe_send(bot, chat_id, text, reply_markup=open_bot_kb(bot_url, button))
    if sent.message_id:
        _slots[key] = sent.message_id


async def ping_move(bot: Bot, chat_id: int, key: str, user_id: int, name: str,
                    deadline: datetime | None, bot_url: str) -> None:
    await _post(bot, chat_id, key, move_ping_text(user_id, name, seconds_left(deadline)),
                bot_url, "🎮 Choose my move")


async def ping_defend(bot: Bot, chat_id: int, key: str, user_id: int, name: str,
                      deadline: datetime | None, bot_url: str) -> None:
    await _post(bot, chat_id, key, defend_ping_text(user_id, name, seconds_left(deadline)),
                bot_url, "🛡️ Open the bot")
