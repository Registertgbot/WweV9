from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import get_settings
from database import transaction
from services import media_manager, publisher, runtime_settings

router = Router(name="admin")


def is_owner(user_id: int | None) -> bool:
    owner = get_settings().owner_id
    return bool(owner) and user_id == owner


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    if not message.from_user or not is_owner(message.from_user.id):
        await message.answer("🚫 This command is for the bot owner only.")
        return
    async with transaction() as s:
        values = await runtime_settings.get_all(s)
    lines = [f"• <code>{k}</code> = <b>{v if not isinstance(v, bool) else ('on' if v else 'off')}</b>" for k, v in values.items()]
    missing = media_manager.missing_assets()
    gif_note = f"\n\n🎞️ Missing GIFs: {', '.join(missing)}" if missing else "\n\n🎞️ All GIFs present."
    await message.answer("⚙️ <b>Arena settings</b>\n\n" + "\n".join(lines) +
                         "\n\nChange with <code>/setsetting key value</code>" + gif_note)


@router.message(Command("setsetting"))
async def cmd_setsetting(message: Message, command: CommandObject) -> None:
    if not message.from_user or not is_owner(message.from_user.id):
        await message.answer("🚫 This command is for the bot owner only.")
        return
    parts = (command.args or "").split()
    if len(parts) != 2:
        await message.answer("Usage: <code>/setsetting key value</code> — see /settings for keys.")
        return
    try:
        async with transaction() as s:
            value = await runtime_settings.set_value(s, parts[0], parts[1])
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await message.answer(f"✅ <code>{parts[0]}</code> set to <b>{value}</b>.")


@router.message(Command("channeltest"))
async def cmd_channeltest(message: Message, bot: Bot) -> None:
    if not message.from_user or not is_owner(message.from_user.id):
        await message.answer("🚫 This command is for the bot owner only.")
        return
    await message.answer(await publisher.channel_check(bot))
