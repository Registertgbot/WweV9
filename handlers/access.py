"""Owner-only group approval: /allow, /disallow, /groups, plus the middleware that blocks unapproved groups."""
from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from config import get_settings
from database import transaction
from services import access

router = Router(name="access")
GROUP = ("group", "supergroup")
OPEN_COMMANDS = ("/allow", "/disallow", "/groups", "/id")      # handled by owner checks / harmless


def _owner(user_id: int | None) -> bool:
    owner = get_settings().owner_id
    return bool(owner) and user_id == owner


def _notice(chat_id: int) -> str:
    return ("🔒 <b>This bot is not authorized in this group.</b>\n"
            "Only groups approved by the bot owner can use it. Ask the owner to enable it here.\n"
            f"<code>{chat_id}</code>")


class GroupGate(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            chat, text = event.chat, (event.text or "")
            if chat.type in GROUP and not access.is_allowed(chat.id):
                cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
                if cmd in OPEN_COMMANDS:
                    return await handler(event, data)
                if cmd and access.should_notice(chat.id):
                    await event.answer(_notice(chat.id))
                return None
        elif isinstance(event, CallbackQuery) and event.message is not None:
            chat = event.message.chat
            if chat.type in GROUP and not access.is_allowed(chat.id):
                await event.answer("🔒 This bot is not authorized in this group.", show_alert=True)
                return None
        return await handler(event, data)


def _target(message: Message, command: CommandObject) -> int | None:
    arg = (command.args or "").strip()
    if arg.lstrip("-").isdigit():
        return int(arg)
    return message.chat.id if message.chat.type in GROUP else None


@router.message(Command("allow"))
async def cmd_allow(message: Message, command: CommandObject, bot: Bot) -> None:
    if not _owner(message.from_user.id if message.from_user else None):
        return                                                  # silent for everybody else
    chat_id = _target(message, command)
    if chat_id is None:
        await message.answer("Use <code>/allow</code> inside the group, or <code>/allow -100123…</code> here.")
        return
    title = message.chat.title if chat_id == message.chat.id else None
    async with transaction() as s:
        new = await access.allow(s, chat_id, title, message.from_user.id)
    await message.answer("✅ Group approved — the bot works here now." if new else "✅ This group was already approved.")
    if chat_id != message.chat.id:
        try:
            await bot.send_message(chat_id, "✅ The bot owner approved this group. Send /play to start!")
        except Exception:
            pass


@router.message(Command("disallow"))
async def cmd_disallow(message: Message, command: CommandObject) -> None:
    if not _owner(message.from_user.id if message.from_user else None):
        return
    chat_id = _target(message, command)
    if chat_id is None:
        await message.answer("Use <code>/disallow</code> inside the group, or <code>/disallow -100123…</code> here.")
        return
    if chat_id in get_settings().default_groups:
        await message.answer("⚠️ That is a DEFAULT_GROUP_ID group — remove it from the environment instead.")
        return
    async with transaction() as s:
        removed = await access.disallow(s, chat_id)
    await message.answer("🚫 Group removed — the bot is blocked there." if removed else "That group wasn't approved.")


@router.message(Command("groups"))
async def cmd_groups(message: Message) -> None:
    if not _owner(message.from_user.id if message.from_user else None):
        return
    async with transaction() as s:
        rows = await access.listing(s)
    lines = [f"⭐ <code>{c}</code> (default)" for c in get_settings().default_groups]
    lines += [f"✅ <code>{c}</code> {(t or '')[:40]}" for c, t in rows]
    await message.answer("🏟️ <b>Approved groups</b>\n" + ("\n".join(lines) or "none yet"))
