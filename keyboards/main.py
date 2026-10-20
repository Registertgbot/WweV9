from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M


def welcome_kb() -> M:
    return M(inline_keyboard=[
        [B(text="🥊 How to Play", callback_data="menu:help"), B(text="👤 My Profile", callback_data="menu:profile")],
        [B(text="🏆 Leaderboard", callback_data="menu:leaderboard"), B(text="📜 Match History", callback_data="menu:history")],
    ])


def back_kb() -> M:
    return M(inline_keyboard=[[B(text="⬅️ Back", callback_data="menu:home")]])


def pager_kb(prefix: str, page: int, pages: int) -> M:
    row = []
    if page > 0:
        row.append(B(text="◀️ Prev", callback_data=f"{prefix}:{page - 1}"))
    if page + 1 < pages:
        row.append(B(text="Next ▶️", callback_data=f"{prefix}:{page + 1}"))
    rows = [row] if row else []
    rows.append([B(text="⬅️ Back", callback_data="menu:home")])
    return M(inline_keyboard=rows)


def open_bot_kb(url: str, text: str = "📩 Open the bot") -> M:
    return M(inline_keyboard=[[B(text=text, url=url)]])
