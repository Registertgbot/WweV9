from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from handlers.common import edit_callback
from keyboards.main import back_kb
from services import scoreboard as sb, texts

router = Router(name="help")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP)


@router.callback_query(F.data == "menu:help")
async def cb_help(cb: CallbackQuery) -> None:
    await cb.answer()
    await edit_callback(cb, texts.HELP, back_kb())


@router.callback_query(F.data == "rules")
async def cb_rules(cb: CallbackQuery) -> None:
    await cb.answer(sb.short_rules(), show_alert=True)
