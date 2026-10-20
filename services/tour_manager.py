"""Publishes tournament events to Telegram and runs the persistent tournament clock.

State lives in the database; this module only reacts to committed results (like the other managers),
so a crash between commit and publish never corrupts a tournament, and a restart simply continues.
"""
from __future__ import annotations

import asyncio
import json
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile

from database import transaction
from keyboards import tour as tk
from models import utcnow
from services import avatars, cards, publisher
from services import media_manager as media
from services import tour_engine as eng
from services import tour_rules as tr
from services import tour_text as tt
from services import turn_manager as tm
from services.tg import safe_clear_markup, safe_edit, safe_send
from services.tour_engine import Event, FSnap, TSnap
from services.turn_ping import mention

log = logging.getLogger(__name__)


# -------------------------------------------------------------------- cards in the group

async def publish_registration(bot: Bot, t: TSnap) -> None:
    sent = await safe_send(bot, t.group_chat_id, tt.registration_text(t), reply_markup=tk.registration_kb(t))
    if sent.message_id:
        async with transaction() as s:
            await eng.set_message_id(s, t.id, "registration_message_id", sent.message_id)


async def refresh_registration(bot: Bot, t: TSnap) -> None:
    if t.registration_message_id:
        await safe_edit(bot, t.group_chat_id, t.registration_message_id, tt.registration_text(t),
                        reply_markup=tk.registration_kb(t))


async def refresh_bracket(bot: Bot, t: TSnap) -> None:
    """Edit the live bracket message (or post it the first time and try to pin it)."""
    if not t.total_rounds:
        return
    markup = tk.bracket_kb(t) if t.status == tr.ACTIVE else None
    if t.bracket_message_id:
        if await safe_edit(bot, t.group_chat_id, t.bracket_message_id, tt.bracket_text(t), reply_markup=markup):
            return
    sent = await safe_send(bot, t.group_chat_id, tt.bracket_text(t), reply_markup=markup)
    if sent.message_id:
        async with transaction() as s:
            await eng.set_message_id(s, t.id, "bracket_message_id", sent.message_id)
        try:
            await bot.pin_chat_message(t.group_chat_id, sent.message_id, disable_notification=True)
        except TelegramAPIError:
            pass                                                  # no pin right: the bracket is still posted


async def send_admin_panel(bot: Bot, t: TSnap, chat_id: int) -> bool:
    sent = await safe_send(bot, chat_id, tt.admin_text(t), reply_markup=tk.admin_kb(t))
    return sent.ok


async def send_backup(bot: Bot, t: TSnap, note: str = "") -> None:
    """Send a JSON backup of the tournament to the owner's DM."""
    try:
        data = json.dumps(eng.export_dict(t), indent=2, ensure_ascii=False, default=str).encode("utf-8")
        await bot.send_document(t.owner_id, BufferedInputFile(data, filename=f"tournament_{t.id}_{t.status}.json"),
                                caption=f"💾 Backup — {t.title}{(' • ' + note) if note else ''}")
    except TelegramAPIError as exc:
        log.info("Backup to owner failed: %s", exc)


# -------------------------------------------------------------------- result pictures

async def _render(fn, data) -> bytes | None:
    """Draw a card in a worker thread; a drawing error must never stop the text results."""
    try:
        return await asyncio.to_thread(fn, data)
    except Exception:
        log.exception("Rendering %s failed", getattr(fn, "__name__", "card"))
        return None


def standings_subtitle(t: TSnap, after_matchday: int | None = None) -> str:
    if t.status == tr.COMPLETED:
        return "Final league table"
    cur = tt.current_round(t)
    if cur is None:
        return "League stage complete"
    if after_matchday is not None:
        return f"After Matchday {after_matchday} / {t.league_rounds}"
    return f"Live table • Matchday {cur} / {t.league_rounds}"


async def publish_match_card(bot: Bot, t: TSnap, f: FSnap) -> bool:
    """Winner card (both fighters' profile pictures) to the group and the results channel."""
    winner_id = f.winner_id
    loser_id = f.p2_id if winner_id == f.p1_id else f.p1_id
    if not winner_id or not loser_id:
        return False
    async with transaction() as s:
        stats = await eng.fixture_card_stats(s, f.match_id, winner_id)
    winner, loser = await avatars.people(bot, [(winner_id, t.name(winner_id)), (loser_id, t.name(loser_id))])
    league_match = t.format == tr.LEAGUE and f.round_no <= t.league_rounds
    data = cards.MatchCard(
        title=t.title, round_label=t.round_label(f.round_no), winner=winner, loser=loser, method=f.result or "knockout",
        winner_hp=stats.get("winner_hp"), loser_hp=stats.get("loser_hp"), turns=stats.get("turns"),
        finishers=stats.get("finishers"), damage=stats.get("damage"), points=tr.WIN_POINTS if league_match else None,
        note=f"Match {f.slot + 1} of {len(t.round_fixtures(f.round_no))}", date_text=tr.fmt_ist(utcnow()))
    photo = await _render(cards.render_match_card, data)
    return await publisher.post_everywhere(bot, t.group_chat_id, photo, tt.match_result_caption(t, f))


async def publish_champion_card(bot: Bot, t: TSnap) -> bool:
    champ_id = t.winner_id
    if not champ_id:
        return False
    table = t.table() if t.format == tr.LEAGUE else []
    final = next((f for f in t.fixtures if f.round_no == t.total_rounds), None)
    runner_id = None
    if final is not None and final.winner_id:
        runner_id = final.p2_id if final.winner_id == final.p1_id else final.p1_id
    if runner_id is None:                                       # no playoffs: the runner-up is second in the table
        runner_id = next((r.user_id for r in table if r.user_id != champ_id), None)
    third = [p.user_id for p in t.players if p.out_round == (t.total_rounds or 0) - 1 and p.status == tr.P_OUT]
    wanted = [champ_id] + ([runner_id] if runner_id else []) + third[:2]
    people = {p.user_id: p for p in await avatars.people(bot, [(uid, t.name(uid)) for uid in wanted])}
    champ = t.player(champ_id)
    row = next((r for r in table if r.user_id == champ_id), None)
    data = cards.ChampionCard(
        title=t.title, champion=people[champ_id], wins=champ.wins if champ else None, points=row.points if row else None,
        runner_up=people.get(runner_id) if runner_id else None, third=[people[u] for u in third[:2] if u in people],
        date_text=tr.fmt_ist(utcnow()))
    photo = await _render(cards.render_champion_card, data)
    return await publisher.post_everywhere(bot, t.group_chat_id, photo, tt.champion_caption(t))


async def publish_standings(bot: Bot, t: TSnap, subtitle: str, *, chat_id: int | None = None,
                            to_channel: bool = True) -> bool:
    """League table picture. Automatic posts go to the group + channel; ``chat_id`` sends it to one chat only."""
    table = t.table()
    if t.format != tr.LEAGUE or not table:
        return False
    people = await avatars.people(bot, [(r.user_id, t.name(r.user_id)) for r in table])
    rows = [cards.StandRow(person, r.played, r.wins, r.losses, r.margin, r.points,
                           dq=getattr(t.player(r.user_id), "status", None) == tr.P_DQ)
            for r, person in zip(table, people)]
    spots = tr.playoff_size(t.bracket_size or len(table))
    data = cards.StandingsCard(title=t.title, subtitle=subtitle, rows=rows, playoff_spots=spots,
                               show_cut=t.status == tr.ACTIVE and tt.current_round(t) is not None)
    photo = await _render(cards.render_standings_card, data)
    caption = tt.standings_caption(t, subtitle)
    if chat_id is not None:
        return await publisher.post_everywhere(bot, chat_id, photo, caption, to_channel=False)
    return await publisher.post_everywhere(bot, t.group_chat_id, photo, caption, to_channel=to_channel)


# ------------------------------------------------------------------------ event rendering

async def handle_events(bot: Bot, t: TSnap, events: list[Event]) -> None:
    """Turn engine events into Telegram messages, then refresh the live bracket once."""
    chat = t.group_chat_id
    touch_bracket = False
    walkovers: list = []
    decided = [e for e in events if e.kind == "fixture_done" and e.note in ("walkover", "owner")]
    bulk = len(decided) > 2                                       # e.g. a disqualified player's remaining fixtures
    last_matchday_done = max((int(e.note) for e in events if e.kind == "round_done"), default=0)
    for ev in events:
        try:
            f = t.fixture(ev.fixture_id) if ev.fixture_id else None
            if ev.kind == "started":
                await media.send_event(bot, chat, "tour_start", tt.started_text(t))
                if t.registration_message_id:
                    await safe_edit(bot, chat, t.registration_message_id, tt.registration_closed_text(t))
                touch_bracket = True
            elif ev.kind == "redrawn":
                await safe_send(bot, chat, "🎲 <b>The owner redrew the bracket.</b>")
                touch_bracket = True
            elif ev.kind == "reminder" and f:
                await safe_send(bot, chat, tt.reminder_card(t, f))
                for uid in (f.p1_id, f.p2_id):
                    await safe_send(bot, uid, tt.reminder_dm(t, f, uid))
            elif ev.kind == "match_start" and f and ev.match:
                await media.send_event(bot, chat, "tour_match", tt.match_start_card(t, f))
                await tm.publish_live(bot, ev.match)
                touch_bracket = True
            elif ev.kind == "blocked" and f:
                await safe_send(bot, chat, tt.blocked_note(t, f, ev.user_id))
                if ev.user_id:
                    await safe_send(bot, ev.user_id, "🏆 Your tournament match is ready! Open this chat and press "
                                    "<b>Start</b>, and finish any other match, or you lose by no-show.")
            elif ev.kind == "fixture_done" and f:
                posted = False if bulk else await publish_match_card(bot, t, f)
                if ev.note in ("walkover", "owner") and not posted:
                    walkovers.append(f)
                touch_bracket = True
            elif ev.kind == "league_done":
                await safe_send(bot, chat, tt.league_done_text(t))
                await publish_standings(bot, t, "Final league table")
                await send_backup(bot, t, note="league stage complete")
                touch_bracket = True
            elif ev.kind == "round_done" and t.status == tr.ACTIVE:
                if not (t.format == tr.LEAGUE and int(ev.note) == t.league_rounds and t.total_rounds != t.league_rounds):
                    await safe_send(bot, chat, tt.round_done_note(t, int(ev.note)))   # league_done covers the last matchday
                if t.format == tr.LEAGUE and int(ev.note) < t.league_rounds and int(ev.note) == last_matchday_done:
                    await publish_standings(bot, t, standings_subtitle(t, int(ev.note)))
                await send_backup(bot, t, note=f"{t.round_label(int(ev.note))} complete")
            elif ev.kind == "champion":
                await media.send_event(bot, chat, "tour_champion", tt.champion_text(t))
                if ev.user_id:
                    await safe_send(bot, ev.user_id, tt.champion_dm())
                await publish_champion_card(bot, t)
                await send_backup(bot, t, note="final")
                touch_bracket = True
            elif ev.kind == "rescheduled" and f:
                await safe_send(bot, chat, tt.rescheduled_note(t, f))
                touch_bracket = True
            elif ev.kind == "reschedule_request" and f:
                rr = t.reschedule(int(ev.note))
                if rr is not None:
                    await safe_send(bot, rr.opponent_id, tt.reschedule_request_dm(t, f, rr),
                                    reply_markup=tk.reschedule_answer_kb(rr.id))
                    if t.owner_id not in (rr.requester_id, rr.opponent_id):
                        await safe_send(bot, t.owner_id, tt.reschedule_request_dm(t, f, rr, owner_copy=True),
                                        reply_markup=tk.reschedule_answer_kb(rr.id))
            elif ev.kind == "reschedule_answer" and ev.user_id:
                rr = next((r for r in reversed(t.reschedules) if r.fixture_id == ev.fixture_id and r.status == ev.note), None)
                if rr is not None:
                    text = tt.reschedule_answer_dm(t, f, rr, ev.note)
                    if text:
                        await safe_send(bot, ev.user_id, text)
            elif ev.kind == "rescheduled_all":
                await safe_send(bot, chat, f"📅 <b>Timetable updated by the owner.</b> First match: {tr.fmt_ist(t.start_at)}")
                touch_bracket = True
            elif ev.kind == "reg_extended":
                await safe_send(bot, chat, f"⏰ Not enough players yet — registration extended by {eng.EXTEND_MINUTES} min.")
                await refresh_registration(bot, t)
            elif ev.kind == "match_cancelled" and ev.match:
                for p in ev.match.players:
                    await safe_clear_markup(bot, p.user_id, p.prompt_message_id)
                    await safe_send(bot, p.user_id, "🛑 The owner ended your tournament match.")
            elif ev.kind == "match_reset" and f:
                await safe_send(bot, chat, "🔁 A tournament match was reset and will start again shortly.")
            elif ev.kind in ("kicked", "disqualified"):
                await safe_send(bot, chat, f"🚫 <b>{tt.esc(t.name(ev.user_id))}</b> was "
                                f"{'removed' if ev.kind == 'kicked' else 'disqualified'} by the owner.")
                touch_bracket = True
                await refresh_registration(bot, t)
            elif ev.kind == "paused":
                await safe_send(bot, chat, "⏸️ <b>Tournament paused</b> — no new matches will start.")
                touch_bracket = True
            elif ev.kind == "resumed":
                await safe_send(bot, chat, "▶️ <b>Tournament resumed.</b>")
                touch_bracket = True
            elif ev.kind == "cancelled":
                await safe_send(bot, chat, tt.cancelled_text(t))
                for mid in (t.registration_message_id,):
                    if mid:
                        await safe_edit(bot, chat, mid, tt.cancelled_text(t))
                touch_bracket = bool(t.total_rounds)
        except Exception:                                           # one bad message must not block the rest
            log.exception("Publishing tournament event %s failed", ev.kind)
    if len(walkovers) > 2:                                          # e.g. a disqualified player's remaining fixtures
        await safe_send(bot, chat, f"🚪 {len(walkovers)} fixtures were awarded by walkover.")
    else:
        for f in walkovers:
            await safe_send(bot, chat, tt.walkover_note(t, f))
    if touch_bracket:
        await refresh_bracket(bot, t)


async def announce(bot: Bot, t: TSnap, text: str) -> None:
    await safe_send(bot, t.group_chat_id, f"📣 <b>{tt.esc(t.title)}</b>\n\n{tt.esc(text)}")


# ----------------------------------------------------------------------------- the clock

async def process_open(bot: Bot) -> int:
    async with transaction() as s:
        ids = await eng.open_ids(s)
    handled = 0
    for tid in ids:
        try:
            async with transaction() as s:
                res = await eng.tick(s, tid)
            if res is not None and res.events:
                handled += 1
                await handle_events(bot, res.t, res.events)
        except Exception:
            log.exception("Tournament %s tick failed", tid)
    return handled


async def deadline_loop(bot: Bot, interval: float = 3.0) -> None:
    log.info("Tournament loop started")
    while True:
        try:
            await process_open(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Tournament loop iteration failed")
        await asyncio.sleep(interval)
