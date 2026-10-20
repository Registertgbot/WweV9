from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M

from services import rules
from services import tag_rules as tr
from services.rules import Move
from services.tag_engine import TAG, TagSnap, TPlayerSnap, tag_move


def lobby_kb(match_id: int, bot_url: str) -> M:
    return M(inline_keyboard=[
        [B(text=f"{tr.TEAM_EMOJI[1]} Join Lions", callback_data=f"tl:j1:{match_id}"),
         B(text=f"{tr.TEAM_EMOJI[2]} Join Tigers", callback_data=f"tl:j2:{match_id}")],
        [B(text="🚪 Leave", callback_data=f"tl:l:{match_id}"),
         B(text="▶️ Start (host)", callback_data=f"tl:s:{match_id}")],
        [B(text="🔔 Open Bot", url=bot_url)],
    ])


def board_kb(match_id: int) -> M:
    return M(inline_keyboard=[[B(text="🔄 Refresh Board", callback_data=f"tl:b:{match_id}"),
                               B(text="📜 Rules", callback_data="tl:r:0")]])


def _label(move: Move, me: TPlayerSnap) -> str:
    base = f"{rules.MOVE_EMOJI[move]} {rules.MOVE_NAMES[move]}"
    if move == Move.FINISHER:
        base += f" ({me.finishers_left}/{rules.MAX_FINISHERS})"
    blocked = (move == Move.FINISHER and me.finishers_left <= 0) or me.last_attack == move.value
    return f"🚫 {base}" if blocked else base


def move_kb(snap: TagSnap, viewer_id: int) -> M:
    me, t = snap.player(viewer_id), snap.turn_number

    def btn(move: Move) -> B:
        return B(text=_label(move, me), callback_data=f"tm:{snap.id}:{t}:{move.value}")

    resting = snap.aprons(me.team)
    if not resting:
        tag_rows = [[B(text="🚫 No partner left", callback_data=f"tm:{snap.id}:{t}:{TAG}")]]
    elif snap.tag_locked(me.team):
        tag_rows = [[B(text="🚫 TAG (attack first)", callback_data=f"tm:{snap.id}:{t}:{TAG}")]]
    else:
        tag_rows = [[B(text=f"🔄 TAG {p.name[:14]} • ❤️ {p.hp}", callback_data=f"tm:{snap.id}:{t}:{tag_move(p.user_id)}")]
                    for p in resting]
    return M(inline_keyboard=[
        [btn(Move.FINISHER)],
        [btn(Move.F5), btn(Move.SPEAR)],
        [btn(Move.SUPERKICK), btn(Move.DDT)],
        *tag_rows,
        [B(text="📊 My Status", callback_data=f"tx:{snap.id}")],
    ])


def incoming_kb(snap: TagSnap, viewer_id: int) -> M:
    me, t = snap.player(viewer_id), snap.turn_number
    return M(inline_keyboard=[
        [B(text=f"🛡️ REVERSAL • {int(rules.REVERSAL_CHANCE * 100)}% • {me.reversals_left} left",
           callback_data=f"td:{snap.id}:{t}:r")],
        [B(text="😤 Take the hit", callback_data=f"td:{snap.id}:{t}:t")],
        [B(text="📊 My Status", callback_data=f"tx:{snap.id}")],
    ])
