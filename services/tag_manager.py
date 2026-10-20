"""Publishes Tag Team events to Telegram and runs the Tag deadline loop (same idea as rumble_manager)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from database import transaction
from keyboards import tag as tk
from keyboards.main import open_bot_kb
from services import media_manager as media
from services import runtime_settings, turn_ping
from services import tag_engine as eng
from services import tag_text as tt
from services.tag_engine import TEvent, TResult, TagSnap
from services.tg import safe_clear_markup, safe_edit, safe_send

log = logging.getLogger(__name__)


async def bot_link(bot: Bot) -> str:
    return f"https://t.me/{(await bot.me()).username}"


def _key(snap: TagSnap) -> str:
    return f"t{snap.id}"


# ----------------------------------------------------------------------- lobby

async def publish_lobby(bot: Bot, snap: TagSnap) -> None:
    sent = await safe_send(bot, snap.group_chat_id, tt.lobby_text(snap),
                           reply_markup=tk.lobby_kb(snap.id, await bot_link(bot)))
    if sent.message_id:
        async with transaction() as s:
            await eng.set_message_id(s, snap.id, "lobby_message_id", sent.message_id)


async def refresh_lobby(bot: Bot, snap: TagSnap) -> None:
    if snap.lobby_message_id:
        await safe_edit(bot, snap.group_chat_id, snap.lobby_message_id, tt.lobby_text(snap),
                        reply_markup=tk.lobby_kb(snap.id, await bot_link(bot)))


async def close_lobby(bot: Bot, snap: TagSnap, why: str) -> None:
    if snap.lobby_message_id:
        await safe_edit(bot, snap.group_chat_id, snap.lobby_message_id, tt.lobby_closed_text(why))


# ----------------------------------------------------------------------- board

async def refresh_board(bot: Bot, snap: TagSnap) -> None:
    markup = tk.board_kb(snap.id) if snap.status == "active" else None
    if snap.board_message_id:
        await safe_edit(bot, snap.group_chat_id, snap.board_message_id, tt.board_text(snap), reply_markup=markup)
        return
    sent = await safe_send(bot, snap.group_chat_id, tt.board_text(snap), reply_markup=markup)
    if sent.message_id:
        async with transaction() as s:
            await eng.set_message_id(s, snap.id, "board_message_id", sent.message_id)


# ------------------------------------------------------------------- DM helpers

async def _dm(bot: Bot, snap: TagSnap, user_id: int, text: str, markup=None, keep_prompt: bool = False) -> None:
    sent = await safe_send(bot, user_id, text, reply_markup=markup)
    async with transaction() as s:
        if sent.forbidden:
            await eng.mark_dm_unreachable(s, user_id)
        elif keep_prompt and sent.message_id:
            await eng.set_prompt_message(s, snap.id, user_id, sent.message_id)
    if sent.forbidden:
        p = snap.player(user_id)
        await safe_send(bot, snap.group_chat_id,
                        f"⚠️ I can't message <b>{tt.esc(p.name if p else '?')}</b>. Open the bot privately and press Start.",
                        reply_markup=open_bot_kb(await bot_link(bot)))


async def prompt_actor(bot: Bot, snap: TagSnap) -> None:
    uid = snap.active_player_id
    await _dm(bot, snap, uid, tt.move_prompt(snap, uid), tk.move_kb(snap, uid), keep_prompt=True)
    await turn_ping.ping_move(bot, snap.group_chat_id, _key(snap), uid, snap.player(uid).name,
                              snap.deadline_at, await bot_link(bot))


async def publish_attack_declared(bot: Bot, snap: TagSnap) -> None:
    async with transaction() as s:
        window = await runtime_settings.get_value(s, "reversal_timeout")
    uid = snap.pending_target_id
    await _dm(bot, snap, uid, tt.incoming_text(snap, uid, window), tk.incoming_kb(snap, uid), keep_prompt=True)
    await turn_ping.ping_defend(bot, snap.group_chat_id, _key(snap), uid, snap.player(uid).name,
                                snap.deadline_at, await bot_link(bot))


# --------------------------------------------------------------------- events

async def _publish_event(bot: Bot, snap: TagSnap, ev: TEvent) -> None:
    gid = snap.group_chat_id
    if ev.kind in ("turn", "skip", "tag", "winner"):
        await turn_ping.clear(bot, gid, _key(snap))
    if ev.kind == "tag":
        p = snap.player(ev.user_id)
        await safe_clear_markup(bot, ev.user_id, p.prompt_message_id if p else None)
        await media.send_event(bot, gid, "tag_in", tt.tag_caption(snap, ev))
    elif ev.kind == "turn":
        for uid in (ev.user_id, ev.other_id):
            p = snap.player(uid)
            if p:
                await safe_clear_markup(bot, uid, p.prompt_message_id)
        await media.send_event(bot, gid, "reversal" if ev.reversed else ev.move, tt.turn_caption(snap, ev))
    elif ev.kind == "skip":
        p = snap.player(ev.user_id)
        await safe_clear_markup(bot, ev.user_id, p.prompt_message_id if p else None)
        await safe_send(bot, gid, tt.skip_caption(snap, ev))
        await safe_send(bot, ev.user_id, "💤 You took too long — your turn was skipped. "
                                         "Miss another in a row and you're out!")
    elif ev.kind == "eliminated":
        await media.send_event(bot, gid, "tag_ko", tt.elimination_caption(snap, ev))
        await _dm(bot, snap, ev.user_id, tt.eliminated_dm(snap, ev))
    elif ev.kind == "handicap":
        await media.send_event(bot, gid, "tag_handicap", tt.handicap_caption(snap, ev))
    elif ev.kind == "winner":
        await media.send_event(bot, gid, "tag_winner", tt.winner_caption(snap))
        for p in snap.players:
            await _dm(bot, snap, p.user_id, tt.result_dm(snap, p.user_id))


async def publish_events(bot: Bot, snap: TagSnap, events: list[TEvent]) -> None:
    for ev in events:
        await _publish_event(bot, snap, ev)
    await refresh_board(bot, snap)
    if snap.status == "active" and any(e.kind == "turn_start" for e in events):
        await prompt_actor(bot, snap)


async def publish_started(bot: Bot, snap: TagSnap, events: list[TEvent]) -> None:
    await close_lobby(bot, snap, "✅ The match has started — see below.")
    await media.send_event(bot, snap.group_chat_id, "tag_start", tt.started_caption(snap))
    await publish_events(bot, snap, events)


async def publish_result(bot: Bot, result: TResult) -> None:
    snap = result.match
    if result.kind == "started":
        await publish_started(bot, snap, result.events)
    elif result.kind == "lobby_expired":
        await close_lobby(bot, snap, "⏳ Not enough wrestlers (need 2 + 2). The match was cancelled.")
    elif result.kind in ("turn_resolved", "turn_skipped"):
        await publish_events(bot, snap, result.events)


async def publish_cancelled(bot: Bot, snap: TagSnap) -> None:
    await turn_ping.clear(bot, snap.group_chat_id, _key(snap))
    await close_lobby(bot, snap, "🛑 The Tag Team match was cancelled.")
    if snap.board_message_id:
        await safe_edit(bot, snap.group_chat_id, snap.board_message_id, "🛑 <b>TAG TEAM — CANCELLED</b>")
    for p in snap.players:
        await safe_clear_markup(bot, p.user_id, p.prompt_message_id)


# ------------------------------------------------------------------ deadline loop

async def process_due(bot: Bot) -> int:
    async with transaction() as s:
        ids = await eng.due_tag_ids(s)
    handled = 0
    for match_id in ids:
        try:
            async with transaction() as s:
                result = await eng.handle_due(s, match_id)
            if result is not None:
                handled += 1
                await publish_result(bot, result)
        except Exception:
            log.exception("Failed processing Tag Team deadline %s", match_id)
    return handled


async def deadline_loop(bot: Bot, interval: float = 1.5) -> None:
    log.info("Tag Team deadline loop started")
    while True:
        try:
            await process_due(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Tag Team deadline loop iteration failed")
        await asyncio.sleep(interval)
