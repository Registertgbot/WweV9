"""Money in the Bank text (HTML) with decorative fonts for headings."""
from __future__ import annotations

from services import fancy as F
from services import mitb_rules as mr
from services import rules
from services.mitb_engine import BriefcaseSnap, DuelSnap, EventSnap, MEvent, OfferSnap
from services.rules import Move
from services.scoreboard import LINE, esc, hp_bar
from services.turn_ping import mention

MODE_LABEL = {"fast": "⚡ Fast", "slow": "🐢 Slow"}
TITLE = f"💼 {F.sans_bold('MONEY IN THE BANK')}"


def _fmt_move(move: str) -> str:
    m = Move(move)
    return f"{rules.MOVE_EMOJI[m]} {rules.MOVE_NAMES[m]}"


def _n(snap, user_id: int | None) -> str:
    p = snap.player(user_id)
    return f"<b>{esc(p.name)}</b>" if p else "<b>Unknown Superstar</b>"


def _quote(lines: list[str]) -> str:
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


def _mins(seconds: int) -> str:
    return f"{seconds // 3600}h" if seconds >= 3600 and seconds % 3600 == 0 else f"{max(1, seconds // 60)} min"


# ------------------------------------------------------------------------ lobby

def lobby_text(snap: EventSnap) -> str:
    t = mr.timing(snap.mode)
    names = [f"{i}. {esc(p.name)}" + (" 👑" if p.user_id == snap.host_id else "")
             for i, p in enumerate(snap.players, start=1)]
    return (f"{TITLE}\n\n"
            f"👥 {F.sans_bold('WRESTLERS')} ({len(snap.players)}/{snap.capacity}) • {MODE_LABEL[snap.mode]}\n"
            f"{_quote(names)}\n"
            f"🥊 {F.small_caps('qualifiers')} → 🪜 {F.small_caps('ladder')} → 💼\n"
            f"⏳ {_mins(t.lobby)} • {F.small_caps('min')} {mr.MIN_PLAYERS} • "
            f"📩 {F.small_caps('press start in the bot dm first')}")


def lobby_closed_text(why: str) -> str:
    return f"{TITLE}\n{why}"


def rules_text() -> str:
    return (f"{F.sans_bold('MONEY IN THE BANK — RULES')}\n\n"
            f"• /mitb (8), /mitb 16, /mitb 32 — add `slow` for a day-long mode\n"
            f"• Qualifier: random 1v1 duels in the bot DM until {mr.LADDER_MAX} are left\n"
            f"• Ladder: climb {mr.RUNGS} rungs to grab the briefcase; attacks knock rivals down a rung "
            f"(Finisher: 2); a knockout = back to the floor with {mr.STUN_HP} HP and a missed turn\n"
            f"• The winner holds the briefcase for {mr.BRIEFCASE_DAYS} days: cash in after any 1v1 in the group "
            f"(the target has at least {mr.CASHIN_FLOOR_HP} HP and strikes first)")


# ----------------------------------------------------------------------- bracket

def _duel_line(d: DuelSnap) -> str:
    a, b = d.players
    if d.status == "completed" and d.winner_id:
        w, l = (a, b) if a.user_id == d.winner_id else (b, a)
        tag = {"forfeit": "forfeit", "time_limit": "on points"}.get(d.end_reason or "", "")
        extra = f" ({tag})" if tag else ""
        return f"✅ <b>{esc(w.name)}</b> ❤️{w.hp} def. <s>{esc(l.name)}</s>{extra}"
    return f"⚔️ {esc(a.name)} <i>vs</i> {esc(b.name)}"


def _ladder_rows(snap: EventSnap) -> list[str]:
    rows = []
    for p in snap.ladder:
        bar = "🪜" * p.rung + "▫️" * (mr.RUNGS - p.rung)
        stun = " 😵" if p.stun else ""
        turn = "🎯 " if p.user_id == snap.active_player_id else ""
        rows.append(f"{turn}<b>{esc(p.name)}</b>{stun}\n   {bar} 💼 ❤️ {p.hp} 🔥{p.finishers_left} 🛡️{p.reversals_left}")
    for p in snap.players:
        if p.status == "dq":
            rows.append(f"🚪 <s>{esc(p.name)}</s> — {F.small_caps('disqualified')}")
    return rows


def board_text(snap: EventSnap) -> str:
    mode = MODE_LABEL[snap.mode]
    if snap.status == "completed":
        champ = snap.player(snap.winner_id)
        return f"{TITLE} • {F.small_caps('finished')}\n\n💼 {F.sans_bold('MR. MONEY IN THE BANK')}\n👑 <b>{esc(champ.name)}</b>"
    if snap.status == "ladder":
        return (f"{TITLE} • {F.sans_bold('LADDER MATCH')}\n\n"
                + _quote(_ladder_rows(snap)) +
                f"\n\n🎯 {F.sans_bold('TURN ' + str(snap.turn_number))} • {_n(snap, snap.active_player_id)}")
    # qualifier
    rnd = f"{F.sans_bold('ROUND ' + str(snap.round_no))} / {snap.total_rounds}"
    lines = [_duel_line(d) for d in snap.duels]
    done = sum(1 for d in snap.duels if d.status == "completed")
    byes = [p for p in snap.alive if p.had_bye and not any(p.user_id == x.user_id for d in snap.duels for x in d.players)]
    if byes:
        lines.append("🛌 " + ", ".join(esc(p.name) for p in byes) + f" — {F.small_caps('bye')}")
    return (f"{TITLE} • {mode}\n\n🥊 {rnd}\n{_quote(lines)}\n"
            f"✅ {done}/{len(snap.duels)} • 👥 {len(snap.alive)} {F.small_caps('still in')}")


# --------------------------------------------------------------- group captions

def started_caption(snap: EventSnap) -> str:
    return (f"{TITLE}\n{F.sans_bold('THE TOURNAMENT BEGINS!')}\n\n"
            f"👥 {len(snap.players)} • {MODE_LABEL[snap.mode]} • 🥊 {snap.total_rounds} {F.small_caps('round(s)')} → 🪜 → 💼")


def round_caption(snap: EventSnap, ev: MEvent) -> str:
    lines = []
    for _, a, b in ev.pairs:
        lines.append(f"⚔️ {mention(a, snap.player(a).name)} <i>vs</i> {mention(b, snap.player(b).name)}")
    bye = f"\n🛌 {F.small_caps('bye')}: {mention(ev.bye, snap.player(ev.bye).name)}" if ev.bye else ""
    t = mr.timing(snap.mode)
    return (f"🔔 {F.sans_bold('ROUND ' + str(ev.round_no))} / {snap.total_rounds}\n\n" + "\n".join(lines) + bye +
            f"\n\n📩 {F.small_caps('check your dm')} • ⏱️ {_mins(t.duel_cap)}")


def duel_end_caption(snap: EventSnap, ev: MEvent) -> str:
    w, l = snap.player(ev.user_id), snap.player(ev.other_id)
    how = {"knockout": "by knockout", "forfeit": "by forfeit", "time_limit": "on points"}.get(ev.reason, "")
    left = len(snap.alive)
    return (f"✅ {F.sans_bold('ROUND ' + str(ev.round_no))}: <b>{esc(w.name)}</b> beats <s>{esc(l.name)}</s> {how}\n"
            f"❤️ {ev.hp_a} • 👥 {left} {F.small_caps('still in')}")


def ladder_start_caption(snap: EventSnap) -> str:
    names = " • ".join(f"{p.ladder_order}. {esc(p.name)}" for p in snap.ladder)
    return (f"🪜 {F.sans_bold('LADDER MATCH — FOR THE BRIEFCASE!')}\n\n{names}\n\n"
            f"{F.small_caps('climb 5 rungs to grab')} 💼 • {F.small_caps('max')} {mr.LADDER_ROUNDS_CAP} {F.small_caps('turns each')}")


def climb_caption(snap: EventSnap, ev: MEvent) -> str:
    return (f"🪜 {F.sans_bold('CLIMB!')} {_n(snap, ev.user_id)} → rung <b>{ev.rung}</b>/{mr.RUNGS}\n"
            + "🪜" * ev.rung + "▫️" * (mr.RUNGS - ev.rung) + " 💼")


def attack_caption(snap: EventSnap, ev: MEvent) -> str:
    a, d, move = _n(snap, ev.user_id), _n(snap, ev.other_id), _fmt_move(ev.move)
    note = f" ⏱️ {F.small_caps('no reversal in time')}" if ev.auto else ""
    if ev.reversed:
        return (f"🛡️ {F.sans_bold('REVERSAL!')}{note}\n{d} turned {a}'s {move} around!\n"
                f"💥 {ev.reflected} {F.small_caps('back')} → ❤️ {ev.hp_a}")
    return f"{move}!{note}\n{a} hits {d} for <b>{ev.damage}</b> → ❤️ {ev.hp_d}/{rules.MAX_HP}"


def shaken_caption(snap: EventSnap, ev: MEvent) -> str:
    return f"⬇️ {_n(snap, ev.user_id)} is shaken down to rung <b>{ev.rung}</b>!"


def knockdown_caption(snap: EventSnap, ev: MEvent) -> str:
    return (f"😵 {F.sans_bold('KNOCKED OFF THE LADDER!')} {_n(snap, ev.user_id)} is back on the floor "
            f"with {mr.STUN_HP} HP and misses a turn.")


def stunned_caption(snap: EventSnap, ev: MEvent) -> str:
    return f"😵 {_n(snap, ev.user_id)} is still on the mat and loses the turn."


def skip_caption(snap: EventSnap, ev: MEvent) -> str:
    return f"💤 {_n(snap, ev.user_id)} froze and lost the turn!"


def dq_caption(snap: EventSnap, ev: MEvent) -> str:
    return f"🚪 {_n(snap, ev.user_id)} was too slow — {F.sans_bold('DISQUALIFIED')}!"


def winner_caption(snap: EventSnap, ev: MEvent) -> str:
    how = {"grabbed": "grabs the briefcase!", "time_limit": "takes it on points!", "last_standing": "is the last one standing!"}
    return (f"💼 {F.sans_bold('MR. MONEY IN THE BANK!')}\n\n👑 <b>{esc(snap.player(ev.user_id).name)}</b> "
            f"{how.get(ev.reason, '')}\n\n{F.small_caps('valid for')} {mr.BRIEFCASE_DAYS} {F.small_caps('days')}")


# --------------------------------------------------------------------- cash-in

def offer_text(offer: OfferSnap) -> str:
    return (f"💼 {F.sans_bold('CASH-IN OPPORTUNITY!')}\n\n{mention(offer.holder_id, offer.holder_name)} holds the briefcase!\n"
            f"🎯 <b>{esc(offer.target_name)}</b> ❤️ {mr.cashin_hp(offer.target_hp)} • "
            f"⏱️ {mr.CASHIN_WINDOW}s {F.small_caps('to cash in')}")


def offer_taken_text(offer: OfferSnap) -> str:
    return f"💼 {F.sans_bold('CASHED IN!')} {offer.holder_name} vs {offer.target_name}"


def offer_expired_text(offer: OfferSnap) -> str:
    return f"💼 {F.small_caps('cash-in window closed')} — {esc(offer.holder_name)} {F.small_caps('keeps the briefcase')}"


def cashin_caption(duel: DuelSnap, ev: MEvent) -> str:
    h, t = duel.player(ev.user_id), duel.player(ev.other_id)
    return (f"💼 {F.sans_bold('THE BRIEFCASE IS CASHED IN!')}\n\n"
            f"<b>{esc(h.name)}</b> ❤️ {h.hp}  <i>vs</i>  <b>{esc(t.name)}</b> ❤️ {t.hp}\n"
            f"{F.small_caps('the defender strikes first')}!")


def cashin_end_caption(duel: DuelSnap, ev: MEvent) -> str:
    w = duel.player(ev.user_id)
    if ev.flag:
        return f"👑 {F.sans_bold('CASH-IN SUCCESS!')} <b>{esc(w.name)}</b> is the new champion!"
    return f"💔 {F.sans_bold('CASH-IN FAILED!')} <b>{esc(w.name)}</b> survives!"


def cashin_turn_caption(duel: DuelSnap, ev: MEvent) -> str:
    a, d, move = duel.player(ev.user_id), duel.player(ev.other_id), _fmt_move(ev.move)
    note = f" ⏱️ {F.small_caps('no reversal in time')}" if ev.auto else ""
    if ev.reversed:
        return (f"🛡️ {F.sans_bold('REVERSAL!')}{note}\n<b>{esc(d.name)}</b> turned {esc(a.name)}'s {move} around!\n"
                f"💥 {ev.reflected} → ❤️ {ev.hp_a}")
    return (f"{move}!{note}\n<b>{esc(a.name)}</b> hits <b>{esc(d.name)}</b> for <b>{ev.damage}</b> → "
            f"❤️ {ev.hp_d}/{rules.MAX_HP}")


# ------------------------------------------------------------------- DM prompts

def duel_move_prompt(duel: DuelSnap, viewer_id: int, rounds_label: str) -> str:
    me, foe = duel.player(viewer_id), duel.other(viewer_id)
    t = mr.timing(duel.mode)
    return (f"🎯 {F.sans_bold('YOUR TURN')} • {rounds_label}\n\n"
            f"❤️ {me.hp}/{rules.MAX_HP} • 🔥 {me.finishers_left}/{rules.MAX_FINISHERS} • "
            f"🛡️ {me.reversals_left}/{rules.MAX_REVERSALS}\n"
            f"🥊 {esc(foe.name)} ❤️ {foe.hp}\n\n"
            f"⏱️ {_mins(t.turn)}")


def duel_waiting_text(duel: DuelSnap, viewer_id: int) -> str:
    foe = duel.other(viewer_id)
    return f"⏳ {F.small_caps('waiting for')} <b>{esc(foe.name)}</b>…"


def duel_attack_sent(duel: DuelSnap) -> str:
    return f"{_fmt_move(duel.pending_move)} sent at {esc(duel.other(duel.active_user_id).name)}…"


def duel_incoming(duel: DuelSnap, viewer_id: int) -> str:
    me, foe = duel.player(viewer_id), duel.other(viewer_id)
    return (f"⚠️ {F.sans_bold('INCOMING ATTACK!')}\n\n<b>{esc(foe.name)}</b> is coming for you!\n"
            f"❤️ {me.hp}/{rules.MAX_HP} • 🛡️ {me.reversals_left} {F.small_caps('left')} • "
            f"⏱️ {_mins(mr.timing(duel.mode).reversal)}")


def duel_result_dm(duel: DuelSnap, ev: MEvent, viewer_id: int) -> str:
    a, d = duel.player(ev.user_id), duel.player(ev.other_id)
    move = _fmt_move(ev.move)
    note = f"⏱️ {F.small_caps('no reversal in time')}\n" if ev.auto else ""
    if ev.reversed:
        body = f"🛡️ <b>{esc(d.name)}</b> reversed {esc(a.name)}'s {move}! 💥 {ev.reflected}"
    else:
        body = f"{move}: <b>{esc(a.name)}</b> hits <b>{esc(d.name)}</b> for {ev.damage}"
    me, foe = duel.player(viewer_id), duel.other(viewer_id)
    return f"{note}{body}\n\n❤️ {F.small_caps('you')} {me.hp} • {esc(foe.name)} {foe.hp}"


def advance_dm(ev: MEvent, next_label: str) -> str:
    return f"✅ {F.sans_bold('YOU ADVANCE!')} {next_label}"


def knocked_out_dm(ev: MEvent) -> str:
    how = {"forfeit": "by forfeit", "time_limit": "on points", "knockout": "by knockout"}.get(ev.reason, "")
    return f"💔 {F.sans_bold('YOU ARE OUT')} — lost {how}. Watch the group for the final!"


def ladder_move_prompt(snap: EventSnap, viewer_id: int) -> str:
    me = snap.player(viewer_id)
    bar = "🪜" * me.rung + "▫️" * (mr.RUNGS - me.rung)
    rivals = "\n".join(f"• {esc(p.name)} {'🪜' * p.rung or '▫️'} ❤️ {p.hp}" for p in snap.ladder if p.user_id != viewer_id)
    return (f"🎯 {F.sans_bold('YOUR TURN')} • 🪜 {F.sans_bold('LADDER')}\n\n"
            f"{bar} 💼 ({me.rung}/{mr.RUNGS})\n❤️ {me.hp}/{rules.MAX_HP} 🔥{me.finishers_left} 🛡️{me.reversals_left}\n\n"
            f"{rivals}\n\n{F.small_caps('climb, or attack to knock a rival down')}")


def ladder_target_prompt(snap: EventSnap) -> str:
    return f"🎯 <b>{_fmt_move(snap.pending_move)}</b> — who do you hit?"


def ladder_attack_sent(snap: EventSnap) -> str:
    return f"{_fmt_move(snap.pending_move)} sent at {_n(snap, snap.pending_target_id)}…"


def ladder_incoming(snap: EventSnap, viewer_id: int) -> str:
    me = snap.player(viewer_id)
    return (f"⚠️ {F.sans_bold('INCOMING ATTACK!')}\n\n{_n(snap, snap.active_player_id)} is coming for you!\n"
            f"❤️ {me.hp}/{rules.MAX_HP} • 🪜 {me.rung} • 🛡️ {me.reversals_left} {F.small_caps('left')} • "
            f"⏱️ {_mins(mr.timing(snap.mode).reversal)}")


def status_alert_event(snap: EventSnap, viewer_id: int) -> str:
    me = snap.player(viewer_id)
    if me is None:
        return "🚫 You're not in this tournament."
    return (f"{me.name[:20]} — {me.status}\n🪜 Rung {me.rung}/{mr.RUNGS}  ❤️ {me.hp}/{rules.MAX_HP}\n"
            f"🔥 {me.finishers_left}  🛡️ {me.reversals_left}")


def status_alert_duel(duel: DuelSnap, viewer_id: int) -> str:
    me, foe = duel.player(viewer_id), duel.other(viewer_id)
    if me is None:
        return "🚫 You're not in this match."
    return (f"❤️ {me.hp}/{rules.MAX_HP}  🔥 {me.finishers_left}  🛡️ {me.reversals_left}\n"
            f"🥊 {foe.name[:20]}: ❤️ {foe.hp}")


def briefcase_text(b: BriefcaseSnap | None) -> str:
    if b is None:
        return f"💼 {F.sans_bold('NO BRIEFCASE')}\n\nWin a Money in the Bank tournament (/mitb) to earn one!"
    return (f"💼 {F.sans_bold('YOUR BRIEFCASE')}\n\nValid until <b>{b.expires_at:%d %b %Y, %H:%M} UTC</b>.\n"
            f"Cash in within {mr.CASHIN_WINDOW}s after any 1v1 match in your group!")


def leaderboard_text(rows) -> str:
    if not rows:
        return f"🏆 {F.sans_bold('MITB LEADERS')}\n\nNo tournament has finished yet. Start one with /mitb!"
    lines = [f"{i}. <b>{esc(n)}</b> — 💼 {t} titles • 👑 {w} cash-ins • 💔 {l}" for i, (n, t, w, l) in enumerate(rows, 1)]
    return f"🏆 {F.sans_bold('MITB LEADERS')}\n\n" + "\n".join(lines)
