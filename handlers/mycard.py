"""/mycard (reply to someone to see theirs) and the 🪪 My Card button."""
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message, User as TgUser

from database import transaction
from services import mycard

router = Router(name="mycard")


async def _send(bot: Bot, chat_id: int, who: TgUser) -> None:
    async with transaction() as s:
        photo = await mycard.render(s, bot, who.id, who.full_name, who.username)
    await bot.send_photo(chat_id, BufferedInputFile(photo, "mycard.jpg"), caption=f"🪪 <b>{who.full_name}</b>")


@router.message(Command("mycard"))
async def cmd_mycard(message: Message, bot: Bot) -> None:
    target = message.reply_to_message.from_user if message.reply_to_message and message.reply_to_message.from_user else message.from_user
    if target is None or target.is_bot:
        return
    await _send(bot, message.chat.id, target)


@router.callback_query(F.data == "mc:me")
async def cb_mycard(cb: CallbackQuery, bot: Bot) -> None:
    await cb.answer("🪪 Making your card…")
    await _send(bot, cb.message.chat.id, cb.from_user)
