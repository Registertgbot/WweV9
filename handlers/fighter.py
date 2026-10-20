"""/fighter — pick a wrestler picture for your cards (or go back to your Telegram profile photo)."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup as M, Message

from services import fighters

router = Router(name="fighter")
PER_PAGE = 8


def _kb(page: int) -> M:
    names = fighters.stems()
    pages = max(1, -(-len(names) // PER_PAGE))
    page = max(0, min(page, pages - 1))
    chunk = names[page * PER_PAGE:(page + 1) * PER_PAGE]
    rows = [[B(text=fighters.label(n), callback_data=f"fg:s:{n}")] for n in chunk]
    nav = []
    if page > 0:
        nav.append(B(text="⬅️", callback_data=f"fg:p:{page - 1}"))
    nav.append(B(text=f"{page + 1}/{pages}", callback_data="fg:n"))
    if page < pages - 1:
        nav.append(B(text="➡️", callback_data=f"fg:p:{page + 1}"))
    rows += [nav, [B(text="👤 Use my Telegram photo", callback_data="fg:x")]]
    return M(inline_keyboard=rows)


@router.message(Command("fighter"))
async def cmd_fighter(message: Message) -> None:
    if not fighters.stems():
        await message.answer("🤼 No wrestler pictures have been added yet.")
        return
    await message.answer("🤼 <b>Pick your wrestler</b> — shown on your winner / champion / standings cards.", reply_markup=_kb(0))


@router.callback_query(F.data.startswith("fg:"))
async def cb_fighter(cb: CallbackQuery) -> None:
    parts = (cb.data or "").split(":", 2)
    act = parts[1] if len(parts) > 1 else ""
    if act == "p" and len(parts) == 3 and parts[2].isdigit():
        await cb.answer()
        await cb.message.edit_reply_markup(reply_markup=_kb(int(parts[2])))
    elif act == "s" and len(parts) == 3 and parts[2] in fighters.stems():
        await fighters.choose(cb.from_user.id, parts[2])
        await cb.answer("✅ Saved!")
        data = fighters.image_bytes(parts[2])
        if data:
            await cb.message.answer_photo(BufferedInputFile(data, "fighter.jpg"), caption=f"✅ Your wrestler: <b>{fighters.label(parts[2])}</b>")
    elif act == "x":
        await fighters.choose(cb.from_user.id, None)
        await cb.answer("👤 Using your Telegram photo again.", show_alert=True)
    else:
        await cb.answer()
