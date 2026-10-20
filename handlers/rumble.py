from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database import transaction
from handlers.common import edit_callback, identity_from, int_parts, is_group, to_int
from services import rumble_engine as eng
from services import rumble_manager as mgr
from services import rumble_text as rt
from keyboards import rumble as rk

router = Router(name="rumble")


@router.message(Command("rumble"))
async def cmd_rumble(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return
    if not is_group(message):
        await message.answer("🔔 The Royal Rumble happens in groups. Add me to a group and send /rumble there!")
        return
    async with transaction() as s:
        result = await eng.create_rumble(s, identity_from(message.from_user), message.chat.id, message.chat.title)
    if not result.ok:
        await message.answer(result.error)
        return
    await mgr.publish_lobby(bot, result.rumble)


@router.message(Command("cancelrumble"))
async def cmd_cancel(message: Message, bot: Bot) -> None:
    if message.from_user is None or not is_group(message):
        return
    async with transaction() as s:
        rid = await eng.open_rumble_id_in_chat(s, message.chat.id)
        result = await eng.cancel(s, rid, message.from_user.id) if rid else None
    if result is None:
        await message.answer("There's no open Rumble in this group.")
    elif not result.ok:
        await message.answer(result.error)
    else:
        await message.answer("🛑 Rumble cancelled.")
        await mgr.publish_cancelled(bot, result.rumble)


@router.message(Command("rumbleboard"))
async def cmd_board(message: Message) -> None:
    async with transaction() as s:
        rows = await eng.rumble_leaderboard(s)
    await message.answer(rt.leaderboard_text(rows))


# -------------------------------------------------------------------- lobby buttons

@router.callback_query(F.data.startswith("ru:"))
async def cb_lobby(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 3)
    rid = to_int(parts[2]) if parts else None
    if parts is None or rid is None or cb.message is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    action = parts[1]
    if action == "r":
        await cb.answer(rt.rules_text().replace("<b>", "").replace("</b>", ""), show_alert=True)
        return
    if action == "b":
        async with transaction() as s:
            snap = await eng.get_snapshot(s, rid)
        if snap is None or snap.group_chat_id != cb.message.chat.id:
            await cb.answer("This board is outdated.", show_alert=True)
            return
        await cb.answer("Updated")
        await mgr.refresh_board(bot, snap)
        return
    ident = identity_from(cb.from_user)
    async with transaction() as s:
        if action == "j":
            result = await eng.join(s, rid, ident, cb.message.chat.id)
        elif action == "l":
            result = await eng.leave(s, rid, ident.id)
        elif action == "s":
            result = await eng.start(s, rid, ident.id)
        else:
            await cb.answer("Invalid button.", show_alert=True)
            return
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    if result.kind == "started":
        await cb.answer("🔔 Here we go!")
        await mgr.publish_started(bot, result.rumble, result.events)
    elif result.kind == "cancelled":
        await cb.answer("Rumble cancelled")
        await mgr.close_lobby(bot, result.rumble, "🛑 Everyone left. The Rumble was cancelled.")
    else:
        await cb.answer("🥊 You're in!" if result.kind == "joined" else "You left the lobby.")
        await mgr.refresh_lobby(bot, result.rumble)


# ---------------------------------------------------------------------- DM buttons

@router.callback_query(F.data.startswith("rm:"))
async def cb_move(cb: CallbackQuery) -> None:
    parts = int_parts(cb.data, 4)
    rid, turn = (to_int(parts[1]), to_int(parts[2])) if parts else (None, None)
    if rid is None or turn is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    async with transaction() as s:
        result = await eng.submit_move(s, rid, cb.from_user.id, turn, parts[3])
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    await edit_callback(cb, rt.target_prompt(result.rumble), rk.target_kb(result.rumble, cb.from_user.id))


@router.callback_query(F.data.startswith("rk:"))
async def cb_back(cb: CallbackQuery) -> None:
    parts = int_parts(cb.data, 3)
    rid, turn = (to_int(parts[1]), to_int(parts[2])) if parts else (None, None)
    if rid is None or turn is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    async with transaction() as s:
        result = await eng.back_to_moves(s, rid, cb.from_user.id, turn)
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    await edit_callback(cb, rt.move_prompt(result.rumble, cb.from_user.id), rk.move_kb(result.rumble, cb.from_user.id))


@router.callback_query(F.data.startswith("rt:"))
async def cb_target(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 4)
    rid, turn, target = (to_int(parts[1]), to_int(parts[2]), to_int(parts[3])) if parts else (None, None, None)
    if rid is None or turn is None or target is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    async with transaction() as s:
        result = await eng.submit_target(s, rid, cb.from_user.id, turn, target)
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    await edit_callback(cb, rt.attack_sent_text(result.rumble))
    await mgr.publish_attack_declared(bot, result.rumble)


@router.callback_query(F.data.startswith("rr:"))
async def cb_response(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 4)
    rid, turn = (to_int(parts[1]), to_int(parts[2])) if parts else (None, None)
    if rid is None or turn is None or parts[3] not in ("r", "t"):
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    use = parts[3] == "r"
    async with transaction() as s:
        result = await eng.submit_response(s, rid, cb.from_user.id, turn, use_reversal=use)
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    await edit_callback(cb, "🛡️ You went for a <b>REVERSAL</b> (100%)…" if use else "😤 You braced for impact…")
    await mgr.publish_events(bot, result.rumble, result.events)


@router.callback_query(F.data.startswith("rx:"))
async def cb_status(cb: CallbackQuery) -> None:
    parts = int_parts(cb.data, 2)
    rid = to_int(parts[1]) if parts else None
    if rid is None:
        await cb.answer("Invalid button.", show_alert=True)
        return
    async with transaction() as s:
        snap = await eng.get_snapshot(s, rid)
    if snap is None:
        await cb.answer("This Rumble is over.", show_alert=True)
        return
    await cb.answer(rt.status_alert(snap, cb.from_user.id), show_alert=True)
