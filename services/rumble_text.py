"""All Royal Rumble text (HTML): lobby, live ring board, event captions and DM prompts."""
from __future__ import annotations

from config import get_settings
from services import rules
from services import rumble_rules as rr
from services.rules import Move
from services.rumble_engine import REvent, RPlayerSnap, RumbleSnap
from services.scoreboard import LINE, esc

COLORS = ["🔴", "🔵", "🟢", "🟡", "🟣", "🟠", "⚪", "🟤", "⚫", "🔶", "🔷", "💠"]


def _c(p: RPlayerSnap) -> str:
    return COLORS[(p.entry_order - 1) % len(COLORS)] if p.entry_order else "👤"


def _n(snap: RumbleSnap, user_id: int | None) -> str:
    p = snap.player(user_id)
    return f"<b>{esc(p.name)}</b>" if p else "<b>Unknown Superstar</b>"


def _fmt_move(move: str) -> str:
    m = Move(move)
    return f"{rules.MOVE_EMOJI[m]} {rules.MOVE_NAMES[m]}"


# ------------------------------------------------------------------------ lobby

def lobby_text(snap: RumbleSnap) -> str:
    s = get_settings()
    names = "\n".join(f"{i}. {esc(p.name)}" for i, p in enumerate(snap.players, start=1))
    return (f"🔔 <b>ROYAL RUMBLE — ENTER THE RING!</b>\n\n"
            f"👑 {_n(snap, snap.host_id)} • 👥 {len(snap.players)}/{s.rumble_max_players}\n{names}\n\n"
            f"❤️ {rr.RUMBLE_HP} HP • KO = +{rr.ADRENALINE_HEAL} HP • last one standing wins\n"
            f"⏳ {s.rumble_lobby_timeout}s • min {s.rumble_min_players} • 📩 press Start in the bot DM first")


def lobby_closed_text(snap: RumbleSnap, why: str) -> str:
    return f"🔔 <b>ROYAL RUMBLE</b>\n{why}"


def rules_text() -> str:
    s = get_settings()
    return (f"🔔 <b>ROYAL RUMBLE — RULES</b>\n\n"
            f"• {s.rumble_min_players}-{s.rumble_max_players} wrestlers; two start, a new one enters every {s.rumble_entry_interval}s\n"
            f"• {rr.RUMBLE_HP} HP, 1 Finisher, {rules.MAX_REVERSALS} Reversals each\n"
            f"• Pick a move, then a target; the target may Reversal\n"
            f"• Eliminate someone = +{rr.ADRENALINE_HEAL} HP\n"
            f"• Idle {rr.AFK_LIMIT} turns in a row = eliminated\n"
            f"• Last one standing wins 🏆")


# --------------------------------------------------------------------- live board

def _row(p: RPlayerSnap, active: bool) -> str:
    mark = "🎯" if active else "▫️"
    return (f"{mark} <b>#{p.entry_order}</b> {_c(p)} {esc(p.name)} — ❤️ {p.hp}/{rr.RUMBLE_HP} "
            f"🔥{p.finishers_left} 🛡️{p.reversals_left} 💀{p.eliminations}")


def board_text(snap: RumbleSnap) -> str:
    head = "🏆 <b>ROYAL RUMBLE — FINISHED</b>" if snap.status == "completed" else "🔔 <b>ROYAL RUMBLE — LIVE</b>"
    ring = snap.ring
    lines = [head, "", f"🥊 <b>In the ring ({len(ring)})</b>"]
    lines += [_row(p, p.user_id == snap.active_player_id) for p in ring] or ["(empty)"]
    waiting = snap.waiting
    if waiting:
        lines += ["", f"🚪 To enter: {len(waiting)} (next #{waiting[0].entry_order})"]
    out = snap.out
    if out:
        shown = ", ".join(f"{p.placement}. {esc(p.name)}" for p in out[:3])
        more = f" +{len(out) - 3} more" if len(out) > 3 else ""
        lines += ["", f"💀 Out ({len(out)}): {shown}{more}"]
    if snap.status == "completed" and snap.winner_id:
        lines += ["", f"👑 Champion: {_n(snap, snap.winner_id)}"]
    elif snap.status == "active" and snap.active_player_id:
        lines += ["", f"🎯 Turn {snap.turn_number}: {_n(snap, snap.active_player_id)}"]
    return "\n".join(lines)


# --------------------------------------------------------------- group captions

def started_caption(snap: RumbleSnap) -> str:
    order = " • ".join(f"#{p.entry_order} {esc(p.name)}" for p in sorted(snap.players, key=lambda x: x.entry_order))
    return f"🔔 <b>THE ROYAL RUMBLE BEGINS!</b>\n\n📋 {order}"


def entry_caption(snap: RumbleSnap, ev: REvent) -> str:
    left = len(snap.waiting)
    tail = f" • 🚪 {left} more" if left else " • last entrant!"
    return f"🚪 <b>#{ev.number} ENTERS!</b> {_c(snap.player(ev.user_id))} {_n(snap, ev.user_id)}{tail}"


def turn_caption(snap: RumbleSnap, ev: REvent) -> str:
    a, d = _n(snap, ev.user_id), _n(snap, ev.other_id)
    move = _fmt_move(ev.move)
    note = " ⏱️ no Reversal in time" if ev.auto else ""
    if ev.reversed:
        return (f"🛡️ <b>REVERSAL!</b>{note}\n{d} turned {a}'s {move} around!\n"
                f"💥 {ev.reflected} back to {a} → ❤️ {ev.hp_attacker}/{rr.RUMBLE_HP}")
    return (f"{move}!{note}\n{a} hits {d} for <b>{ev.damage}</b> → "
            f"❤️ {ev.hp_defender}/{rr.RUMBLE_HP}")


def skip_caption(snap: RumbleSnap, ev: REvent) -> str:
    return f"💤 {_n(snap, ev.user_id)} froze and lost the turn!"


def elimination_caption(snap: RumbleSnap, ev: REvent) -> str:
    victim = _n(snap, ev.user_id)
    left = snap.alive_count
    if ev.reason == "afk":
        return f"🚪 {victim} <b>eliminated</b> for inactivity • 🧍 {left} left"
    killer = _n(snap, ev.other_id)
    how = "OVER THE TOP ROPE" if ev.finisher_kill else "ELIMINATED"
    heal = f" • 💚 +{ev.healed} HP" if ev.healed else ""
    return f"💀 <b>{how}!</b> {victim} (#{ev.placement}) by {killer}{heal} • 🧍 {left} left"


def final_two_caption(snap: RumbleSnap) -> str:
    names = " vs ".join(f"<b>{esc(p.name)}</b>" for p in snap.players if not p.eliminated)
    return f"🔥 <b>FINAL TWO!</b>\n{names}"


def winner_caption(snap: RumbleSnap) -> str:
    w = snap.player(snap.winner_id)
    return (f"🏆 <b>{esc(w.name)} WINS THE ROYAL RUMBLE!</b>\n\n"
            f"❤️ {w.hp}/{rr.RUMBLE_HP} • 💀 {w.eliminations} • 💥 {w.damage_dealt}")


# ------------------------------------------------------------------- DM prompts

def move_prompt(snap: RumbleSnap, viewer_id: int) -> str:
    me = snap.player(viewer_id)
    foes = ", ".join(f"{esc(p.name)} ({p.hp})" for p in snap.ring if p.user_id != viewer_id)
    return (f"🎯 <b>YOUR TURN</b>\n\n❤️ {me.hp}/{rr.RUMBLE_HP} • 🔥 {me.finishers_left}/{rules.MAX_FINISHERS} • "
            f"🛡️ {me.reversals_left}/{rules.MAX_REVERSALS}\n🥊 {foes}\n\nPick a move, then a target.")


def target_prompt(snap: RumbleSnap) -> str:
    return f"🎯 <b>{_fmt_move(snap.pending_move)}</b> — who do you hit?"


def attack_sent_text(snap: RumbleSnap) -> str:
    return f"{_fmt_move(snap.pending_move)} sent at {_n(snap, snap.pending_target_id)}…"


def incoming_text(snap: RumbleSnap, viewer_id: int, window: int) -> str:
    me = snap.player(viewer_id)
    return (f"⚠️ <b>INCOMING ATTACK!</b>\n\n{_n(snap, snap.active_player_id)} is coming for you!\n"
            f"❤️ {me.hp}/{rr.RUMBLE_HP} • 🛡️ Reversals {me.reversals_left} • ⏱️ {window}s")


def eliminated_dm(snap: RumbleSnap, ev: REvent) -> str:
    who = f" by {_n(snap, ev.other_id)}" if ev.other_id else " for inactivity"
    return f"💀 <b>Eliminated{who}.</b> You finished <b>#{ev.placement}</b>. Well fought!"


def winner_dm(snap: RumbleSnap) -> str:
    return "🏆 <b>YOU WON THE ROYAL RUMBLE!</b> 👑"


def status_alert(snap: RumbleSnap, viewer_id: int) -> str:
    me = snap.player(viewer_id)
    if me is None:
        return "🚫 You're not in this Rumble."
    where = "in the ring" if me.in_ring else ("eliminated" if me.eliminated else f"waiting (#{me.entry_order})")
    return (f"#{me.entry_order} {me.name[:20]} — {where}\n❤️ {me.hp}/{rr.RUMBLE_HP}  🔥 {me.finishers_left}  "
            f"🛡️ {me.reversals_left}\n💀 Eliminations: {me.eliminations}\n"
            f"Ring: {len(snap.ring)} • Waiting: {len(snap.waiting)} • Out: {len(snap.out)}")


def leaderboard_text(rows) -> str:
    if not rows:
        return "🏆 <b>ROYAL RUMBLE LEADERS</b>\n\nNo Rumble has finished yet. Start one with /rumble!"
    lines = [f"{i}. <b>{esc(n)}</b> — 🏆 {w} wins • 💀 {e} elims • {c} played" for i, (n, w, e, c) in enumerate(rows, 1)]
    return "🏆 <b>ROYAL RUMBLE LEADERS</b>\n\n" + "\n".join(lines)
