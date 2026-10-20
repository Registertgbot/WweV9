from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M

from services import mitb_rules as mr
from services.hub import HubStatus

BACK = [B(text="⬅️ Back", callback_data="hub:home")]


def _mode(emoji: str, name: str, running: bool, action: str) -> B:
    if running:
        return B(text=f"⏳ {name} (running)", callback_data="hub:busy")
    return B(text=f"{emoji} {name}", callback_data=action)


def main_kb(status: HubStatus | None = None) -> M:
    st = status or HubStatus()
    return M(inline_keyboard=[
        [B(text="🥊 1v1 Match", callback_data="hub:m1"), _mode("🔔", "Royal Rumble", st.rumble, "hub:rumble")],
        [_mode("🏷️", "Tag Team", st.tag, "hub:tag"), _mode("💼", "Money in the Bank", st.mitb, "hub:mitb")],
        [B(text="🏆 Leaderboards", callback_data="hub:lb"), B(text="🏆 Tournament", callback_data="hub:tour")],
        [B(text="👑 Championships", callback_data="hub:belts")],
    ])


def belts_kb() -> M:
    from services.titles import BELTS
    rows = [[B(text=f"{b.emoji} {b.name}" + (" (2v2)" if b.kind == "tag" else ""), callback_data=f"hub:bt:{b.key}")]
            for b in BELTS.values()]
    rows.append([B(text="📋 Title holders", callback_data="hub:bth")])
    rows.append(BACK)
    return M(inline_keyboard=rows)


def tag_kb() -> M:
    return M(inline_keyboard=[
        [B(text="🦁 2v2 🐯", callback_data="hub:tg:2"), B(text="🦁 3v3 🐯", callback_data="hub:tg:3"),
         B(text="🦁 4v4 🐯", callback_data="hub:tg:4")],
        BACK,
    ])


def mitb_kb() -> M:
    rows = [[B(text=f"⚡ {size} players", callback_data=f"hub:mb:{size}:fast"),
             B(text=f"🐢 {size} • slow", callback_data=f"hub:mb:{size}:slow")] for size in mr.SIZES]
    rows.append(BACK)
    return M(inline_keyboard=rows)


def lb_kb() -> M:
    return M(inline_keyboard=[
        [B(text="🥊 1v1", callback_data="hub:lb:1"), B(text="🔔 Rumble", callback_data="hub:lb:r")],
        [B(text="🏷️ Tag Team", callback_data="hub:lb:t"), B(text="💼 MITB", callback_data="hub:lb:m")],
        BACK,
    ])


def help_kb() -> M:
    return M(inline_keyboard=[
        [B(text="🥊 1v1", callback_data="hub:h:1"), B(text="🔔 Rumble", callback_data="hub:h:r")],
        [B(text="🏷️ Tag Team", callback_data="hub:h:t"), B(text="💼 MITB", callback_data="hub:h:m")],
        BACK,
    ])
