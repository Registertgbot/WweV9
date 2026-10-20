from __future__ import annotations

from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, User as TgUser

from services.match_engine import Identity


def identity_from(user: TgUser) -> Identity:
    return Identity(id=user.id, username=user.username, name=user.full_name or user.username or str(user.id))


def is_group(message: Message) -> bool:
    return message.chat.type in ("group", "supergroup")


def int_parts(data: str | None, expected: int) -> list[str] | None:
    """Split callback data and make sure it has the expected number of parts."""
    parts = (data or "").split(":")
    return parts if len(parts) == expected else None


def to_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def edit_callback(cb: CallbackQuery, text: str, markup=None) -> bool:
    """Edit the message a callback came from; tolerate 'message is not modified' and deleted messages."""
    if not isinstance(cb.message, Message):
        return False
    try:
        await cb.message.edit_text(text, reply_markup=markup)
        return True
    except TelegramAPIError:
        return False
