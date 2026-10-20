from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database import transaction
from handlers.common import edit_callback, to_int
from keyboards.main import pager_kb
from services import stats, texts

router = Router(name="leaderboard")


async def _render(page: int):
    async with transaction() as s:
        rows, pages = await stats.leaderboard_page(s, page)
    page = max(0, min(page, pages - 1))
    return texts.leaderboard_text(rows, page, stats.PAGE_SIZE, pages), pager_kb("lb", page, pages)


@router.message(Command("leaderboard"))
async def cmd_leaderboard(message: Message) -> None:
    text, kb = await _render(0)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "menu:leaderboard")
async def cb_menu(cb: CallbackQuery) -> None:
    await cb.answer()
    text, kb = await _render(0)
    await edit_callback(cb, text, kb)


@router.callback_query(F.data.startswith("lb:"))
async def cb_page(cb: CallbackQuery) -> None:
    page = to_int((cb.data or "").split(":")[1]) if (cb.data or "").count(":") == 1 else None
    if page is None:
        await cb.answer("Invalid page.", show_alert=True)
        return
    await cb.answer()
    text, kb = await _render(page)
    await edit_callback(cb, text, kb)
