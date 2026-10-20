from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database import transaction
from handlers.common import edit_callback, is_group, to_int
from keyboards.main import open_bot_kb, pager_kb
from services import stats, texts, turn_manager as tm

router = Router(name="history")


async def _render(user_id: int, page: int):
    """Always scoped to the requesting user: nobody can read another player's history."""
    async with transaction() as s:
        items, page, pages = await stats.history_page(s, user_id, page)
    return texts.history_text(items, page, pages), pager_kb("hs", page, pages)


@router.message(Command("history"))
async def cmd_history(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return
    if is_group(message):
        await message.answer("📜 Your match history is private — open it in the bot's DM.",
                             reply_markup=open_bot_kb(await tm.bot_link(bot), "📜 Open history"))
        return
    text, kb = await _render(message.from_user.id, 0)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "menu:history")
async def cb_menu(cb: CallbackQuery) -> None:
    await cb.answer()
    text, kb = await _render(cb.from_user.id, 0)
    await edit_callback(cb, text, kb)


@router.callback_query(F.data.startswith("hs:"))
async def cb_page(cb: CallbackQuery) -> None:
    parts = (cb.data or "").split(":")
    page = to_int(parts[1]) if len(parts) == 2 else None
    if page is None:
        await cb.answer("Invalid page.", show_alert=True)
        return
    await cb.answer()
    text, kb = await _render(cb.from_user.id, page)
    await edit_callback(cb, text, kb)
