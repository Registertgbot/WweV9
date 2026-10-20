"""All Tag Team text (HTML) — uses the decorative fonts from services.fancy for headings."""
from __future__ import annotations

from config import get_settings
from services import fancy as F
from services import rules
from services import tag_rules as tr
from services.rules import Move
from services.scoreboard import LINE, esc, hp_bar
from services.tag_engine import TEvent, TPlayerSnap, TagSnap

E = tr.TEAM_EMOJI


def team_title(team: int) -> str:
    return f"{E[team]} {F.script('Team ' + tr.TEAMS[team].title())}"


def team_caps(team: int) -> str:
    return f"{E[team]} {F.sans_bold('TEAM ' + tr.TEAMS[team])}"


def _n(snap: TagSnap, user_id: int | None) -> str:
    p = snap.player(user_id)
    return f"<b>{esc(p.name)}</b>" if p else "<b>Unknown Superstar</b>"


def _tn(snap: TagSnap, user_id: int | None) -> str:
    """Name with its team emoji."""
    p = snap.player(user_id)
    return f"{E[p.team]} {_n(snap, user_id)}" if p else _n(snap, user_id)


def _fmt_move(move: str) -> str:
    m = Move(move)
    return f"{rules.MOVE_EMOJI[m]} {rules.MOVE_NAMES[m]}"


def _quote(lines: list[str]) -> str:
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


# ------------------------------------------------------------------------ lobby

def lobby_text(snap: TagSnap) -> str:
    blocks = []
    for team in (1, 2):
        rows = []
        members = snap.team(team)
        for i in range(snap.team_size):
            if i < len(members):
                crown = " 👑" if members[i].user_id == snap.host_id else ""
                rows.append(f"✅ {esc(members[i].name)}{crown}")
            else:
                rows.append("🔓 <i>open slot</i>")
        blocks.append(f"{team_title(team)}\n{_quote(rows)}")
    s = get_settings()
    size = F.double(f"{snap.team_size}v{snap.team_size}")
    return (f"🏷️ {F.sans_bold('TAG TEAM MATCH')} • {size}\n\n"
            + "\n".join(blocks) +
            f"\n\n❤️ {rules.MAX_HP} {F.small_caps('hp')} • 🪑 {F.small_caps('apron heals')} +{tr.APRON_HEAL}/{F.small_caps('turn')}\n"
            f"⏳ {s.rumble_lobby_timeout}s • 📩 {F.small_caps('press start in the bot dm first')}")


def lobby_closed_text(why: str) -> str:
    return f"🏷️ {F.sans_bold('TAG TEAM MATCH')}\n{why}"


def rules_text() -> str:
    return (f"{F.sans_bold('TAG TEAM — RULES')}\n\n"
            f"• {E[1]} Lions vs {E[2]} Tigers: /tag, /tag 3, /tag 4 — one wrestler per team in the ring\n"
            f"• {rules.MAX_HP} HP, {rules.MAX_FINISHERS} Finisher, {rules.MAX_REVERSALS} Reversals each\n"
            f"• Attack, or TAG your partner in (not twice in a row)\n"
            f"• Apron wrestlers heal +{tr.APRON_HEAL} HP per turn\n"
            f"• Uneven teams: the smaller team gets +1 Reversal each\n"
            f"• Idle {tr.AFK_LIMIT} turns in a row = eliminated\n"
            f"• A whole team down = the other team wins")


# ---------------------------------------------------------------------- live board

def _row(p: TPlayerSnap) -> str:
    if p.eliminated:
        return f"💀 <s>{esc(p.name)}</s>"
    if not p.in_ring:                                    # apron: one compact line
        return f"🪑 {esc(p.name)} ❤️ {p.hp} 🛡️{p.reversals_left}"
    return f"🥊 <b>{esc(p.name)}</b> ❤️ {p.hp}/{rules.MAX_HP} {hp_bar(p.hp, 5)} 🔥{p.finishers_left} 🛡️{p.reversals_left}"


def board_text(snap: TagSnap) -> str:
    head = (f"🏆 {F.sans_bold('TAG TEAM — FINISHED')}" if snap.status == "completed"
            else f"🏷️ {F.sans_bold('TAG TEAM')} • {F.small_caps('live')}")
    blocks = []
    for team in (1, 2):
        mark = " 🎯" if snap.active_team == team and snap.status == "active" else ""
        blocks.append(f"{team_title(team)}{mark}\n{_quote([_row(p) for p in snap.team(team)])}")
    foot = ""
    if snap.status == "completed" and snap.winner_team:
        foot = f"\n\n🏆 {team_caps(snap.winner_team)} {F.sans_bold('WINS!')}"
    elif snap.status == "active" and snap.active_player_id:
        foot = f"\n\n🎯 {F.sans_bold('TURN ' + str(snap.turn_number))} • {_n(snap, snap.active_player_id)}"
    return f"{head}\n\n" + "\n".join(blocks) + foot


# --------------------------------------------------------------- group captions

def started_caption(snap: TagSnap) -> str:
    lions = " & ".join(esc(p.name) for p in snap.team(1))
    tigers = " & ".join(esc(p.name) for p in snap.team(2))
    sizes = F.double(f"{len(snap.team(1))}v{len(snap.team(2))}")
    odd = (f"\n⚖️ {team_caps(1 if len(snap.team(1)) < len(snap.team(2)) else 2)} {F.small_caps('get +1 reversal each')}"
           if len(snap.team(1)) != len(snap.team(2)) else "")
    return (f"🏷️ {F.sans_bold('TAG TEAM MATCH — BEGINS!')} {sizes}\n\n"
            f"{E[1]} <b>{lions}</b>\n{E[2]} <b>{tigers}</b>\n\n"
            f"🔔 {F.small_caps('first turn')}: {team_caps(snap.active_team)}{odd}")


def turn_caption(snap: TagSnap, ev: TEvent) -> str:
    a, d = _tn(snap, ev.user_id), _tn(snap, ev.other_id)
    move = _fmt_move(ev.move)
    note = f" ⏱️ {F.small_caps('no reversal in time')}" if ev.auto else ""
    if ev.reversed:
        return (f"🛡️ {F.sans_bold('REVERSAL!')}{note}\n{d} turned {a}'s {move} around!\n"
                f"💥 {ev.reflected} {F.small_caps('back')} → ❤️ {ev.hp_attacker}/{rules.MAX_HP}")
    return (f"{move}!{note}\n{a} hits {d} for <b>{ev.damage}</b> → "
            f"❤️ {ev.hp_defender}/{rules.MAX_HP}")


def tag_caption(snap: TagSnap, ev: TEvent) -> str:
    return f"🔄 {F.sans_bold('TAG!')} {E[ev.team]} {_n(snap, ev.user_id)} ➜ {_n(snap, ev.other_id)}"


def skip_caption(snap: TagSnap, ev: TEvent) -> str:
    return f"💤 {_tn(snap, ev.user_id)} froze and lost the turn!"


def elimination_caption(snap: TagSnap, ev: TEvent) -> str:
    victim = _tn(snap, ev.user_id)
    replacement = snap.ring(ev.team)
    follow = f"\n🥊 {_n(snap, replacement.user_id)} {F.small_caps('enters the ring')}" if replacement else ""
    if ev.reason == "afk":
        return f"🚪 {victim} — {F.sans_bold('ELIMINATED')} for inactivity!{follow}"
    how = f"{F.sans_bold('KNOCKED OUT')}" + (" 💥" if ev.finisher_kill else "")
    return f"💀 {how} {victim} by {_tn(snap, ev.other_id)}{follow}"


def handicap_caption(snap: TagSnap, ev: TEvent) -> str:
    left = next((p for p in snap.team(ev.team) if not p.eliminated), None)
    name = _n(snap, left.user_id) if left else ""
    odds = len(snap.alive(tr.other_team(ev.team)))
    return f"⚠️ {F.sans_bold('HANDICAP!')} {name} is alone against {odds}!"


def winner_caption(snap: TagSnap) -> str:
    team = snap.winner_team
    names = " & ".join(esc(p.name) for p in snap.team(team))
    stats = [f"{esc(p.name)} — 💀 {p.eliminations} • 💥 {p.damage_dealt} • 🔄 {p.tags_made}"
             for p in snap.team(team)]
    return (f"🏆 {F.sans_bold('TAG TEAM CHAMPIONS!')}\n\n{team_caps(team)}\n<b>{names}</b>\n\n"
            + "\n".join(stats))


# ------------------------------------------------------------------- DM prompts

def move_prompt(snap: TagSnap, viewer_id: int) -> str:
    me = snap.player(viewer_id)
    foe = snap.ring(tr.other_team(me.team))
    resting = snap.aprons(me.team)
    partner_line = (f"🪑 " + ", ".join(f"{esc(p.name)} ❤️ {p.hp}" for p in resting)
                    if resting else f"🪑 {F.small_caps('no partner left')}")
    return (f"🎯 {F.sans_bold('YOUR TURN')} • {team_caps(me.team)}\n\n"
            f"❤️ {me.hp}/{rules.MAX_HP} • 🔥 {me.finishers_left}/{rules.MAX_FINISHERS} • "
            f"🛡️ {me.reversals_left}/{rules.MAX_REVERSALS}\n"
            f"{partner_line}\n"
            f"{E[foe.team]} {esc(foe.name)} ❤️ {foe.hp}\n\n"
            f"{F.small_caps('attack, or tag your partner in')}")


def attack_sent_text(snap: TagSnap) -> str:
    return f"{_fmt_move(snap.pending_move)} sent at {_tn(snap, snap.pending_target_id)}…"


def tag_done_text(snap: TagSnap, user_id: int) -> str:
    me = snap.player(user_id)
    ring = snap.ring(me.team)
    return f"🔄 {F.sans_bold('TAGGED!')} {esc(ring.name) if ring else ''} {F.small_caps('is in the ring')}."


def incoming_text(snap: TagSnap, viewer_id: int, window: int) -> str:
    me = snap.player(viewer_id)
    return (f"⚠️ {F.sans_bold('INCOMING ATTACK!')}\n\n{_tn(snap, snap.active_player_id)} is coming for you!\n"
            f"❤️ {me.hp}/{rules.MAX_HP} • 🛡️ {me.reversals_left} {F.small_caps('left')} • ⏱️ {window}s")


def eliminated_dm(snap: TagSnap, ev: TEvent) -> str:
    return f"💀 {F.sans_bold('YOU WERE KNOCKED OUT')} — {F.small_caps('cheer on your partner')}!"


def result_dm(snap: TagSnap, user_id: int) -> str:
    me = snap.player(user_id)
    if me.team == snap.winner_team:
        return f"🏆 {F.sans_bold('YOUR TEAM WON!')} {E[me.team]}"
    return f"😔 {F.sans_bold('YOUR TEAM LOST')} — go again with /tag!"


def status_alert(snap: TagSnap, viewer_id: int) -> str:
    me = snap.player(viewer_id)
    if me is None:
        return "🚫 You're not in this match."
    where = "OUT" if me.eliminated else ("in the ring" if me.in_ring else "on the apron")
    return (f"{E[me.team]} {me.name[:20]} — {where}\n❤️ {me.hp}/{rules.MAX_HP}  🔥 {me.finishers_left}  "
            f"🛡️ {me.reversals_left}\n🔄 Tags: {me.tags_made}  💀 KOs: {me.eliminations}")


def leaderboard_text(rows) -> str:
    if not rows:
        return f"🏆 {F.sans_bold('TAG TEAM LEADERS')}\n\nNo Tag Team match has finished yet. Start one with /tag!"
    lines = [f"{i}. <b>{esc(n)}</b> — 🏆 {w} wins • {c} played • 💀 {e}" for i, (n, w, c, e) in enumerate(rows, 1)]
    return f"🏆 {F.sans_bold('TAG TEAM LEADERS')}\n\n" + "\n".join(lines)
