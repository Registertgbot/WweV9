"""Publishes Royal Rumble events to Telegram and runs the Rumble deadline loop.

State lives in the database; this module only reacts to committed results (same idea as turn_manager).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from database import transaction
from keyboards import rumble as rk
from keyboards.main import open_bot_kb
from services import media_manager as media
from services import rumble_engine as eng
from services import rumble_text as rt
from services import runtime_settings, turn_ping
from services.rumble_engine import REvent, RResult, RumbleSnap
from services.tg import safe_clear_markup, safe_edit, safe_send

log = logging.getLogger(__name__)


async def bot_link(bot: Bot) -> str:
    return f"https://t.me/{(await bot.me()).username}"


# ----------------------------------------------------------------------- lobby

async def publish_lobby(bot: Bot, snap: RumbleSnap) -> None:
    sent = await safe_send(bot, snap.group_chat_id, rt.lobby_text(snap),
                           reply_markup=rk.lobby_kb(snap.id, await bot_link(bot)))
    if sent.message_id:
        async with transaction() as s:
            await eng.set_message_id(s, snap.id, "lobby_message_id", sent.message_id)


async def refresh_lobby(bot: Bot, snap: RumbleSnap) -> None:
    if snap.lobby_message_id:
        await safe_edit(bot, snap.group_chat_id, snap.lobby_message_id, rt.lobby_text(snap),
                        reply_markup=rk.lobby_kb(snap.id, await bot_link(bot)))


async def close_lobby(bot: Bot, snap: RumbleSnap, why: str) -> None:
    if snap.lobby_message_id:
        await safe_edit(bot, snap.group_chat_id, snap.lobby_message_id, rt.lobby_closed_text(snap, why))


# ----------------------------------------------------------------------- board

async def refresh_board(bot: Bot, snap: RumbleSnap) -> None:
    markup = rk.board_kb(snap.id) if snap.status == "active" else None
    if snap.board_message_id:
        await safe_edit(bot, snap.group_chat_id, snap.board_message_id, rt.board_text(snap), reply_markup=markup)
        return
    sent = await safe_send(bot, snap.group_chat_id, rt.board_text(snap), reply_markup=markup)
    if sent.message_id:
        async with transaction() as s:
            await eng.set_message_id(s, snap.id, "board_message_id", sent.message_id)


# ------------------------------------------------------------------- DM helpers

async def _dm(bot: Bot, snap: RumbleSnap, user_id: int, text: str, markup=None, keep_prompt: bool = False) -> None:
    sent = await safe_send(bot, user_id, text, reply_markup=markup)
    async with transaction() as s:
        if sent.forbidden:
            await eng.mark_dm_unreachable(s, user_id)
        elif keep_prompt and sent.message_id:
            await eng.set_prompt_message(s, snap.id, user_id, sent.message_id)
    if sent.forbidden:
        p = snap.player(user_id)
        await safe_send(bot, snap.group_chat_id,
                        f"⚠️ I can't message <b>{rt.esc(p.name if p else '?')}</b>. Open the bot privately and press Start.",
                        reply_markup=open_bot_kb(await bot_link(bot)))


async def prompt_actor(bot: Bot, snap: RumbleSnap) -> None:
    uid = snap.active_player_id
    await _dm(bot, snap, uid, rt.move_prompt(snap, uid), rk.move_kb(snap, uid), keep_prompt=True)
    await turn_ping.ping_move(bot, snap.group_chat_id, f"r{snap.id}", uid, snap.player(uid).name,
                              snap.deadline_at, await bot_link(bot))


async def publish_attack_declared(bot: Bot, snap: RumbleSnap) -> None:
    async with transaction() as s:
        window = await runtime_settings.get_value(s, "reversal_timeout")
    uid = snap.pending_target_id
    await _dm(bot, snap, uid, rt.incoming_text(snap, uid, window), rk.incoming_kb(snap, uid), keep_prompt=True)
    await turn_ping.ping_defend(bot, snap.group_chat_id, f"r{snap.id}", uid, snap.player(uid).name,
                                snap.deadline_at, await bot_link(bot))


# --------------------------------------------------------------------- events

async def _publish_event(bot: Bot, snap: RumbleSnap, ev: REvent) -> None:
    if ev.kind in ("turn", "skip", "winner"):
        await turn_ping.clear(bot, snap.group_chat_id, f"r{snap.id}")
    if ev.kind == "entry":
        await media.send_event(bot, snap.group_chat_id, "rumble_entry", rt.entry_caption(snap, ev))
    elif ev.kind == "turn":
        for uid in (ev.user_id, ev.other_id):             # strip stale keyboards
            p = snap.player(uid)
            if p:
                await safe_clear_markup(bot, uid, p.prompt_message_id)
        await media.send_event(bot, snap.group_chat_id, "reversal" if ev.reversed else ev.move,
                               rt.turn_caption(snap, ev))
    elif ev.kind == "skip":
        p = snap.player(ev.user_id)
        await safe_clear_markup(bot, ev.user_id, p.prompt_message_id if p else None)
        await safe_send(bot, snap.group_chat_id, rt.skip_caption(snap, ev))
        await safe_send(bot, ev.user_id, "💤 You took too long — your turn was skipped. "
                                         "Miss another in a row and you're out!")
    elif ev.kind == "eliminated":
        gif = "rumble_over_the_top" if ev.finisher_kill else "rumble_elimination"
        await media.send_event(bot, snap.group_chat_id, gif, rt.elimination_caption(snap, ev))
        await _dm(bot, snap, ev.user_id, rt.eliminated_dm(snap, ev))
    elif ev.kind == "final_two":
        await media.send_event(bot, snap.group_chat_id, "rumble_final_two", rt.final_two_caption(snap))
    elif ev.kind == "winner":
        await media.send_event(bot, snap.group_chat_id, "rumble_winner", rt.winner_caption(snap))
        await _dm(bot, snap, ev.user_id, rt.winner_dm(snap))


async def publish_events(bot: Bot, snap: RumbleSnap, events: list[REvent]) -> None:
    for ev in events:
        await _publish_event(bot, snap, ev)
    await refresh_board(bot, snap)
    if snap.status == "active" and any(e.kind == "turn_start" for e in events):
        await prompt_actor(bot, snap)


async def publish_started(bot: Bot, snap: RumbleSnap, events: list[REvent]) -> None:
    await close_lobby(bot, snap, "✅ The Rumble has started — see below.")
    await media.send_event(bot, snap.group_chat_id, "rumble_start", rt.started_caption(snap))
    await publish_events(bot, snap, events)


async def publish_result(bot: Bot, result: RResult) -> None:
    snap = result.rumble
    if result.kind == "started":
        await publish_started(bot, snap, result.events)
    elif result.kind == "lobby_expired":
        await close_lobby(bot, snap, "⏳ Not enough wrestlers showed up. The Rumble was cancelled.")
    elif result.kind in ("entry", "turn_resolved", "turn_skipped"):
        await publish_events(bot, snap, result.events)


async def publish_cancelled(bot: Bot, snap: RumbleSnap) -> None:
    await turn_ping.clear(bot, snap.group_chat_id, f"r{snap.id}")
    await close_lobby(bot, snap, "🛑 The Rumble was cancelled.")
    if snap.board_message_id:
        await safe_edit(bot, snap.group_chat_id, snap.board_message_id, "🛑 <b>ROYAL RUMBLE — CANCELLED</b>")
    for p in snap.players:
        await safe_clear_markup(bot, p.user_id, p.prompt_message_id)


# ------------------------------------------------------------------ deadline loop

async def process_due(bot: Bot) -> int:
    async with transaction() as s:
        ids = await eng.due_rumble_ids(s)
    handled = 0
    for rumble_id in ids:
        try:
            async with transaction() as s:
                result = await eng.handle_due(s, rumble_id)
            if result is not None:
                handled += 1
                await publish_result(bot, result)
        except Exception:
            log.exception("Failed processing Rumble deadline %s", rumble_id)
    return handled


async def deadline_loop(bot: Bot, interval: float = 1.5) -> None:
    log.info("Rumble deadline loop started")
    while True:
        try:
            await process_due(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Rumble deadline loop iteration failed")
        await asyncio.sleep(interval)
