from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database import transaction
from handlers.common import edit_callback
from keyboards.main import back_kb
from services import stats, texts

router = Router(name="profile")


async def _profile(user_id: int, fallback_name: str) -> str:
    async with transaction() as s:
        t = await stats.user_totals(s, user_id)
    if t is None:
        return texts.profile_text(fallback_name, 0, 0, 0, 0, 0, None)
    return texts.profile_text(t["name"], t["wins"], t["losses"], t["total"], t["knockouts"], t["dealt"], t["rate"])


@router.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    if message.from_user:
        await message.answer(await _profile(message.from_user.id, message.from_user.full_name))


@router.callback_query(F.data == "menu:profile")
async def cb_profile(cb: CallbackQuery) -> None:
    await cb.answer()
    await edit_callback(cb, await _profile(cb.from_user.id, cb.from_user.full_name), back_kb())


@router.message(Command("mystats"))
async def cmd_mystats(message: Message) -> None:
    if message.from_user is None:
        return
    async with transaction() as s:
        data = await stats.detailed_stats(s, message.from_user.id)
    if data is None:
        await message.answer("No stats yet — use /match in a group to play your first match!")
        return
    await message.answer(texts.mystats_text(data["name"], data))
