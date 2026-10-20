"""Publishes Money in the Bank events to Telegram and runs the MITB deadline loop.

Qualifier duels are "quiet": move-by-move play happens in the players' DMs and the group only sees the live
bracket board, round announcements and results. The ladder match and cash-ins are fully public.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from database import transaction
from keyboards import mitb as mk
from keyboards.main import open_bot_kb
from services import media_manager as media
from services import mitb_engine as eng
from services import mitb_text as mt
from services import turn_ping
from services.mitb_engine import DuelSnap, EventSnap, MEvent, MResult
from services.tg import safe_clear_markup, safe_edit, safe_send

log = logging.getLogger(__name__)


async def bot_link(bot: Bot) -> str:
    return f"https://t.me/{(await bot.me()).username}"


# ----------------------------------------------------------------------- lobby

async def publish_lobby(bot: Bot, snap: EventSnap) -> None:
    sent = await safe_send(bot, snap.group_chat_id, mt.lobby_text(snap),
                           reply_markup=mk.lobby_kb(snap.id, await bot_link(bot)))
    if sent.message_id:
        async with transaction() as s:
            await eng.set_message_id(s, snap.id, "lobby_message_id", sent.message_id)


async def refresh_lobby(bot: Bot, snap: EventSnap) -> None:
    if snap.lobby_message_id:
        await safe_edit(bot, snap.group_chat_id, snap.lobby_message_id, mt.lobby_text(snap),
                        reply_markup=mk.lobby_kb(snap.id, await bot_link(bot)))


async def close_lobby(bot: Bot, snap: EventSnap, why: str) -> None:
    if snap.lobby_message_id:
        await safe_edit(bot, snap.group_chat_id, snap.lobby_message_id, mt.lobby_closed_text(why))


async def refresh_board(bot: Bot, snap: EventSnap) -> None:
    markup = mk.board_kb(snap.id) if snap.status in ("qualifier", "ladder") else None
    if snap.board_message_id:
        await safe_edit(bot, snap.group_chat_id, snap.board_message_id, mt.board_text(snap), reply_markup=markup)
        return
    sent = await safe_send(bot, snap.group_chat_id, mt.board_text(snap), reply_markup=markup)
    if sent.message_id:
        async with transaction() as s:
            await eng.set_message_id(s, snap.id, "board_message_id", sent.message_id)


# ------------------------------------------------------------------- DM helpers

async def _dm(bot: Bot, user_id: int, text: str, markup=None, *, duel_id: int | None = None,
              event_id: int | None = None, group_chat_id: int | None = None, name: str = "?") -> None:
    sent = await safe_send(bot, user_id, text, reply_markup=markup)
    if sent.forbidden or (sent.message_id and (duel_id or event_id)):
        async with transaction() as s:
            if sent.forbidden:
                await eng.mark_dm_unreachable(s, user_id)
            elif duel_id:
                await eng.set_duel_prompt(s, duel_id, user_id, sent.message_id)
            else:
                await eng.set_ladder_prompt(s, event_id, user_id, sent.message_id)
    if sent.forbidden and group_chat_id:
        await safe_send(bot, group_chat_id,
                        f"⚠️ I can't message <b>{mt.esc(name)}</b>. Open the bot privately and press Start.",
                        reply_markup=open_bot_kb(await bot_link(bot)))


async def _prompt_duel_actor(bot: Bot, duel: DuelSnap, label: str) -> None:
    uid = duel.active_user_id
    me, foe = duel.player(uid), duel.other(uid)
    await _dm(bot, uid, mt.duel_move_prompt(duel, uid, label), mk.duel_move_kb(duel, uid), duel_id=duel.id,
              group_chat_id=duel.group_chat_id, name=me.name)
    if duel.kind == "bracket":
        await _dm(bot, foe.user_id, mt.duel_waiting_text(duel, foe.user_id))
    else:                                                    # cash-in is public: ping the player in the group
        await turn_ping.ping_move(bot, duel.group_chat_id, f"c{duel.id}", uid, me.name, duel.deadline_at,
                                  await bot_link(bot))


async def prompt_duel_defender(bot: Bot, duel: DuelSnap) -> None:
    uid = duel.other(duel.active_user_id).user_id
    await _dm(bot, uid, mt.duel_incoming(duel, uid), mk.duel_incoming_kb(duel, uid), duel_id=duel.id,
              group_chat_id=duel.group_chat_id, name=duel.player(uid).name)
    if duel.kind == "cashin":
        await turn_ping.ping_defend(bot, duel.group_chat_id, f"c{duel.id}", uid, duel.player(uid).name,
                                    duel.deadline_at, await bot_link(bot))


async def _prompt_ladder_actor(bot: Bot, snap: EventSnap) -> None:
    uid = snap.active_player_id
    await _dm(bot, uid, mt.ladder_move_prompt(snap, uid), mk.ladder_move_kb(snap, uid), event_id=snap.id,
              group_chat_id=snap.group_chat_id, name=snap.player(uid).name)
    await turn_ping.ping_move(bot, snap.group_chat_id, f"b{snap.id}", uid, snap.player(uid).name,
                              snap.deadline_at, await bot_link(bot))


async def prompt_ladder_defender(bot: Bot, snap: EventSnap) -> None:
    uid = snap.pending_target_id
    await _dm(bot, uid, mt.ladder_incoming(snap, uid), mk.ladder_incoming_kb(snap, uid), event_id=snap.id,
              group_chat_id=snap.group_chat_id, name=snap.player(uid).name)
    await turn_ping.ping_defend(bot, snap.group_chat_id, f"b{snap.id}", uid, snap.player(uid).name,
                                snap.deadline_at, await bot_link(bot))


# --------------------------------------------------------------------- dispatch

def _find_duel(duel: DuelSnap | None, ev_snap: EventSnap | None, duel_id: int | None) -> DuelSnap | None:
    if duel is not None and duel.id == duel_id:
        return duel
    return next((d for d in (ev_snap.duels if ev_snap else []) if d.id == duel_id), None)


async def _handle(bot: Bot, ev_snap: EventSnap | None, duel: DuelSnap | None, ev: MEvent) -> None:
    gid = (ev_snap.group_chat_id if ev_snap else duel.group_chat_id)
    k = ev.kind
    # ---- qualifier / cash-in duels
    if k == "round_start":
        await media.send_event(bot, gid, "mitb_round", mt.round_caption(ev_snap, ev))
        if ev.bye:
            await _dm(bot, ev.bye, "🛌 <b>You got a bye</b> — you advance to the next round without playing.")
    elif k == "duel_turn_start":
        d = _find_duel(duel, ev_snap, ev.duel_id)
        if d is not None:
            label = (f"Round {d.round_no}/{ev_snap.total_rounds}" if ev_snap and d.kind == "bracket"
                     else ("💼 Cash-in" if d.kind == "cashin" else f"Round {d.round_no}"))
            await _prompt_duel_actor(bot, d, label)
    elif k == "duel_turn" and duel is not None:
        for p in duel.players:
            await safe_clear_markup(bot, p.user_id, p.prompt_message_id)
        if duel.kind == "bracket":
            for p in duel.players:
                await _dm(bot, p.user_id, mt.duel_result_dm(duel, ev, p.user_id))
        else:
            await turn_ping.clear(bot, gid, f"c{duel.id}")
            await media.send_event(bot, gid, "reversal" if ev.reversed else ev.move, mt.cashin_turn_caption(duel, ev))
    elif k == "duel_skip" and duel is not None:
        await safe_clear_markup(bot, ev.user_id, getattr(duel.player(ev.user_id), "prompt_message_id", None))
        await _dm(bot, ev.user_id, "💤 You took too long — your turn was skipped. Miss another and you forfeit!")
        if duel.kind == "cashin":
            await turn_ping.clear(bot, gid, f"c{duel.id}")
    elif k == "duel_end" and duel is not None:
        for p in duel.players:
            await safe_clear_markup(bot, p.user_id, p.prompt_message_id)
        if duel.kind == "bracket" and ev_snap is not None:
            await media.send_event(bot, gid, "mitb_duel_end", mt.duel_end_caption(ev_snap, ev))
            await _dm(bot, ev.user_id, mt.advance_dm(ev, "The next round starts when every match has finished."))
            await _dm(bot, ev.other_id, mt.knocked_out_dm(ev))
    elif k == "cashin_start" and duel is not None:
        await media.send_event(bot, gid, "mitb_cashin", mt.cashin_caption(duel, ev))
    elif k == "cashin_end" and duel is not None:
        await turn_ping.clear(bot, gid, f"c{duel.id}")
        await media.send_event(bot, gid, "mitb_champion" if ev.flag else "mitb_duel_end", mt.cashin_end_caption(duel, ev))
    # ---- ladder
    elif ev_snap is not None and k == "ladder_start":
        await media.send_event(bot, gid, "mitb_ladder", mt.ladder_start_caption(ev_snap))
    elif ev_snap is not None and k == "ladder_turn_start":
        await _prompt_ladder_actor(bot, ev_snap)
    elif ev_snap is not None and k in ("climb", "attack", "skip", "dq", "winner"):
        await turn_ping.clear(bot, gid, f"b{ev_snap.id}")
        for uid in (ev.user_id, ev.other_id):
            p = ev_snap.player(uid)
            if p:
                await safe_clear_markup(bot, uid, p.prompt_message_id)
        if k == "climb":
            await media.send_event(bot, gid, "mitb_climb", mt.climb_caption(ev_snap, ev))
        elif k == "attack":
            await media.send_event(bot, gid, "reversal" if ev.reversed else ev.move, mt.attack_caption(ev_snap, ev))
        elif k == "skip":
            await safe_send(bot, gid, mt.skip_caption(ev_snap, ev))
        elif k == "dq":
            await safe_send(bot, gid, mt.dq_caption(ev_snap, ev))
        else:
            await media.send_event(bot, gid, "mitb_winner", mt.winner_caption(ev_snap, ev))
            await _dm(bot, ev.user_id, f"💼 <b>YOU ARE MR. MONEY IN THE BANK!</b>\n\n"
                                       f"Cash in after any 1v1 match in the group. Use /briefcase to check it.")
    elif ev_snap is not None and k == "shaken":
        await safe_send(bot, gid, mt.shaken_caption(ev_snap, ev))
    elif ev_snap is not None and k == "knockdown":
        await media.send_event(bot, gid, "mitb_fall", mt.knockdown_caption(ev_snap, ev))
    elif ev_snap is not None and k == "stunned":
        await safe_send(bot, gid, mt.stunned_caption(ev_snap, ev))


async def dispatch(bot: Bot, ev_snap: EventSnap | None, duel: DuelSnap | None, events: list[MEvent]) -> None:
    for ev in events:
        if ev.kind == "event_start":
            continue
        await _handle(bot, ev_snap, duel, ev)
    if ev_snap is not None and ev_snap.status in ("qualifier", "ladder", "completed"):
        await refresh_board(bot, ev_snap)


async def publish_started(bot: Bot, snap: EventSnap, events: list[MEvent]) -> None:
    await close_lobby(bot, snap, "✅ The tournament has started — see below.")
    await media.send_event(bot, snap.group_chat_id, "mitb_start", mt.started_caption(snap))
    await dispatch(bot, snap, None, events)


async def publish_event_result(bot: Bot, result: MResult) -> None:
    snap = result.event
    if result.kind == "started":
        await publish_started(bot, snap, result.events)
    elif result.kind == "lobby_expired":
        await close_lobby(bot, snap, f"⏳ Not enough wrestlers (need {eng.mr.MIN_PLAYERS}). The tournament was cancelled.")
    elif result.kind in ("turn_resolved", "turn_skipped"):
        await dispatch(bot, snap, None, result.events)


async def publish_duel_result(bot: Bot, result: MResult) -> None:
    await dispatch(bot, result.event, result.duel, result.events)


async def publish_cancelled(bot: Bot, snap: EventSnap) -> None:
    await turn_ping.clear(bot, snap.group_chat_id, f"b{snap.id}")
    await close_lobby(bot, snap, "🛑 The tournament was cancelled.")
    if snap.board_message_id:
        await safe_edit(bot, snap.group_chat_id, snap.board_message_id, "🛑 <b>MONEY IN THE BANK — CANCELLED</b>")
    for p in snap.players:
        await safe_clear_markup(bot, p.user_id, p.prompt_message_id)
    for d in snap.duels:
        for p in d.players:
            await safe_clear_markup(bot, p.user_id, p.prompt_message_id)


# ------------------------------------------------------------------------ cash-in

async def publish_offer(bot: Bot, offer: eng.OfferSnap) -> None:
    sent = await safe_send(bot, offer.group_chat_id, mt.offer_text(offer), reply_markup=mk.cashin_kb(offer.id))
    if sent.message_id:
        async with transaction() as s:
            await eng.set_offer_message(s, offer.id, sent.message_id)


async def offer_after_match(bot: Bot, match_snap) -> None:
    """Hook called when a 1v1 match in a group ends: maybe open a cash-in window for a briefcase holder."""
    try:
        if not match_snap.winner_id:
            return
        winner = match_snap.player(match_snap.winner_id)
        ids = {p.user_id for p in match_snap.players}
        async with transaction() as s:
            res = await eng.offer_cashin(s, match_snap.group_chat_id, ids, winner.user_id, winner.hp)
        if res.ok:
            await publish_offer(bot, res.offer)
    except Exception:
        log.exception("Cash-in offer failed (the 1v1 match is unaffected)")


# ------------------------------------------------------------------ deadline loop

async def process_due(bot: Bot) -> int:
    async with transaction() as s:
        event_ids, duel_ids = await eng.due_ids(s)
    handled = 0
    for event_id in event_ids:
        try:
            async with transaction() as s:
                result = await eng.handle_due_event(s, event_id)
            if result is not None:
                handled += 1
                await publish_event_result(bot, result)
        except Exception:
            log.exception("Failed processing MITB event deadline %s", event_id)
    for duel_id in duel_ids:
        try:
            async with transaction() as s:
                result = await eng.handle_due_duel(s, duel_id)
            if result is not None:
                handled += 1
                await publish_duel_result(bot, result)
        except Exception:
            log.exception("Failed processing MITB duel deadline %s", duel_id)
    try:
        async with transaction() as s:
            expired = await eng.expire_stale(s)
        for o in expired:
            if o.message_id:
                await safe_edit(bot, o.group_chat_id, o.message_id, mt.offer_expired_text(o))
    except Exception:
        log.exception("Failed expiring cash-in offers")
    return handled


async def deadline_loop(bot: Bot, interval: float = 1.5) -> None:
    log.info("Money in the Bank deadline loop started")
    while True:
        try:
            await process_due(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("MITB deadline loop iteration failed")
        await asyncio.sleep(interval)
