from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database import transaction
from handlers.common import identity_from, int_parts, is_group, to_int
from keyboards import challenge as ck
from services import match_engine as eng
from services import scoreboard as sb, titles, turn_manager as tm
from services.tg import safe_clear_markup, safe_send

router = Router(name="match")


@router.message(Command("match"))
async def cmd_match(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return
    if not is_group(message):
        await message.answer("🥊 Challenges happen in groups. Add me to a group and send /match there!")
        return
    async with transaction() as s:
        result = await eng.create_challenge(s, identity_from(message.from_user), message.chat.id, message.chat.title)
    if not result.ok:
        await message.answer(result.error)
        return
    await tm.publish_challenge(bot, message.chat.id, result.match)


@router.callback_query(F.data.startswith("ch:"))
async def cb_accept(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 2)
    match_id = to_int(parts[1]) if parts else None
    if match_id is None or cb.message is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    async with transaction() as s:
        blocked = await titles.accept_error(s, match_id, cb.from_user.id)
    if blocked:
        await cb.answer(blocked, show_alert=True)
        return
    async with transaction() as s:
        result = await eng.accept_challenge(s, match_id, identity_from(cb.from_user), cb.message.chat.id)
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer("🔔 Challenge accepted!")
    await tm.publish_accepted(bot, result.match, result.missing_dm)


@router.callback_query(F.data.startswith("sb:"))
async def cb_scoreboard(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 2)
    match_id = to_int(parts[1]) if parts else None
    if match_id is None or cb.message is None:
        await cb.answer("Invalid button.", show_alert=True)
        return
    async with transaction() as s:
        snap = await eng.get_snapshot(s, match_id)
    if snap is None or snap.group_chat_id != cb.message.chat.id:
        await cb.answer("This scoreboard is outdated.", show_alert=True)
        return
    markup = ck.scoreboard_kb(match_id) if snap.status in ("active", "paused") else None
    try:
        await cb.message.edit_text(sb.scoreboard_text(snap), reply_markup=markup)
        await cb.answer("Scoreboard updated")
    except Exception:  # "message is not modified" or deleted message: never crash the handler
        await cb.answer("Already up to date")


@router.callback_query(F.data.startswith("rs:"))
async def cb_resume(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 2)
    match_id = to_int(parts[1]) if parts else None
    if match_id is None:
        await cb.answer("Invalid button.", show_alert=True)
        return
    async with transaction() as s:
        result = await eng.resume_match(s, match_id, cb.from_user.id)
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer("▶️ Resuming!")
    if cb.message is not None:
        await safe_clear_markup(bot, cb.message.chat.id, cb.message.message_id)
    await tm.publish_resumed(bot, result.match)


@router.message(Command("cancelmatch"))
async def cmd_cancel(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return
    uid = message.from_user.id
    async with transaction() as s:
        result = await eng.cancel_command(s, uid)
    if not result.ok:
        await message.answer(result.error)
        return
    if result.kind == "cancelled":
        await message.answer("🛑 Cancelled.")
        await tm.publish_cancelled(bot, result.match)
    elif result.kind == "cancel_requested":
        opp = result.match.other(uid)
        name = sb.esc(result.match.player(uid).name)
        await safe_send(bot, opp.user_id, f"🛑 <b>{name}</b> wants to cancel your match. No stats will be recorded.",
                        reply_markup=ck.cancel_confirm_kb(result.match.id))
        await message.answer("📨 Cancellation request sent. Your opponent must agree.")


@router.callback_query(F.data.startswith("cx:"))
async def cb_cancel_answer(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 3)
    match_id = to_int(parts[1]) if parts else None
    if match_id is None or parts[2] not in ("y", "n"):
        await cb.answer("Invalid button.", show_alert=True)
        return
    async with transaction() as s:
        result = await eng.respond_cancel(s, match_id, cb.from_user.id, agree=parts[2] == "y")
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    if cb.message is not None:
        await safe_clear_markup(bot, cb.message.chat.id, cb.message.message_id)
    if result.kind == "cancelled":
        await tm.publish_cancelled(bot, result.match)
    else:
        for p in result.match.players:
            await safe_send(bot, p.user_id, "❌ The cancellation was declined — the fight goes on!")
