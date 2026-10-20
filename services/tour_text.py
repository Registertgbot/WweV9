"""Tournament texts (HTML): compact cards with quote boxes, built to fit one phone screen."""
from __future__ import annotations

from services import fancy as F
from services import tour_rules as tr
from services.scoreboard import esc
from services.tour_engine import FSnap, TSnap
from services.turn_ping import mention

TITLE = f"🏆 {F.sans_bold('TOURNAMENT')}"
RESULT_NOTE = {"walkover": " (w/o)", "owner": " (owner)", "timeout": " (timeout)", "bye": ""}
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
VS = "🆚"


def _short(name: str, limit: int = 14) -> str:
    return esc(name if len(name) <= limit else name[:limit - 1] + "…")


def _box(lines: list[str], expandable: bool = False) -> str:
    tag = "blockquote expandable" if expandable else "blockquote"
    return f"<{tag}>" + "\n".join(lines) + "</blockquote>"


def _t(dt) -> str:
    return tr.fmt_ist_compact(dt)


# ---------------------------------------------------------------------------- menus

def menu_caption() -> str:
    return (f"{TITLE}\n\n🧍 {F.sans_bold('SOLO')} — league + playoffs\n"
            f"👥 {F.sans_bold('TEAM')} — {F.small_caps('coming soon')}")


def name_prompt() -> str:
    return "✍️ <b>Send the tournament name</b> as a reply.\n<i>e.g. Summer Slam</i>"


def size_prompt(name: str) -> str:
    return f"🏆 <b>{esc(name)}</b>\n👥 How many players?"


def time_prompt(what: str) -> str:
    return (f"🕒 <b>{what}</b> — reply in <b>IST</b>:\n"
            f"<code>9:30pm</code> • <code>tomorrow 8pm</code> • <code>20-10 9:30pm</code> • <code>+30m</code>")


def announce_prompt() -> str:
    return "📣 Reply with the text to post in the group."


def bad_time() -> str:
    return "❌ Couldn't read that. Try <code>9:30pm</code>, <code>tomorrow 8pm</code>, <code>20-10 21:30</code> or <code>+30m</code>."


# ------------------------------------------------------------------- registration card

def registration_text(t: TSnap) -> str:
    regs = t.registered
    kind = "SOLO LEAGUE" if t.format == tr.LEAGUE else "SOLO KNOCKOUT"
    names = [f"{i}. {_short(p.name, 22)}" for i, p in enumerate(regs, start=1)] or ["<i>no players yet</i>"]
    lines = [f"🏆 <b>{esc(t.title)}</b>", f"🧍 {F.small_caps(kind.lower())}  •  👥 {len(regs)}/{t.max_players}", "",
             _box(names)]
    if t.waitlist:
        lines.append(f"⏳ +{len(t.waitlist)} waiting")
    if t.reg_close_at:
        lines.append(f"⏰ closes {tr.fmt_ist(t.reg_close_at)}")
    if t.start_at:
        lines.append(f"🕒 starts {tr.fmt_ist(t.start_at)}")
    lines += ["", "🏁 win = 3 pts • top 4 → semis → final" if t.format == tr.LEAGUE else "🏁 knockout • lose once and you're out",
              f"📩 {F.small_caps('press start in the bot dm to join')}"]
    return "\n".join(lines)


def registration_closed_text(t: TSnap) -> str:
    n = sum(1 for p in t.players if p.status != tr.P_WAITLIST)
    return f"🔒 <b>{esc(t.title)}</b> — registration closed • 👥 {n}"


def cancelled_text(t: TSnap) -> str:
    return f"🛑 <b>{esc(t.title)}</b> was cancelled by the owner."


# ------------------------------------------------------------------------- fixtures

def _fixture_line(t: TSnap, f: FSnap) -> str:
    a, b = _short(t.name(f.p1_id)), _short(t.name(f.p2_id))
    if f.status == tr.DONE:
        if f.result == "bye":
            return f"🛌 <b>{a if f.p1_id else b}</b> — bye"
        win, lose = (a, b) if f.winner_id == f.p1_id else (b, a)
        return f"✅ <b>{win}</b> def. <s>{lose}</s>{RESULT_NOTE.get(f.result or '', '')}"
    if f.status == tr.LIVE:
        return f"⚔️ <b>{a}</b> {VS} <b>{b}</b> • LIVE"
    if f.status == tr.SCHEDULED:
        return f"🕒 {a} {VS} {b} • {_t(f.scheduled_at)}"
    return f"▫️ {a} {VS} {b}"


def _pair(t: TSnap, f: FSnap) -> str:
    return f"<b>{esc(t.name(f.p1_id))}</b> {VS} <b>{esc(t.name(f.p2_id))}</b>"


# ---------------------------------------------------------------------- league board

def _current_round(t: TSnap) -> int | None:
    for r in range(1, t.league_rounds + 1):
        if any(f.status != tr.DONE for f in t.round_fixtures(r)):
            return r
    return None


def current_round(t: TSnap) -> int | None:
    """The matchday that still has unfinished fixtures (None once the league stage is over)."""
    return _current_round(t)


def _standings_lines(t: TSnap) -> list[str]:
    table = t.table()
    k = tr.playoff_size(t.bracket_size or len(table))
    league_open = t.status == tr.ACTIVE and _current_round(t) is not None
    lines = []
    for i, r in enumerate(table, start=1):
        p = t.player(r.user_id)
        dq = "❌ " if p is not None and p.status == tr.P_DQ else ""
        lines.append(f"{MEDALS.get(i, f'{i}.')} {dq}<b>{_short(t.name(r.user_id))}</b> • {r.points}p • {r.wins}-{r.losses}")
        if league_open and k and i == k and i < len(table):
            lines.append(f"┈ {F.small_caps('playoff line')} ┈")
    return lines


def _league_state(t: TSnap) -> str:
    if t.status == tr.COMPLETED:
        return f"🏆 Champion: <b>{esc(t.name(t.winner_id))}</b>"
    if t.status == tr.CANCELLED:
        return "🛑 Cancelled"
    if t.paused:
        return "⏸️ Paused by the owner"
    if t.status != tr.ACTIVE:
        return ""
    nxt = min((f.scheduled_at for f in t.fixtures if f.status == tr.SCHEDULED and f.scheduled_at), default=None)
    live = any(f.status == tr.LIVE for f in t.fixtures)
    parts = (["⚔️ Live"] if live else []) + ([f"🕒 Next {tr.fmt_ist(nxt)}"] if nxt else [])
    return " • ".join(parts)


def league_text(t: TSnap) -> str:
    lines = [f"🏆 <b>{esc(t.title)}</b> • {F.small_caps('league')}", _league_state(t), "",
             f"📊 {F.sans_bold('STANDINGS')}", _box(_standings_lines(t), expandable=True)]
    cur = _current_round(t)
    play_rounds = list(range(t.league_rounds + 1, (t.total_rounds or 0) + 1))
    if cur is not None:
        fx = sorted(t.round_fixtures(cur), key=lambda x: x.slot)
        lines += [f"⚔️ {F.sans_bold('MATCHDAY ' + str(cur))} / {t.league_rounds}", _box([_fixture_line(t, f) for f in fx])]
        if play_rounds:
            lines.append("🎯 top 4 → semis → final" if len(play_rounds) == 2 else "🎯 top 2 → final")
    else:
        for r in play_rounds:
            fx = sorted(t.round_fixtures(r), key=lambda x: x.slot)
            lines += [f"🎯 {F.sans_bold(t.round_label(r).upper())}", _box([_fixture_line(t, f) for f in fx])]
    return "\n".join(x for x in lines if x is not None)


def league_done_text(t: TSnap) -> str:
    k = tr.playoff_size(t.bracket_size or 0)
    alive = [r for r in t.table() if getattr(t.player(r.user_id), "status", None) == tr.P_REGISTERED][:k]
    if not k or t.status == tr.COMPLETED:
        return f"🏁 {F.sans_bold('LEAGUE COMPLETE')}"
    box = _box([f"{MEDALS.get(i, f'{i}.')} <b>{_short(t.name(r.user_id))}</b> • {r.points}p"
                for i, r in enumerate(alive, start=1)])
    fx = sorted(t.round_fixtures(t.league_rounds + 1), key=lambda x: x.slot)
    match_line = "  •  ".join(f"{_short(t.name(f.p1_id))} {VS} {_short(t.name(f.p2_id))}" for f in fx if f.p1_id or f.p2_id)
    when = next((f.scheduled_at for f in fx if f.scheduled_at), None)
    return (f"🏁 {F.sans_bold('LEAGUE COMPLETE')}\n{box}\n🎯 {F.sans_bold(t.round_label(t.league_rounds + 1).upper())}\n"
            f"{match_line}" + (f"\n🕒 {tr.fmt_ist(when)}" if when else ""))


# -------------------------------------------------------------------- knockout board

def bracket_text(t: TSnap) -> str:
    if t.format == tr.LEAGUE:
        return league_text(t)
    state = (f"🏆 Champion: <b>{esc(t.name(t.winner_id))}</b>" if t.status == tr.COMPLETED else
             "🛑 Cancelled" if t.status == tr.CANCELLED else "⏸️ Paused by the owner" if t.paused else "")
    lines = [f"🏆 <b>{esc(t.title)}</b> • {F.small_caps('knockout')}", state, ""]
    for r in range(1, (t.total_rounds or 0) + 1):
        fx = sorted(t.round_fixtures(r), key=lambda x: x.slot)
        lines += [f"🎯 {F.sans_bold(t.round_label(r).upper())}", _box([_fixture_line(t, f) for f in fx])]
    return "\n".join(x for x in lines if x is not None)


# ------------------------------------------------------------------------ match cards

def match_start_card(t: TSnap, f: FSnap) -> str:
    n = len(t.round_fixtures(f.round_no))
    return (f"🔔 {F.sans_bold(t.round_label(f.round_no).upper())} • {f.slot + 1}/{n}\n"
            f"🥊 {mention(f.p1_id, t.name(f.p1_id))} {VS} {mention(f.p2_id, t.name(f.p2_id))}\n"
            f"📩 {F.small_caps('check your dm')} • ⏱️ {t.noshow_sec // 60} min")


def reminder_card(t: TSnap, f: FSnap) -> str:
    return (f"⏰ <b>{t.round_label(f.round_no)}</b> in {tr.REMINDER_MINUTES} min\n"
            f"🥊 {mention(f.p1_id, t.name(f.p1_id))} {VS} {mention(f.p2_id, t.name(f.p2_id))} • 🕒 {_t(f.scheduled_at)}")


def reminder_dm(t: TSnap, f: FSnap, user_id: int) -> str:
    opp = f.p2_id if user_id == f.p1_id else f.p1_id
    return (f"⏰ <b>{t.round_label(f.round_no)}</b> in {tr.REMINDER_MINUTES} min\n"
            f"🥊 vs <b>{esc(t.name(opp))}</b> • 🕒 {_t(f.scheduled_at)}\n"
            f"{F.small_caps('the bot starts it automatically')}")


def blocked_note(t: TSnap, f: FSnap, user_id: int | None) -> str:
    who = mention(user_id, t.name(user_id)) if user_id else "A fighter"
    return f"⏳ {who} can't start yet (busy or bot not started). Waiting up to {t.noshow_sec // 60} min."


def walkover_note(t: TSnap, f: FSnap) -> str:
    return (f"🚪 {t.round_label(f.round_no)}: <b>{esc(t.name(f.winner_id))}</b> wins "
            f"({'owner decision' if f.result == 'owner' else 'walkover'})")


def rescheduled_note(t: TSnap, f: FSnap) -> str:
    return (f"📅 <b>Rescheduled</b> • {t.round_label(f.round_no)}\n"
            f"{_pair(t, f)}\n🕒 {tr.fmt_ist(f.scheduled_at)}")


def round_done_note(t: TSnap, round_no: int) -> str:
    nxt = sorted(t.round_fixtures(round_no + 1), key=lambda x: x.slot)
    when = next((f.scheduled_at for f in nxt if f.scheduled_at), None)
    return (f"✅ <b>{t.round_label(round_no)} complete</b>"
            + (f"\n➡️ {t.round_label(round_no + 1)} • 🕒 {tr.fmt_ist(when)}" if when else ""))


def started_text(t: TSnap) -> str:
    if t.format == tr.LEAGUE:
        matches = sum(1 for f in t.fixtures if f.round_no <= t.league_rounds)
        return (f"🏆 <b>{esc(t.title)}</b> {F.sans_bold('BEGINS!')}\n"
                f"👥 {len(t.registered)} players • 🏁 {t.league_rounds} matchdays • {matches} matches\n"
                f"🕒 {tr.fmt_ist(t.start_at)}\n"
                f"🎯 win = 3 pts • top 4 → semis → final\n"
                f"🤖 {F.small_caps('every match starts automatically')}")
    byes = sum(1 for f in t.fixtures if f.result == "bye")
    return (f"🏆 <b>{esc(t.title)}</b> {F.sans_bold('BEGINS!')}\n"
            f"👥 {len(t.registered)} players • {t.round_label(1)}" + (f" • {byes} bye{'s' if byes != 1 else ''}" if byes else "")
            + f"\n🕒 {tr.fmt_ist(t.start_at)}\n🤖 {F.small_caps('every match starts automatically')}")


def champion_text(t: TSnap) -> str:
    final = next((f for f in t.fixtures if f.round_no == t.total_rounds), None)
    runner = (final.p2_id if final and final.winner_id == final.p1_id else final.p1_id) if final else None
    semis = [p for p in t.players if p.out_round == (t.total_rounds or 0) - 1 and p.status == tr.P_OUT]
    champ = t.player(t.winner_id)
    podium = []
    if runner:
        podium.append(f"🥈 {_short(t.name(runner), 18)}")
    if semis:
        podium.append("🥉 " + ", ".join(_short(p.name, 18) for p in semis))
    return (f"🏆 <b>{esc(t.title)}</b>\n👑 {F.sans_bold('CHAMPION')}\n"
            f"<b>{esc(t.name(t.winner_id))}</b>" + (f" • {champ.wins} wins" if champ else "")
            + ("\n" + "  ".join(podium) if podium else ""))


def champion_dm() -> str:
    return "🏆 <b>YOU ARE THE CHAMPION!</b> 👑"


# ---------------------------------------------------------------------- admin panel

def admin_text(t: TSnap) -> str:
    if t.status == tr.REGISTRATION:
        state = f"Registration • 👥 {len(t.registered)}/{t.max_players}" + (f" (+{len(t.waitlist)})" if t.waitlist else "")
    elif t.status == tr.ACTIVE:
        done = sum(1 for f in t.fixtures if f.status == tr.DONE)
        state = f"Live • {done}/{len(t.fixtures)} done" + (" • ⏸️ paused" if t.paused else "")
    else:
        state = t.status.title()
    closes = f"\n⏰ closes {tr.fmt_ist(t.reg_close_at)}" if t.status == tr.REGISTRATION and t.reg_close_at else ""
    return (f"👑 <b>Admin</b> • {esc(t.title)}\n{state}\n🕒 {tr.fmt_ist(t.start_at)}{closes}\n"
            f"⏱️ gap {t.round_gap_min}m / {t.match_gap_min}m • no-show {t.noshow_sec // 60}m")


def gaps_text(t: TSnap) -> str:
    return (f"⏱️ <b>Timing</b> • {esc(t.title)}\n"
            f"🔁 Round gap <b>{t.round_gap_min} min</b>\n⚔️ Match gap <b>{t.match_gap_min} min</b>\n"
            f"⌛ No-show <b>{t.noshow_sec // 60} min</b>\n<i>Hand-edited fixture times stay as set.</i>")


def fixtures_admin_text(t: TSnap) -> str:
    return f"📅 <b>Fixtures</b> • {esc(t.title)}\nTap one to change its time (IST) or set the result."


def fixture_admin_text(t: TSnap, f: FSnap) -> str:
    return (f"📅 <b>{t.round_label(f.round_no)}</b> • #{f.slot + 1}\n{_pair(t, f)}\n"
            f"{f.status} • 🕒 {tr.fmt_ist(f.scheduled_at)}" + (" 🔒" if f.time_locked else ""))


def pick_player_text(action: str) -> str:
    return f"🚫 <b>{action}</b> — pick a player:"


# ----------------------------------------------------------------- history & stats

def history_text(rows: list[dict]) -> str:
    if not rows:
        return "📜 <b>HISTORY</b>\nNo finished tournament in this group yet."
    lines = [f"🏆 <b>{_short(r['title'], 20)}</b> • 👑 {_short(r['winner'], 16)} • 👥 {r['players']} • "
             f"{r['when'].astimezone(tr.IST):%d %b}" for r in rows]
    return "📜 <b>HISTORY</b>\n" + _box(lines)


def stats_text(name: str, s: dict) -> str:
    if not s["played"]:
        return f"📊 <b>{esc(name)}</b>\nNo tournament played yet — join the next one!"
    recent = [f"{_short(title, 20)} • {label}" for title, label in s["recent"]]
    return (f"📊 <b>{esc(name)}</b>\n🏆 {s['titles']} titles • 🥈 {s['finals']} finals • 🎟️ {s['played']} played\n"
            f"✅ {s['wins']} wins • ❌ {s['losses']} losses\n" + _box(recent))


def leaderboard_text(rows: list[tuple[str, int, int, int]]) -> str:
    if not rows:
        return "🏅 <b>LEADERS</b>\nNo tournament results yet."
    lines = [f"{MEDALS.get(i, f'{i}.')} <b>{_short(n, 16)}</b> • 🏆 {titles} • ✅ {wins} • {played} played"
             for i, (n, titles, played, wins) in enumerate(rows, start=1)]
    return "🏅 <b>LEADERS</b>\n" + _box(lines)


# --------------------------------------------------------------------------- my match

def _status_word(f: FSnap) -> str:
    return {tr.LIVE: "⚔️ LIVE now — open the bot chat and play", tr.SCHEDULED: "🕒 Scheduled",
            tr.PENDING: "⏳ Waiting for the earlier matches"}.get(f.status, f.status)


def my_match_text(t: TSnap, user_id: int) -> str:
    """The personal card sent to a fighter's DM: next match, time, opponent, record and any reschedule request."""
    p = t.player(user_id)
    head = f"⚔️ {F.sans_bold('MY MATCH')} • {esc(t.title)}"
    if p is None or p.status not in (tr.P_REGISTERED, tr.P_OUT, tr.P_CHAMPION, tr.P_DQ):
        return f"{head}\nYou are not in this tournament."
    if p.status == tr.P_DQ:
        return f"{head}\n🚫 You were disqualified."
    table = t.table() if t.format == tr.LEAGUE else []
    rank = next((i for i, r in enumerate(table, start=1) if r.user_id == user_id), None)
    row = next((r for r in table if r.user_id == user_id), None)
    record = f"✅ {p.wins} • ❌ {p.losses}" + (f" • 📊 #{rank} • {row.points}p" if rank and row else "")
    mine = t.my_fixtures(user_id)
    if not mine:
        end = "🏆 You are the champion!" if p.status == tr.P_CHAMPION else (
            "You have no match left." if t.status == tr.ACTIVE else "The tournament is over.")
        return f"{head}\n{record}\n{end}"
    f = mine[0]
    opp = f.p2_id if user_id == f.p1_id else f.p1_id
    lines = [head, record, "", f"🎯 <b>{esc(t.round_label(f.round_no))}</b> • {_status_word(f)}",
             f"🥊 vs <b>{esc(t.name(opp))}</b>" if opp else "🥊 vs <i>opponent to be decided</i>"]
    if f.scheduled_at and f.status != tr.LIVE:
        lines.append(f"🕒 {tr.fmt_ist(f.scheduled_at)}")
    rr = t.pending_reschedule(f.id)
    if rr is not None:
        lines.append(f"🔁 Reschedule to <b>{tr.fmt_ist(rr.new_at)}</b> — "
                     + ("waiting for your opponent" if rr.requester_id == user_id else "<b>needs your answer</b>"))
    later = len(mine) - 1
    if later > 0:
        lines.append(f"📋 +{later} more match{'es' if later != 1 else ''} after this")
    return "\n".join(lines)


def my_match_alert(t: TSnap, user_id: int) -> str:
    """The same information squeezed into a callback pop-up (200 characters) for fighters whose DM is closed."""
    import re
    plain = re.sub(r"<[^>]+>", "", my_match_text(t, user_id))
    return plain[:195]


# ------------------------------------------------------------------------- reschedule

def reschedule_prompt(t: TSnap, f: FSnap, user_id: int) -> str:
    opp = f.p2_id if user_id == f.p1_id else f.p1_id
    return (f"🔁 <b>Reschedule</b> • {esc(t.round_label(f.round_no))} vs <b>{esc(t.name(opp))}</b>\n"
            f"Now: {tr.fmt_ist(f.scheduled_at)}\n"
            f"Reply with the <b>new time (IST)</b>: <code>9:30pm</code> • <code>tomorrow 8pm</code> • <code>+45m</code>\n"
            f"<i>Your opponent must accept. Max {tr.RESCHEDULE_MAX_SHIFT_H}h later, "
            f"{tr.RESCHEDULE_MAX_ACCEPTED} moves per match.</i>")


def reschedule_sent_text(t: TSnap, f: FSnap, rr) -> str:
    opp = rr.opponent_id
    return (f"📨 Request sent to <b>{esc(t.name(opp))}</b>\n"
            f"🕒 {tr.fmt_ist(rr.old_at)} ➜ <b>{tr.fmt_ist(rr.new_at)}</b>\n"
            f"⏳ They have {tr.RESCHEDULE_TTL_MIN} min to answer, otherwise the match stays as scheduled.")


def reschedule_request_dm(t: TSnap, f: FSnap, rr, owner_copy: bool = False) -> str:
    who = esc(t.name(rr.requester_id))
    head = "👑 <b>Owner copy</b> • " if owner_copy else ""
    return (f"{head}🔁 <b>{who}</b> asks to move your match\n"
            f"🎯 {esc(t.round_label(f.round_no))} • {_pair(t, f)}\n"
            f"🕒 {tr.fmt_ist(rr.old_at)} ➜ <b>{tr.fmt_ist(rr.new_at)}</b>\n"
            f"⏳ Answer within {tr.RESCHEDULE_TTL_MIN} min" + (" (either fighter or you can decide)" if owner_copy else ""))


def reschedule_answer_dm(t: TSnap, f: FSnap | None, rr, note: str) -> str:
    when = tr.fmt_ist(rr.new_at)
    opp = esc(t.name(rr.opponent_id))
    return {tr.RQ_ACCEPTED: f"✅ Reschedule <b>accepted</b>. New time: <b>{when}</b>",
            tr.RQ_DECLINED: f"❌ <b>{opp}</b> declined. The match stays at {tr.fmt_ist(rr.old_at)}.",
            tr.RQ_EXPIRED: f"⌛ No answer in time — the match stays at {tr.fmt_ist(rr.old_at)}.",
            tr.RQ_WITHDRAWN: f"↩️ <b>{esc(t.name(rr.requester_id))}</b> withdrew the reschedule request."}.get(note, "")


def reschedule_closed_text(note: str, by: str = "") -> str:
    return {tr.RQ_ACCEPTED: "✅ You accepted — match moved.", tr.RQ_DECLINED: "❌ You declined — the match stays.",
            tr.RQ_WITHDRAWN: "↩️ Request withdrawn."}.get(note, "Closed.") + (f" {by}" if by else "")


# ------------------------------------------------------------------------ result posts

def _method_word(f: FSnap) -> str:
    return {"knockout": "by knockout", "timeout": "by timeout", "walkover": "by walkover",
            "owner": "by owner decision"}.get(f.result or "", "")


def match_result_caption(t: TSnap, f: FSnap) -> str:
    win, lose = t.name(f.winner_id), t.name(f.p2_id if f.winner_id == f.p1_id else f.p1_id)
    pts = f" • +{tr.WIN_POINTS} pts" if t.format == tr.LEAGUE and f.round_no <= t.league_rounds else ""
    return (f"🏆 <b>{esc(t.title)}</b> • {esc(t.round_label(f.round_no))}\n"
            f"🥇 <b>{esc(win)}</b> def. <s>{esc(lose)}</s> {_method_word(f)}{pts}")


def standings_caption(t: TSnap, subtitle: str) -> str:
    table = t.table()[:3]
    podium = "\n".join(f"{MEDALS[i]} <b>{_short(t.name(r.user_id), 18)}</b> • {r.points}p" for i, r in enumerate(table, start=1))
    return f"📊 <b>{esc(t.title)}</b> • {esc(subtitle)}" + (f"\n{podium}" if podium else "")


def champion_caption(t: TSnap) -> str:
    return champion_text(t)
