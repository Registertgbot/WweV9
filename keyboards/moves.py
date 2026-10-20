from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M

from services import rules
from services.match_engine import MatchSnap
from services.rules import Move


def _label(move: Move, me) -> str:
    base = f"{rules.MOVE_EMOJI[move]} {rules.MOVE_NAMES[move]}"
    if move == Move.FINISHER:
        base += f" ({me.finishers_left}/{rules.MAX_FINISHERS})"
    blocked = (move == Move.FINISHER and me.finishers_left <= 0) or me.last_attack == move.value
    return f"🚫 {base}" if blocked else base


def move_kb(snap: MatchSnap, viewer_id: int) -> M:
    """Unavailable moves stay visible (marked 🚫) so the server can explain *why* when tapped."""
    me = snap.player(viewer_id)
    t = snap.turn_number

    def btn(move: Move) -> B:
        return B(text=_label(move, me), callback_data=f"mv:{snap.id}:{t}:{move.value}")

    rows = [
        [btn(Move.FINISHER)],
        [btn(Move.F5), btn(Move.SPEAR)],
        [btn(Move.SUPERKICK), btn(Move.DDT)],
    ]
    if me.wild_draws_left > 0:
        rows.append([B(text=f"🎴 WILD DRAW ({me.wild_draws_left}/{rules.MAX_WILD_DRAWS})",
                       callback_data=f"wd:{snap.id}:{t}")])
    rows.append([B(text="📊 Match Status", callback_data=f"ms:{snap.id}")])
    return M(inline_keyboard=rows)


def incoming_kb(snap: MatchSnap, viewer_id: int) -> M:
    me = snap.player(viewer_id)
    t = snap.turn_number
    return M(inline_keyboard=[
        [B(text=f"🛡️ REVERSAL • {int(rules.REVERSAL_CHANCE * 100)}% • {me.reversals_left} left",
           callback_data=f"rv:{snap.id}:{t}:r")],
        [B(text="😤 Take the hit", callback_data=f"rv:{snap.id}:{t}:t")],
        [B(text="📊 Match Status", callback_data=f"ms:{snap.id}")],
    ])
