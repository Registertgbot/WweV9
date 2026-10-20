from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M

from services import rules
from services.rules import Move
from services.rumble_engine import RPlayerSnap, RumbleSnap


def lobby_kb(rumble_id: int, bot_url: str) -> M:
    return M(inline_keyboard=[
        [B(text="🥊 JOIN THE RUMBLE", callback_data=f"ru:j:{rumble_id}"),
         B(text="🚪 Leave", callback_data=f"ru:l:{rumble_id}")],
        [B(text="▶️ Start now (host)", callback_data=f"ru:s:{rumble_id}"),
         B(text="🔔 Open Bot", url=bot_url)],
    ])


def board_kb(rumble_id: int) -> M:
    return M(inline_keyboard=[[B(text="🔄 Refresh Board", callback_data=f"ru:b:{rumble_id}"),
                               B(text="📜 Rules", callback_data="ru:r:0")]])


def _label(move: Move, me: RPlayerSnap) -> str:
    base = f"{rules.MOVE_EMOJI[move]} {rules.MOVE_NAMES[move]}"
    if move == Move.FINISHER:
        base += f" ({me.finishers_left}/{rules.MAX_FINISHERS})"
    blocked = (move == Move.FINISHER and me.finishers_left <= 0) or me.last_attack == move.value
    return f"🚫 {base}" if blocked else base


def move_kb(snap: RumbleSnap, viewer_id: int) -> M:
    me, t = snap.player(viewer_id), snap.turn_number

    def btn(move: Move) -> B:
        return B(text=_label(move, me), callback_data=f"rm:{snap.id}:{t}:{move.value}")

    return M(inline_keyboard=[
        [btn(Move.FINISHER)],
        [btn(Move.F5), btn(Move.SPEAR)],
        [btn(Move.SUPERKICK), btn(Move.DDT)],
        [B(text="📊 My Status", callback_data=f"rx:{snap.id}")],
    ])


def target_kb(snap: RumbleSnap, viewer_id: int) -> M:
    t = snap.turn_number
    rows = [[B(text=f"🎯 #{p.entry_order} {p.name[:18]} • ❤️ {p.hp}",
               callback_data=f"rt:{snap.id}:{t}:{p.user_id}")]
            for p in snap.ring if p.user_id != viewer_id]
    rows.append([B(text="⬅️ Change move", callback_data=f"rk:{snap.id}:{t}")])
    return M(inline_keyboard=rows)


def incoming_kb(snap: RumbleSnap, viewer_id: int) -> M:
    me, t = snap.player(viewer_id), snap.turn_number
    return M(inline_keyboard=[
        [B(text=f"🛡️ REVERSAL • {int(rules.REVERSAL_CHANCE * 100)}% • {me.reversals_left} left",
           callback_data=f"rr:{snap.id}:{t}:r")],
        [B(text="😤 Take the hit", callback_data=f"rr:{snap.id}:{t}:t")],
        [B(text="📊 My Status", callback_data=f"rx:{snap.id}")],
    ])
