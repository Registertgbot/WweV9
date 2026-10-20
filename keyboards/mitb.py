from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M

from services import rules
from services.mitb_engine import CLIMB, DuelSnap, EventSnap, MPlayerSnap, DPlayerSnap
from services.rules import Move


def lobby_kb(event_id: int, bot_url: str) -> M:
    return M(inline_keyboard=[
        [B(text="🥊 JOIN TOURNAMENT", callback_data=f"bl:j:{event_id}"),
         B(text="🚪 Leave", callback_data=f"bl:l:{event_id}")],
        [B(text="▶️ Start now (host)", callback_data=f"bl:s:{event_id}"), B(text="🔔 Open Bot", url=bot_url)],
    ])


def board_kb(event_id: int) -> M:
    return M(inline_keyboard=[[B(text="🔄 Refresh Board", callback_data=f"bl:b:{event_id}"),
                               B(text="📜 Rules", callback_data="bl:r:0")]])


def _label(move: Move, me) -> str:
    base = f"{rules.MOVE_EMOJI[move]} {rules.MOVE_NAMES[move]}"
    if move == Move.FINISHER:
        base += f" ({me.finishers_left}/{rules.MAX_FINISHERS})"
    blocked = (move == Move.FINISHER and me.finishers_left <= 0) or me.last_attack == move.value
    return f"🚫 {base}" if blocked else base


def _move_rows(prefix: str, ident: int, turn: int, me) -> list[list[B]]:
    def btn(move: Move) -> B:
        return B(text=_label(move, me), callback_data=f"{prefix}:{ident}:{turn}:{move.value}")
    return [[btn(Move.FINISHER)], [btn(Move.F5), btn(Move.SPEAR)], [btn(Move.SUPERKICK), btn(Move.DDT)]]


def duel_move_kb(duel: DuelSnap, viewer_id: int) -> M:
    me = duel.player(viewer_id)
    rows = _move_rows("dm", duel.id, duel.turn_number, me)
    rows.append([B(text="📊 My Status", callback_data=f"bx:d:{duel.id}")])
    return M(inline_keyboard=rows)


def duel_incoming_kb(duel: DuelSnap, viewer_id: int) -> M:
    me, t = duel.player(viewer_id), duel.turn_number
    return M(inline_keyboard=[
        [B(text=f"🛡️ REVERSAL • {int(rules.REVERSAL_CHANCE * 100)}% • {me.reversals_left} left",
           callback_data=f"dr:{duel.id}:{t}:r")],
        [B(text="😤 Take the hit", callback_data=f"dr:{duel.id}:{t}:t")],
        [B(text="📊 My Status", callback_data=f"bx:d:{duel.id}")],
    ])


def ladder_move_kb(snap: EventSnap, viewer_id: int) -> M:
    me = snap.player(viewer_id)
    rows = _move_rows("lm", snap.id, snap.turn_number, me)
    rows.append([B(text=f"🪜 CLIMB  ({me.rung}/5 → {me.rung + 1}/5)", callback_data=f"lm:{snap.id}:{snap.turn_number}:{CLIMB}")])
    rows.append([B(text="📊 My Status", callback_data=f"bx:e:{snap.id}")])
    return M(inline_keyboard=rows)


def ladder_target_kb(snap: EventSnap, viewer_id: int) -> M:
    t = snap.turn_number
    rows = [[B(text=f"🎯 {p.name[:18]} • 🪜{p.rung} ❤️ {p.hp}", callback_data=f"lt:{snap.id}:{t}:{p.user_id}")]
            for p in snap.ladder if p.user_id != viewer_id]
    rows.append([B(text="⬅️ Change move", callback_data=f"lk:{snap.id}:{t}")])
    return M(inline_keyboard=rows)


def ladder_incoming_kb(snap: EventSnap, viewer_id: int) -> M:
    me, t = snap.player(viewer_id), snap.turn_number
    return M(inline_keyboard=[
        [B(text=f"🛡️ REVERSAL • {int(rules.REVERSAL_CHANCE * 100)}% • {me.reversals_left} left",
           callback_data=f"lr:{snap.id}:{t}:r")],
        [B(text="😤 Take the hit", callback_data=f"lr:{snap.id}:{t}:t")],
        [B(text="📊 My Status", callback_data=f"bx:e:{snap.id}")],
    ])


def cashin_kb(offer_id: int) -> M:
    return M(inline_keyboard=[[B(text="💼 CASH IN NOW!", callback_data=f"cc:{offer_id}")]])
