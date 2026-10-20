from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from database import transaction
from handlers.common import edit_callback, identity_from, int_parts, is_group, to_int
from keyboards import tag as tk
from services import tag_engine as eng
from services import tag_manager as mgr
from services import tag_text as tt

router = Router(name="tag")


@router.message(Command("tag"))
async def cmd_tag(message: Message, bot: Bot, command: CommandObject) -> None:
    if message.from_user is None:
        return
    if not is_group(message):
        await message.answer("🏷️ Tag Team matches happen in groups. Add me to a group and send /tag (or /tag 3, /tag 4) there!")
        return
    arg = (command.args or "2").strip().lower().replace("v", " ").split()[0]
    size = to_int(arg)
    if size is None:
        await message.answer("Use /tag (2v2), /tag 3 (3v3) or /tag 4 (4v4).")
        return
    async with transaction() as s:
        result = await eng.create_tag(s, identity_from(message.from_user), message.chat.id, message.chat.title,
                                      team_size=size)
    if not result.ok:
        await message.answer(result.error)
        return
    await mgr.publish_lobby(bot, result.match)


@router.message(Command("canceltag"))
async def cmd_cancel(message: Message, bot: Bot) -> None:
    if message.from_user is None or not is_group(message):
        return
    async with transaction() as s:
        mid = await eng.open_tag_id_in_chat(s, message.chat.id)
        result = await eng.cancel(s, mid, message.from_user.id) if mid else None
    if result is None:
        await message.answer("There's no open Tag Team match in this group.")
    elif not result.ok:
        await message.answer(result.error)
    else:
        await message.answer("🛑 Tag Team match cancelled.")
        await mgr.publish_cancelled(bot, result.match)


@router.message(Command("tagboard"))
async def cmd_board(message: Message) -> None:
    async with transaction() as s:
        rows = await eng.tag_leaderboard(s)
    await message.answer(tt.leaderboard_text(rows))


# -------------------------------------------------------------------- lobby buttons

@router.callback_query(F.data.startswith("tl:"))
async def cb_lobby(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 3)
    mid = to_int(parts[2]) if parts else None
    if parts is None or mid is None or cb.message is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    action = parts[1]
    if action == "r":
        await cb.answer(tt.rules_text(), show_alert=True)
        return
    if action == "b":
        async with transaction() as s:
            snap = await eng.get_snapshot(s, mid)
        if snap is None or snap.group_chat_id != cb.message.chat.id:
            await cb.answer("This board is outdated.", show_alert=True)
            return
        await cb.answer("Updated")
        await mgr.refresh_board(bot, snap)
        return
    ident = identity_from(cb.from_user)
    async with transaction() as s:
        if action in ("j1", "j2"):
            result = await eng.join(s, mid, ident, cb.message.chat.id, 1 if action == "j1" else 2)
        elif action == "l":
            result = await eng.leave(s, mid, ident.id)
        elif action == "s":
            result = await eng.start(s, mid, ident.id)
        else:
            await cb.answer("Invalid button.", show_alert=True)
            return
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    if result.kind == "started":
        await cb.answer("🔔 Ding ding ding!")
        await mgr.publish_started(bot, result.match, result.events)
    elif result.kind == "cancelled":
        await cb.answer("Match cancelled")
        await mgr.close_lobby(bot, result.match, "🛑 Everyone left. The match was cancelled.")
    else:
        await cb.answer({"joined": "🥊 You're in!", "switched": "↔️ Switched teams!"}.get(result.kind, "You left."))
        await mgr.refresh_lobby(bot, result.match)


# ---------------------------------------------------------------------- DM buttons

@router.callback_query(F.data.startswith("tm:"))
async def cb_move(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 4)
    mid, turn = (to_int(parts[1]), to_int(parts[2])) if parts else (None, None)
    if mid is None or turn is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    async with transaction() as s:
        result = await eng.submit_move(s, mid, cb.from_user.id, turn, parts[3])
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    if result.kind == "attack_declared":
        await edit_callback(cb, tt.attack_sent_text(result.match))
        await mgr.publish_attack_declared(bot, result.match)
    else:                                                    # TAG: resolved straight away
        await edit_callback(cb, tt.tag_done_text(result.match, cb.from_user.id))
        await mgr.publish_events(bot, result.match, result.events)


@router.callback_query(F.data.startswith("td:"))
async def cb_response(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 4)
    mid, turn = (to_int(parts[1]), to_int(parts[2])) if parts else (None, None)
    if mid is None or turn is None or parts[3] not in ("r", "t"):
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    use = parts[3] == "r"
    async with transaction() as s:
        result = await eng.submit_response(s, mid, cb.from_user.id, turn, use_reversal=use)
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    await edit_callback(cb, "🛡️ You went for a <b>REVERSAL</b> (100%)…" if use else "😤 You braced for impact…")
    await mgr.publish_events(bot, result.match, result.events)


@router.callback_query(F.data.startswith("tx:"))
async def cb_status(cb: CallbackQuery) -> None:
    parts = int_parts(cb.data, 2)
    mid = to_int(parts[1]) if parts else None
    if mid is None:
        await cb.answer("Invalid button.", show_alert=True)
        return
    async with transaction() as s:
        snap = await eng.get_snapshot(s, mid)
    if snap is None:
        await cb.answer("This match is over.", show_alert=True)
        return
    await cb.answer(tt.status_alert(snap, cb.from_user.id), show_alert=True)
