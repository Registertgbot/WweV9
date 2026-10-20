"""All dynamic match text: challenge cards, scoreboard, move prompts, event captions (HTML).

Texts are kept short on purpose so every message fits on one phone screen.
"""
from __future__ import annotations

from html import escape

from services import fancy as F
from services import rules
from services.match_engine import MatchSnap, PlayerSnap, TurnSnap
from services.rules import Move

LINE = "━━━━━━━━━━━━━━━━━━"
MAX_HP = rules.MAX_HP


def esc(text: str | None) -> str:
    return escape(text or "", quote=False)


def hp_bar(hp: int, width: int = 10) -> str:
    filled = round(width * max(hp, 0) / MAX_HP)
    return "▰" * filled + "▱" * (width - filled)


def _n(snap: MatchSnap, user_id: int) -> str:
    p = snap.player(user_id)
    return esc(p.name) if p else "Unknown Superstar"


def _box(lines: list[str]) -> str:
    """A quote box that ends its own line (text can follow directly)."""
    return "<blockquote>" + "\n".join(lines) + "</blockquote>\n"


def _hp(me: int, opp: int, opp_name: str) -> str:
    return f"❤️ You {me} • 💔 {opp_name} {opp}"


# ---------------------------------------------------------------- challenge flow

def challenge_text(challenger_name: str, group_title: str | None, timeout: int) -> str:
    return (f"🥊 {F.sans_bold('OPEN CHALLENGE')}\n"
            f"{_box([f'👤 <b>{esc(challenger_name)}</b> • ❤️ {MAX_HP} HP'])}"
            f"🔥 {F.small_caps('who steps into the ring')}? • ⏳ {timeout}s")


def challenge_closed_text(snap: MatchSnap, why: str) -> str:
    return f"🥊 {F.sans_bold('CHALLENGE CLOSED')}\n👤 {_n(snap, snap.challenger_id)} • {why}"


def accepted_text(snap: MatchSnap) -> str:
    a, b = snap.players[0], snap.players[1]
    return f"🔔 {F.sans_bold('CHALLENGE ACCEPTED')}\n🥊 <b>{esc(a.name)}</b> 🆚 <b>{esc(b.name)}</b>"


def dm_required_text(snap: MatchSnap, missing: list[int]) -> str:
    names = ", ".join(f"<b>{_n(snap, u)}</b>" for u in missing)
    return f"📩 {names}: open the bot privately and press <b>Start</b> — the match begins when everyone is ready."


def live_text(snap: MatchSnap) -> str:
    a, b = snap.players[0], snap.players[1]
    return (f"🔔 {F.sans_bold('MATCH IS LIVE')}\n"
            f"{_box([f'🔴 <b>{esc(a.name)}</b> • ❤️ {a.hp}', f'🔵 <b>{esc(b.name)}</b> • ❤️ {b.hp}'])}"
            f"⚡ <b>{_n(snap, snap.active_player_id)}</b> {F.small_caps('attacks first')}")


def scoreboard_text(snap: MatchSnap) -> str:
    colors = ["🔴", "🔵"]
    rows = []
    for i, p in enumerate(snap.players):
        wild = " 🎴" if p.wild_draws_left else ""
        rows.append(f"{colors[i % 2]} <b>{esc(p.name)}</b> • ❤️ {p.hp}/{MAX_HP}\n"
                    f"{hp_bar(p.hp)}  🔥{p.finishers_left} 🛡️{p.reversals_left}{wild}")
    if snap.status == "active" and snap.active_player_id:
        footer = f"⚡ {F.small_caps('turn')}: <b>{_n(snap, snap.active_player_id)}</b>"
    elif snap.status == "paused":
        footer = f"⏸️ {F.small_caps('paused')}"
    elif snap.status == "completed":
        footer = f"🏆 {F.small_caps('winner')}: <b>{_n(snap, snap.winner_id)}</b>"
    else:
        footer = f"🎯 {esc(snap.status.upper())}"
    return f"🥊 {F.sans_bold('LIVE MATCH')}\n{_box(rows)}{footer}"


# --------------------------------------------------------------------- DM prompts

def move_prompt_text(snap: MatchSnap, viewer_id: int) -> str:
    me, opp = snap.player(viewer_id), snap.other(viewer_id)
    wild = "ready" if me.wild_draws_left else "used"
    return (f"🎯 {F.sans_bold('YOUR TURN')}\n"
            f"{_box([_hp(me.hp, opp.hp, esc(opp.name)), f'🔥 {me.finishers_left}/{rules.MAX_FINISHERS} • 🛡️ {me.reversals_left} • 🎴 {wild}'])}"
            f"🔥40 • 💣30 • 💥25 • ⚡10 • 💢5\n"
            f"⏳ {_secs(snap)}s • {F.small_caps('no repeats')}")


def _secs(snap: MatchSnap) -> str:
    from models import utcnow
    if snap.deadline_at is None:
        return "—"
    return str(max(0, int((snap.deadline_at - utcnow()).total_seconds())))


def waiting_text(snap: MatchSnap, viewer_id: int) -> str:
    me, opp = snap.player(viewer_id), snap.other(viewer_id)
    return f"⏳ {F.small_caps('waiting for')} <b>{esc(opp.name)}</b>…\n{_hp(me.hp, opp.hp, esc(opp.name))}"


def attack_sent_text(move: Move) -> str:
    return f"✅ <b>{rules.MOVE_EMOJI[move]} {rules.MOVE_NAMES[move]}</b> locked in • {F.small_caps('waiting')}…"


def wild_attack_text(move: Move) -> str:
    return f"🎴 <b>WILD DRAW</b> → <b>{rules.MOVE_EMOJI[move]} {rules.MOVE_NAMES[move]}</b> locked in…"


def wild_reversal_text() -> str:
    return f"🎴 <b>WILD DRAW</b> → 🛡️ <b>REVERSAL</b>: bonus charge! You still attack this turn."


def incoming_text(snap: MatchSnap, viewer_id: int, move: Move | None, window: int) -> str:
    me, att = snap.player(viewer_id), snap.other(viewer_id)
    what = (f"{rules.MOVE_EMOJI[move]} <b>{rules.MOVE_NAMES[move]}</b>" if move else "<b>a move</b>")
    return (f"⚠️ {F.sans_bold('INCOMING ATTACK')}\n<b>{esc(att.name)}</b> hits you with {what}!\n"
            f"❤️ {me.hp} • 🛡️ {me.reversals_left} • ⏳ {window}s")


def status_alert(snap: MatchSnap, viewer_id: int) -> str:
    me, opp = snap.player(viewer_id), snap.other(viewer_id)
    turn_of = _plain(snap, snap.active_player_id)
    return (f"Turn {snap.turn_number} • {turn_of}'s move\n"
            f"You: {me.hp} HP • F {me.finishers_left}/{rules.MAX_FINISHERS} • R {me.reversals_left} • 🎴 {me.wild_draws_left}\n"
            f"{_plain(snap, opp.user_id)}: {opp.hp} HP • F {opp.finishers_left}/{rules.MAX_FINISHERS} • R {opp.reversals_left} • 🎴 {opp.wild_draws_left}")[:200]


def _plain(snap: MatchSnap, uid: int | None) -> str:
    p = snap.player(uid) if uid else None
    return (p.name if p else "—")[:24]


# ------------------------------------------------------------------ event captions

STYLE: dict[str, tuple[str, str]] = {
    "spear": ("💥 <b>SPEAR! SPEAR! SPEAR!</b>", ""),
    "f5": ("💣 <b>F5! WHAT A SLAM!</b>", ""),
    "superkick": ("⚡ <b>SUPER KICK!</b>", ""),
    "ddt": ("💢 <b>DDT! HEAD FIRST!</b>", ""),
}


def event_caption(snap: MatchSnap, t: TurnSnap) -> str:
    att, dfn = snap.player(t.attacker_id), snap.player(t.defender_id)
    an, dn = esc(att.name), esc(dfn.name)
    move = Move(t.move)
    mname = rules.MOVE_NAMES[move]
    prefix = f"🎴 <b>WILD DRAW — {mname}!</b>\n" if t.wild else ""
    hp = _box([f"❤️ {an} {att.hp}/{MAX_HP} • 💔 {dn} {dfn.hp}/{MAX_HP}"])
    if t.reversed:
        return (f"{prefix}🛡️ {F.sans_bold('REVERSED!')}\n<b>{dn}</b> countered <b>{an}</b>'s {mname} • "
                f"↩️ −{t.damage_to_attacker}\n{hp}").rstrip()
    gamble = f"🛡️❌ {dn}'s reversal failed!\n" if t.reversal_attempted else ""
    title = "🔥 <b>FINISHER CONNECTS!</b>" if move == Move.FINISHER else STYLE[t.move][0]
    return f"{prefix}{title}\n{gamble}<b>{an}</b> ➜ <b>{dn}</b> • −{t.damage_to_defender}\n{hp}".rstrip()


def _summary_lines(snap: MatchSnap, t: TurnSnap) -> str:
    s = t.summary
    if s is None:
        return ""
    rows = [f"🔢 {s.total_turns} turns"]
    for p in snap.players:
        rows.append(f"{esc(p.name)} • 💥 {s.damage_dealt.get(p.user_id, 0)} • "
                    f"{s.finishers.get(p.user_id, 0)}🔥 {s.reversals.get(p.user_id, 0)}🛡️")
    return _box(rows)


def knockout_caption(snap: MatchSnap, t: TurnSnap) -> str:
    winner, loser = snap.player(t.winner_id), snap.other(t.winner_id)
    return (f"☠️ {F.sans_bold('KNOCKOUT!')}\n🏆 <b>{esc(winner.name)}</b> ❤️ {winner.hp} • 💀 <s>{esc(loser.name)}</s>\n"
            f"{_summary_lines(snap, t)}").rstrip()


def forfeit_caption(snap: MatchSnap, t: TurnSnap) -> str:
    winner, loser = snap.player(t.winner_id), snap.other(t.winner_id)
    return (f"⏱️ {F.sans_bold('TIMEOUT VICTORY!')}\n🏆 <b>{esc(winner.name)}</b> ❤️ {winner.hp} • "
            f"🚪 <s>{esc(loser.name)}</s> missed the bell\n{_summary_lines(snap, t)}").rstrip()


def dm_result_text(snap: MatchSnap, t: TurnSnap, viewer_id: int) -> str:
    me, opp = snap.player(viewer_id), snap.other(viewer_id)
    mname = f"{rules.MOVE_EMOJI[Move(t.move)]} {rules.MOVE_NAMES[Move(t.move)]}"
    dname = esc(snap.player(t.defender_id).name)
    if t.reversed:
        line = f"🛡️ <b>{dname}</b> reversed {mname}! ↩️ −{t.damage_to_attacker}"
    elif t.reversal_attempted:
        line = f"🛡️❌ <b>{dname}</b>'s reversal failed! {mname} −{t.damage_to_defender}"
    else:
        line = f"{mname} ➜ <b>{dname}</b> • −{t.damage_to_defender}"
    if t.wild:
        line = "🎴 <b>WILD DRAW</b> " + line
    return f"{line}\n{_hp(me.hp, opp.hp, esc(opp.name))}"


def final_dm_text(snap: MatchSnap, viewer_id: int) -> str:
    won = snap.winner_id == viewer_id
    return ("🏆 <b>VICTORY!</b> You're the champion of this ring." if won else
            "☠️ <b>DEFEAT.</b> Use /match in a group for revenge.")


def pause_text(snap: MatchSnap) -> str:
    return (f"⏸️ {F.sans_bold('MATCH PAUSED')}\n<b>{_n(snap, snap.active_player_id)}</b> missed the bell • "
            f"either fighter can press Resume.")


def short_rules() -> str:
    return ("HP 100 • Finisher 40 (max 1) • F5 30 • Spear 25 • Super Kick 10 • DDT 5\n"
            "Reversal: 100% reflect (2 uses). Wild Draw: 1 random move. No repeating an attack.")
