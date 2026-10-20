"""Tournament keyboards. Callback data: ``tn:<action>[:<tournament id>[:<arg>...]]`` (always < 64 bytes)."""
from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M

from services import tour_rules as tr
from services.tour_engine import FSnap, TSnap

BACK_HUB = [B(text="⬅️ Back", callback_data="hub:home")]


def menu_kb() -> M:
    return M(inline_keyboard=[
        [B(text="🧍 Solo Mode", callback_data="tn:solo"), B(text="👥 Team Mode", callback_data="tn:team")],
        [B(text="📜 History", callback_data="tn:hist"), B(text="📊 My Stats", callback_data="tn:stats")],
        [B(text="🏅 Leaders", callback_data="tn:lead")],
        BACK_HUB,
    ])


def size_kb() -> M:
    sizes = tr.LEAGUE_SIZES
    rows = [[B(text=f"👥 {n}", callback_data=f"tn:mk:{n}") for n in sizes[i:i + 3]] for i in range(0, len(sizes), 3)]
    return M(inline_keyboard=rows + [[B(text="✖️ Cancel", callback_data="tn:mkx")]])


def registration_kb(t: TSnap) -> M:
    return M(inline_keyboard=[
        [B(text="➕ Join", callback_data=f"tn:j:{t.id}"), B(text="➖ Leave", callback_data=f"tn:l:{t.id}")],
        [B(text="🪪 My Card", callback_data="mc:me"), B(text="👑 Admin", callback_data=f"tn:a:{t.id}")],
    ])


def bracket_kb(t: TSnap) -> M:
    return M(inline_keyboard=[
        [B(text="⚔️ My Match", callback_data=f"tn:mm:{t.id}"), B(text="📊 Standings", callback_data=f"tn:sd:{t.id}")],
        [B(text="👑 Admin", callback_data=f"tn:a:{t.id}")],
    ])


def my_match_kb(t: TSnap, f: FSnap | None, user_id: int) -> M | None:
    """Buttons under a fighter's personal card (DM): ask to move the match, or withdraw the open request."""
    if f is None or f.status != tr.SCHEDULED or not (f.p1_id and f.p2_id):
        return None
    rr = t.pending_reschedule(f.id)
    if rr is not None:
        if rr.requester_id == user_id:
            return M(inline_keyboard=[[B(text="↩️ Withdraw request", callback_data=f"tn:rw:{rr.id}")]])
        return M(inline_keyboard=reschedule_answer_kb(rr.id).inline_keyboard)
    return M(inline_keyboard=[[B(text="🔁 Request reschedule", callback_data=f"tn:rq:{t.id}:{f.id}")]])


def reschedule_answer_kb(request_id: int) -> M:
    return M(inline_keyboard=[[B(text="✅ Accept", callback_data=f"tn:ry:{request_id}"),
                               B(text="❌ Decline", callback_data=f"tn:rn:{request_id}")]])


def admin_kb(t: TSnap) -> M:
    i = t.id
    rows: list[list[B]] = []
    if t.status == tr.REGISTRATION:
        rows.append([B(text="⏰ Registration close", callback_data=f"tn:ad:{i}:reg"),
                     B(text="🕒 First match time", callback_data=f"tn:ad:{i}:st")])
        sizes = tr.sizes_for(t.format)
        for j in range(0, len(sizes), 3):
            rows.append([B(text=f"👥 {n}{' ✅' if n == t.max_players else ''}", callback_data=f"tn:ad:{i}:mx:{n}")
                         for n in sizes[j:j + 3]])
        rows.append([B(text="🚀 Start now", callback_data=f"tn:ad:{i}:go"),
                     B(text="🚫 Kick player", callback_data=f"tn:ad:{i}:dq")])
    else:
        rows.append([B(text="📅 Fixtures", callback_data=f"tn:ad:{i}:fx"),
                     B(text="▶️ Resume" if t.paused else "⏸️ Pause", callback_data=f"tn:ad:{i}:{'resume' if t.paused else 'pause'}")])
        rows.append([B(text="⏱️ Timing", callback_data=f"tn:ad:{i}:gaps"),
                     B(text="🕒 Shift start", callback_data=f"tn:ad:{i}:st")])
        rows.append([B(text="🎲 Redraw", callback_data=f"tn:ad:{i}:redraw"),
                     B(text="🚫 Disqualify", callback_data=f"tn:ad:{i}:dq")])
    rows.append([B(text="📣 Announce", callback_data=f"tn:ad:{i}:ann"),
                 B(text="💾 Backup", callback_data=f"tn:ad:{i}:bk")])
    rows.append([B(text="🔄 Refresh", callback_data=f"tn:ad:{i}:home"),
                 B(text="🗑️ Cancel tournament", callback_data=f"tn:ad:{i}:cancel")])
    return M(inline_keyboard=rows)


def confirm_cancel_kb(t: TSnap) -> M:
    return M(inline_keyboard=[[B(text="🗑️ Yes, cancel it", callback_data=f"tn:ad:{t.id}:cancel_yes"),
                               B(text="⬅️ No", callback_data=f"tn:ad:{t.id}:home")]])


def gaps_kb(t: TSnap) -> M:
    i = t.id
    mark = lambda cur, val: " ✅" if cur == val else ""
    return M(inline_keyboard=[
        [B(text=f"🔁 {n}m{mark(t.round_gap_min, n)}", callback_data=f"tn:ad:{i}:rg:{n}") for n in tr.ROUND_GAP_CHOICES[:3]],
        [B(text=f"🔁 {n}m{mark(t.round_gap_min, n)}", callback_data=f"tn:ad:{i}:rg:{n}") for n in tr.ROUND_GAP_CHOICES[3:]],
        [B(text=f"⚔️ gap {n}m{mark(t.match_gap_min, n)}", callback_data=f"tn:ad:{i}:mg:{n}") for n in tr.MATCH_GAP_CHOICES],
        [B(text=f"⌛ {n // 60}m{mark(t.noshow_sec, n)}", callback_data=f"tn:ad:{i}:ns:{n}") for n in tr.NOSHOW_CHOICES],
        [B(text="⬅️ Back", callback_data=f"tn:ad:{i}:home")],
    ])


def fixtures_kb(t: TSnap) -> M:
    """The next 20 unfinished fixtures, soonest first (the Back button is always kept)."""
    pending = [f for f in t.fixtures if f.status != tr.DONE]
    pending.sort(key=lambda f: (f.scheduled_at is None, f.scheduled_at, f.round_no, f.slot))
    rows, row = [], []
    for f in pending[:20]:
        label = f"{t.round_short(f.round_no)}·{f.slot + 1} {tr.fmt_ist_short(f.scheduled_at)}"
        row.append(B(text=label[:30], callback_data=f"tn:fx:{t.id}:{f.id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([B(text="⬅️ Back", callback_data=f"tn:ad:{t.id}:home")])
    return M(inline_keyboard=rows)


def fixture_kb(t: TSnap, f: FSnap) -> M:
    rows = [[B(text="🕒 Change time (IST)", callback_data=f"tn:ft:{t.id}:{f.id}")]]
    if f.p1_id and f.p2_id:
        rows.append([B(text=f"🏁 {t.name(f.p1_id)[:14]} wins", callback_data=f"tn:fw:{t.id}:{f.id}:{f.p1_id}"),
                     B(text=f"🏁 {t.name(f.p2_id)[:14]} wins", callback_data=f"tn:fw:{t.id}:{f.id}:{f.p2_id}")])
    rows.append([B(text="⬅️ Fixtures", callback_data=f"tn:ad:{t.id}:fx")])
    return M(inline_keyboard=rows)


def players_kb(t: TSnap, prefix_action: str) -> M:
    """Pick a player to kick / disqualify (alive players and the waiting list)."""
    rows, row = [], []
    for p in t.players:
        if p.status not in (tr.P_REGISTERED, tr.P_WAITLIST):
            continue
        row.append(B(text=f"{p.name[:16]}", callback_data=f"tn:ad:{t.id}:{prefix_action}:{p.user_id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([B(text="⬅️ Back", callback_data=f"tn:ad:{t.id}:home")])
    return M(inline_keyboard=rows)
