from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M


def accept_kb(match_id: int) -> M:
    return M(inline_keyboard=[[B(text="🥊 ACCEPT CHALLENGE", callback_data=f"ch:{match_id}")]])


def accepted_kb(url: str) -> M:
    return M(inline_keyboard=[[B(text="🔔 Enter the Arena", url=url), B(text="📜 View Rules", callback_data="rules")]])


def scoreboard_kb(match_id: int) -> M:
    return M(inline_keyboard=[[B(text="🔄 Refresh Scoreboard", callback_data=f"sb:{match_id}"),
                               B(text="📜 Match Rules", callback_data="rules")]])


def resume_kb(match_id: int) -> M:
    return M(inline_keyboard=[[B(text="▶️ Resume Match", callback_data=f"rs:{match_id}")]])


def cancel_confirm_kb(match_id: int) -> M:
    return M(inline_keyboard=[[B(text="✅ Agree to cancel", callback_data=f"cx:{match_id}:y"),
                               B(text="❌ Keep fighting", callback_data=f"cx:{match_id}:n")]])
