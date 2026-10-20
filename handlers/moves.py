from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

from database import transaction
from handlers.common import edit_callback, int_parts, to_int
from services import match_engine as eng
from services import scoreboard as sb, turn_manager as tm
from services.rules import Move, RuleError, parse_move

router = Router(name="moves")


@router.callback_query(F.data.startswith("mv:"))
async def cb_move(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 4)
    match_id, turn = (to_int(parts[1]), to_int(parts[2])) if parts else (None, None)
    if match_id is None or turn is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    async with transaction() as s:           # all validation happens server-side in the engine
        result = await eng.submit_attack(s, match_id, cb.from_user.id, turn, parts[3])
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    move = parse_move(parts[3])
    await edit_callback(cb, sb.attack_sent_text(move))
    if result.kind == "attack_declared":
        await tm.publish_attack_declared(bot, result.match, move)
    else:
        await tm.publish_turn(bot, result.match, result.turn)


@router.callback_query(F.data.startswith("rv:"))
async def cb_response(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 4)
    match_id, turn = (to_int(parts[1]), to_int(parts[2])) if parts else (None, None)
    if match_id is None or turn is None or parts[3] not in ("r", "t"):
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    use = parts[3] == "r"
    async with transaction() as s:
        result = await eng.submit_response(s, match_id, cb.from_user.id, turn, use_reversal=use)
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    await edit_callback(cb, "🛡️ You went for a <b>REVERSAL</b> (100%)…" if use else "😤 You braced for impact…")
    await tm.publish_turn(bot, result.match, result.turn)


@router.callback_query(F.data.startswith("wd:"))
async def cb_wild(cb: CallbackQuery, bot: Bot) -> None:
    parts = int_parts(cb.data, 3)
    match_id, turn = (to_int(parts[1]), to_int(parts[2])) if parts else (None, None)
    if match_id is None or turn is None:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    async with transaction() as s:
        result = await eng.submit_wild_draw(s, match_id, cb.from_user.id, turn)
    if not result.ok:
        await cb.answer(result.error, show_alert=True)
        return
    await cb.answer()
    if result.kind == "wild_reversal":               # bonus Reversal: the player still picks an attack
        await edit_callback(cb, sb.wild_reversal_text())
        await tm.publish_wild_reversal(bot, result.match)
        return
    move = parse_move(result.move)
    await edit_callback(cb, sb.wild_attack_text(move))
    if result.kind == "attack_declared":
        await tm.publish_attack_declared(bot, result.match, move)
    else:
        await tm.publish_turn(bot, result.match, result.turn)


@router.callback_query(F.data.startswith("ms:"))
async def cb_status(cb: CallbackQuery) -> None:
    parts = int_parts(cb.data, 2)
    match_id = to_int(parts[1]) if parts else None
    if match_id is None:
        await cb.answer("Invalid button.", show_alert=True)
        return
    async with transaction() as s:
        snap = await eng.get_snapshot(s, match_id)
    if snap is None or snap.player(cb.from_user.id) is None:
        await cb.answer("🚫 You are not a fighter in this match.", show_alert=True)
        return
    await cb.answer(sb.status_alert(snap, cb.from_user.id), show_alert=True)
