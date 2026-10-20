from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from database import transaction
from handlers.common import edit_callback, identity_from, int_parts, is_group, to_int
from keyboards import mitb as mk
from services import mitb_engine as eng
from services import mitb_manager as mgr
from services import mitb_rules as mr
from services import mitb_text as mt

router = Router(name="mitb")


def _parse_args(args: str | None) -> tuple[int, str] | None:
    capacity, mode = 8, "fast"
    for token in (args or "").lower().split():
        if token.isdigit() and int(token) in mr.SIZES:
            capacity = int(token)
        elif token in mr.TIMINGS:
            mode = token
        else:
            return None
    return capacity, mode


@router.message(Command("mitb"))
async def cmd_mitb(message: Message, bot: Bot, command: CommandObject) -> None:
    if message.from_user is None:
        return
    if not is_group(message):
        await message.answer("💼 Money in the Bank is played in groups. Add me to a group and send /mitb there!")
        return
    parsed = _parse_args(command.args)
    if parsed is None:
        await message.answer("Use /mitb (8 players), /mitb 16 or /mitb 32 — add `slow` for slow mode, e.g. /mitb 16 slow.")
        return
    capacity, mode = parsed
    async with transaction() as s:
        result = await eng.create(s, identity_from(message.from_user), message.chat.id, message.chat.title,
                                  capacity=capacity, mode=mode)
    if not result.ok:
        await message.answer(result.error)
        return
    await mgr.publish_lobby(bot, result.event)


@router.message(Command("cancelmitb"))
async def cmd_cancel(message: Message, bot: Bot) -> None:
    if message.from_user is None or not is_group(message):
        return
    async with transaction() as s:
        eid = await eng.open_event_id_in_chat(s, message.chat.id)
        result = await eng.cancel(s, eid, message.from_user.id) if eid else None
    if result is None:
        await message.answer("There's no open tournament in this group.")
    elif not result.ok:
        await message.answer(result.error)
    else:
        await message.answer("🛑 Tournament cancelled.")
        await mgr.publish_cancelled(bot, result.event)


@router.message(Command("mitbboard"))
async def cmd_board(message: Message) -> None:
    async with transaction() as s:
        rows = await eng.mitb_leaderboard(s)
    await message.answer(mt.leaderboard_text(rows))


@router.message(Command("briefcase"))
async def cmd_briefcase(message: Message) -> None:
    if message.from_user is None:
        return
    async with transaction() as s:
        b = await eng.active_briefcase(s, message.from_user.id)
    await message.answer(mt.briefcase_text(b))


# -------------------------------------------------------------------- lobby buttons

@router.callback_query(F.data.startswith("bl:"))
async def cb_lobby(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 3)
    eid = to_int(parts[2]) if parts else None
    if parts is None or eid is None or cb.message is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    action = parts[1]
    if action == "r":
        await cb.answer(mt.rules_text().replace("`", ""), show_alert=True)
        return
    if action == "b":
        async with transaction() as s:
            snap = await eng.get_snapshot(s, eid)
        if snap is None or snap.group_chat_id != cb.message.chat.id:
            await cb.answer("This board is outdated.", show_alert=True)
            return
        await cb.answer("Updated")
        await mgr.refresh_board(bot, snap)
        return
    ident = identity_from(cb.from_user)
    async with transaction() as s:
        if action == "j":
            result = await eng.join(s, eid, ident, cb.message.chat.id)
        elif action == "l":
            result = await eng.leave(s, eid, ident.id)
        elif action == "s":
            result = await eng.start(s, eid, ident.id)
        else:
            await cb.answer("Invalid button.", show_alert=True)
            return
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    if result.kind == "started":
        await cb.answer("🔔 Let the tournament begin!")
        await mgr.publish_started(bot, result.event, result.events)
    elif result.kind == "cancelled":
        await cb.answer("Cancelled")
        await mgr.close_lobby(bot, result.event, "🛑 Everyone left. The tournament was cancelled.")
    else:
        await cb.answer("🥊 You're in!" if result.kind == "joined" else "You left the lobby.")
        await mgr.refresh_lobby(bot, result.event)


# ------------------------------------------------------------------ qualifier duels

@router.callback_query(F.data.startswith("dm:"))
async def cb_duel_move(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 4)
    did, turn = (to_int(parts[1]), to_int(parts[2])) if parts else (None, None)
    if did is None or turn is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    async with transaction() as s:
        result = await eng.duel_submit_move(s, did, cb.from_user.id, turn, parts[3])
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    await edit_callback(cb, mt.duel_attack_sent(result.duel))
    await mgr.prompt_duel_defender(bot, result.duel)


@router.callback_query(F.data.startswith("dr:"))
async def cb_duel_response(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 4)
    did, turn = (to_int(parts[1]), to_int(parts[2])) if parts else (None, None)
    if did is None or turn is None or parts[3] not in ("r", "t"):
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    use = parts[3] == "r"
    async with transaction() as s:
        result = await eng.duel_submit_response(s, did, cb.from_user.id, turn, use_reversal=use)
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    await edit_callback(cb, "🛡️ You went for a <b>REVERSAL</b> (100%)…" if use else "😤 You braced for impact…")
    await mgr.publish_duel_result(bot, result)


# ----------------------------------------------------------------------- ladder match

@router.callback_query(F.data.startswith("lm:"))
async def cb_ladder_move(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 4)
    eid, turn = (to_int(parts[1]), to_int(parts[2])) if parts else (None, None)
    if eid is None or turn is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    async with transaction() as s:
        result = await eng.ladder_move(s, eid, cb.from_user.id, turn, parts[3])
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    if result.kind == "move_chosen":
        await edit_callback(cb, mt.ladder_target_prompt(result.event), mk.ladder_target_kb(result.event, cb.from_user.id))
    else:                                                     # CLIMB resolved at once
        await edit_callback(cb, "🪜 You climbed!")
        await mgr.dispatch(bot, result.event, None, result.events)


@router.callback_query(F.data.startswith("lk:"))
async def cb_ladder_back(cb: CallbackQuery) -> None:
    parts = int_parts(cb.data, 3)
    eid, turn = (to_int(parts[1]), to_int(parts[2])) if parts else (None, None)
    if eid is None or turn is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    async with transaction() as s:
        result = await eng.ladder_back(s, eid, cb.from_user.id, turn)
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    await edit_callback(cb, mt.ladder_move_prompt(result.event, cb.from_user.id), mk.ladder_move_kb(result.event, cb.from_user.id))


@router.callback_query(F.data.startswith("lt:"))
async def cb_ladder_target(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 4)
    eid, turn, target = (to_int(parts[1]), to_int(parts[2]), to_int(parts[3])) if parts else (None, None, None)
    if eid is None or turn is None or target is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    async with transaction() as s:
        result = await eng.ladder_target(s, eid, cb.from_user.id, turn, target)
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    await edit_callback(cb, mt.ladder_attack_sent(result.event))
    await mgr.prompt_ladder_defender(bot, result.event)


@router.callback_query(F.data.startswith("lr:"))
async def cb_ladder_response(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 4)
    eid, turn = (to_int(parts[1]), to_int(parts[2])) if parts else (None, None)
    if eid is None or turn is None or parts[3] not in ("r", "t"):
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    use = parts[3] == "r"
    async with transaction() as s:
        result = await eng.ladder_response(s, eid, cb.from_user.id, turn, use_reversal=use)
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    await edit_callback(cb, "🛡️ You went for a <b>REVERSAL</b> (100%)…" if use else "😤 You braced for impact…")
    await mgr.dispatch(bot, result.event, None, result.events)


# ------------------------------------------------------------------ status & cash-in

@router.callback_query(F.data.startswith("bx:"))
async def cb_status(cb: CallbackQuery) -> None:
    parts = int_parts(cb.data, 3)
    ident = to_int(parts[2]) if parts else None
    if ident is None or parts[1] not in ("e", "d"):
        await cb.answer("Invalid button.", show_alert=True)
        return
    async with transaction() as s:
        if parts[1] == "e":
            snap = await eng.get_snapshot(s, ident)
            text = mt.status_alert_event(snap, cb.from_user.id) if snap else None
        else:
            duel = await eng.get_duel_snapshot(s, ident)
            text = mt.status_alert_duel(duel, cb.from_user.id) if duel else None
    await cb.answer(text or "This match is over.", show_alert=True)


@router.callback_query(F.data.startswith("cc:"))
async def cb_cashin(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 2)
    oid = to_int(parts[1]) if parts else None
    if oid is None or cb.message is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    async with transaction() as s:
        result = await eng.take_cashin(s, oid, cb.from_user.id)
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer("💼 CASH IN!")
    await edit_callback(cb, mt.offer_taken_text(result.offer))
    await mgr.publish_duel_result(bot, result)
