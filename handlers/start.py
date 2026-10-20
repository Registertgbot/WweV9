from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from database import transaction
from handlers.common import edit_callback, identity_from, is_group
from keyboards.main import open_bot_kb, welcome_kb
from services import match_engine as eng
from services import texts, turn_manager as tm

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return
    if is_group(message):
        await message.answer(texts.WELCOME_GROUP, reply_markup=open_bot_kb(await tm.bot_link(bot)))
        return
    ident = identity_from(message.from_user)
    async with transaction() as s:
        await eng.upsert_user(s, ident, dm_started=True)
        results = await eng.try_start_waiting(s, ident.id)
    await message.answer(texts.WELCOME, reply_markup=welcome_kb())
    for r in results:
        if r.kind == "started":
            await tm.publish_live(bot, r.match)
        else:
            await message.answer("✅ You're ready! Waiting for your opponent to press Start in the bot…")


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    if message.from_user:
        await message.answer(f"🆔 Your Telegram ID: <code>{message.from_user.id}</code>")


@router.callback_query(F.data == "menu:home")
async def cb_home(cb: CallbackQuery) -> None:
    await cb.answer()
    await edit_callback(cb, texts.WELCOME, welcome_kb())
