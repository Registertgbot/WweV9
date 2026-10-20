"""Publishes match events to Telegram and runs the persistent deadline loop.

State lives in PostgreSQL; this module only *reacts* to committed results, so a crash
between commit and publish never corrupts a match. Deadlines are polled from the database,
which makes them restart-safe.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from config import get_settings
from database import transaction
from keyboards import challenge as ck
from keyboards import moves as mk
from keyboards.main import open_bot_kb
from services import match_engine as eng
from services import media_manager as media
from services import rules, runtime_settings
from services import scoreboard as sb
from services.match_engine import MatchSnap, Result, TurnSnap
from services.rules import Move
from services import mitb_manager, turn_ping
from services.tg import safe_clear_markup, safe_edit, safe_send

log = logging.getLogger(__name__)


async def bot_link(bot: Bot, payload: str = "") -> str:
    me = await bot.me()
    return f"https://t.me/{me.username}" + (f"?start={payload}" if payload else "")


# ------------------------------------------------------------------------ challenge

async def publish_challenge(bot: Bot, chat_id: int, snap: MatchSnap) -> None:
    timeout = await _challenge_timeout()
    sent = await safe_send(bot, chat_id, sb.challenge_text(snap.players[0].name, snap.group_title, timeout),
                           reply_markup=ck.accept_kb(snap.id))
    if sent.message_id:
        async with transaction() as s:
            await eng.set_message_id(s, snap.id, "challenge_message_id", sent.message_id)


async def _challenge_timeout() -> int:
    async with transaction() as s:
        return await runtime_settings.get_value(s, "challenge_timeout")


async def publish_accepted(bot: Bot, snap: MatchSnap, missing: list[int]) -> None:
    if snap.challenge_message_id:        # remove the ACCEPT button: no further acceptance
        await safe_edit(bot, snap.group_chat_id, snap.challenge_message_id,
                        sb.challenge_closed_text(snap, "✅ Challenge accepted — see below."))
    link = await bot_link(bot, f"m{snap.id}")
    text = sb.accepted_text(snap)
    if missing:
        text += "\n\n" + sb.dm_required_text(snap, missing)
    await safe_send(bot, snap.group_chat_id, text, reply_markup=ck.accepted_kb(link))
    if snap.status == "active":
        await publish_live(bot, snap)


async def publish_live(bot: Bot, snap: MatchSnap) -> None:
    sent = await safe_send(bot, snap.group_chat_id, sb.live_text(snap), reply_markup=ck.scoreboard_kb(snap.id))
    if sent.message_id:
        async with transaction() as s:
            await eng.set_message_id(s, snap.id, "scoreboard_message_id", sent.message_id)
    await send_turn_prompts(bot, snap)


# ---------------------------------------------------------------------- DM prompts

async def _dm(bot: Bot, snap: MatchSnap, user_id: int, text: str, markup=None, keep_prompt: bool = False) -> None:
    sent = await safe_send(bot, user_id, text, reply_markup=markup)
    async with transaction() as s:
        if sent.forbidden:
            await eng.mark_dm_unreachable(s, user_id)
        elif keep_prompt and sent.message_id:
            await eng.set_prompt_message(s, snap.id, user_id, sent.message_id)
    if sent.forbidden:
        link = await bot_link(bot)
        name = sb.esc(snap.player(user_id).name)
        await safe_send(bot, snap.group_chat_id,
                        f"⚠️ I can't message <b>{name}</b>. Open the bot privately and press Start so you can play.",
                        reply_markup=open_bot_kb(link))


async def _ping_move(bot: Bot, snap: MatchSnap) -> None:
    """Tell the active fighter, in the group, that it is their turn (they choose in DM)."""
    active = snap.active_player_id
    await turn_ping.ping_move(bot, snap.group_chat_id, f"m{snap.id}", active, snap.player(active).name,
                              snap.deadline_at, await bot_link(bot))


async def send_turn_prompts(bot: Bot, snap: MatchSnap) -> None:
    """Active player gets the move keyboard; the other gets a waiting notice (never the opponent's choices)."""
    active = snap.active_player_id
    other = snap.other(active).user_id
    await _dm(bot, snap, active, sb.move_prompt_text(snap, active), mk.move_kb(snap, active), keep_prompt=True)
    await _dm(bot, snap, other, sb.waiting_text(snap, other))
    await _ping_move(bot, snap)


async def publish_wild_reversal(bot: Bot, snap: MatchSnap) -> None:
    """A Wild Draw gave a bonus Reversal: only the drawer is prompted again (their turn continues)."""
    active = snap.active_player_id
    await _dm(bot, snap, active, sb.move_prompt_text(snap, active), mk.move_kb(snap, active), keep_prompt=True)
    await _ping_move(bot, snap)


async def publish_attack_declared(bot: Bot, snap: MatchSnap, move: Move) -> None:
    defender = snap.other(snap.active_player_id).user_id
    window = await _reversal_timeout()
    shown = move if get_settings().reveal_incoming_move else None
    await _dm(bot, snap, defender, sb.incoming_text(snap, defender, shown, window),
              mk.incoming_kb(snap, defender), keep_prompt=True)
    await turn_ping.ping_defend(bot, snap.group_chat_id, f"m{snap.id}", defender, snap.player(defender).name,
                                snap.deadline_at, await bot_link(bot))


async def _reversal_timeout() -> int:
    async with transaction() as s:
        return await runtime_settings.get_value(s, "reversal_timeout")


# ------------------------------------------------------------------------ results

async def publish_turn(bot: Bot, snap: MatchSnap, turn: TurnSnap) -> None:
    await turn_ping.clear(bot, snap.group_chat_id, f"m{snap.id}")
    for p in snap.players:                                   # strip stale keyboards
        await safe_clear_markup(bot, p.user_id, p.prompt_message_id)
    event = "reversal" if turn.reversed else turn.move
    await media.send_event(bot, snap.group_chat_id, event, sb.event_caption(snap, turn))
    if turn.finished:
        await media.send_event(bot, snap.group_chat_id, "knockout", sb.knockout_caption(snap, turn))
        for p in snap.players:
            await _dm(bot, snap, p.user_id, sb.dm_result_text(snap, turn, p.user_id) + "\n\n" + sb.final_dm_text(snap, p.user_id))
        if snap.fixture_id is None:                           # tournament fixtures never open a cash-in window
            await mitb_manager.offer_after_match(bot, snap)       # Money in the Bank: cash-in window
        return
    for p in snap.players:
        note = "⏱️ No Reversal in time — normal damage applied.\n\n" if turn.auto else ""
        await _dm(bot, snap, p.user_id, note + sb.dm_result_text(snap, turn, p.user_id))
    await send_turn_prompts(bot, snap)


async def publish_result(bot: Bot, result: Result) -> None:
    """Publish the outcome of a deadline-driven transition."""
    snap, kind = result.match, result.kind
    if kind != "turn_resolved":                              # publish_turn clears it itself
        await turn_ping.clear(bot, snap.group_chat_id, f"m{snap.id}")
    if kind == "turn_resolved":
        await publish_turn(bot, snap, result.turn)
    elif kind == "challenge_expired":
        if snap.challenge_message_id:
            await safe_edit(bot, snap.group_chat_id, snap.challenge_message_id,
                            sb.challenge_closed_text(snap, "⏳ Nobody stepped up in time. Challenge expired."))
    elif kind == "dm_timeout":
        await safe_send(bot, snap.group_chat_id, "⏳ The match was cancelled: not everyone pressed Start in the bot's private chat in time.")
    elif kind == "pause_expired":
        await safe_send(bot, snap.group_chat_id, "⏳ The paused match expired and was cancelled. No stats were recorded.")
    elif kind == "timeout_cancelled":
        await safe_send(bot, snap.group_chat_id, "🚪 A fighter didn't answer the bell. The match was cancelled; no stats were recorded.")
        for p in snap.players:
            await safe_send(bot, p.user_id, "🚪 The match was cancelled because a move wasn't chosen in time.")
    elif kind == "paused":
        text = sb.pause_text(snap)
        await safe_send(bot, snap.group_chat_id, text, reply_markup=ck.resume_kb(snap.id))
        for p in snap.players:
            await _dm(bot, snap, p.user_id, text, ck.resume_kb(snap.id))
            await safe_clear_markup(bot, p.user_id, p.prompt_message_id)
    elif kind == "forfeit":
        await media.send_event(bot, snap.group_chat_id, "knockout", sb.forfeit_caption(snap, result.turn))
        for p in snap.players:
            await _dm(bot, snap, p.user_id, sb.final_dm_text(snap, p.user_id))
            await safe_clear_markup(bot, p.user_id, p.prompt_message_id)


async def publish_resumed(bot: Bot, snap: MatchSnap) -> None:
    await safe_send(bot, snap.group_chat_id, f"▶️ <b>MATCH RESUMED</b> — <b>{sb.esc(snap.player(snap.active_player_id).name)}</b> is up!")
    await send_turn_prompts(bot, snap)


async def publish_cancelled(bot: Bot, snap: MatchSnap) -> None:
    await turn_ping.clear(bot, snap.group_chat_id, f"m{snap.id}")
    if snap.challenge_message_id and snap.opponent_id is None:
        await safe_edit(bot, snap.group_chat_id, snap.challenge_message_id,
                        sb.challenge_closed_text(snap, "🛑 Challenge cancelled by the challenger."))
        return
    await safe_send(bot, snap.group_chat_id, "🛑 The match was cancelled. No stats were recorded.")
    for p in snap.players:
        await safe_clear_markup(bot, p.user_id, p.prompt_message_id)
        await safe_send(bot, p.user_id, "🛑 Your match was cancelled.")


# ------------------------------------------------------------------ deadline loop

async def process_due(bot: Bot) -> int:
    async with transaction() as s:
        ids = await eng.due_match_ids(s)
    handled = 0
    for match_id in ids:
        try:
            async with transaction() as s:
                result = await eng.handle_due_match(s, match_id)
            if result is not None:
                handled += 1
                await publish_result(bot, result)
        except Exception:  # keep the loop alive; the row stays consistent because commit is atomic
            log.exception("Failed processing deadline for match %s", match_id)
    return handled


async def deadline_loop(bot: Bot, interval: float = 1.5) -> None:
    log.info("Deadline loop started")
    while True:
        try:
            await process_due(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Deadline loop iteration failed")
        await asyncio.sleep(interval)
