"""/titles (board image) and the owner's /givebelt, /vacate."""
from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from config import get_settings
from database import transaction
from services import titles

router = Router(name="titles")


async def send_board(bot: Bot, chat_id: int) -> None:
    photo = await titles.board_photo(bot)
    if photo is None:
        await bot.send_message(chat_id, "👑 Could not draw the title board right now.")
        return
    await bot.send_photo(chat_id, BufferedInputFile(photo, "titles.jpg"), caption="👑 <b>Current champions</b>")


@router.message(Command("titles", "champions"))
async def cmd_titles(message: Message, bot: Bot) -> None:
    await send_board(bot, message.chat.id)


def _is_owner(message: Message) -> bool:
    return bool(message.from_user) and message.from_user.id == get_settings().owner_id


def _keys() -> str:
    return ", ".join(f"<code>{k}</code>" for k in titles.BELTS)


@router.message(Command("givebelt"))
async def cmd_givebelt(message: Message, command: CommandObject) -> None:
    if not _is_owner(message):
        return
    args = (command.args or "").split()
    if not args or args[0] not in titles.BELTS:
        await message.answer(f"Use <code>/givebelt &lt;belt&gt; [user_id user_id2]</code> (or reply to the fighter).\nBelts: {_keys()}")
        return
    ids = [int(x) for x in args[1:3] if x.lstrip("-").isdigit()]
    if not ids and message.reply_to_message and message.reply_to_message.from_user:
        ids = [message.reply_to_message.from_user.id]
    belt = titles.BELTS[args[0]]
    if not ids or (belt.kind == "tag" and len(ids) != 2):
        await message.answer("Give the fighter's id (two ids for the tag belt), or reply to his message.")
        return
    async with transaction() as s:
        await titles.give(s, belt.key, ids)
    await message.answer(f"✅ {belt.emoji} <b>{belt.name}</b> given to <code>{' '.join(map(str, ids))}</code>.")


@router.message(Command("vacate"))
async def cmd_vacate(message: Message, command: CommandObject) -> None:
    if not _is_owner(message):
        return
    key = (command.args or "").strip()
    if key not in titles.BELTS:
        await message.answer(f"Use <code>/vacate &lt;belt&gt;</code>. Belts: {_keys()}")
        return
    async with transaction() as s:
        await titles.vacate(s, key)
    await message.answer(f"🧹 {titles.BELTS[key].name} is vacant now.")
