from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database import transaction
from handlers.common import identity_from, is_group, to_int
from keyboards import hub as hk
from keyboards import tour as tk_
from services import hub
from services import match_engine as match_eng
from services import mitb_engine as mitb_eng
from services import mitb_manager as mitb_mgr
from services import mitb_rules as mr
from services import mitb_text as mt
from services import rumble_engine as rumble_eng
from services import rumble_manager as rumble_mgr
from services import rumble_text as rt
from services import stats, texts
from services import tag_engine as tag_eng
from services import tag_manager as tag_mgr
from services import tag_rules as tr
from services import tag_text as tt
from services import tour_text as tt_
from services import titles as title_svc
from services import turn_manager as tm
from services.hub import HubStatus

router = Router(name="hub")


async def _status(chat_id: int) -> HubStatus:
    async with transaction() as s:
        return HubStatus(rumble=await rumble_eng.open_rumble_id_in_chat(s, chat_id) is not None,
                         tag=await tag_eng.open_tag_id_in_chat(s, chat_id) is not None,
                         mitb=await mitb_eng.open_event_id_in_chat(s, chat_id) is not None)


async def _edit_hub(cb: CallbackQuery, caption: str, markup) -> None:
    """Edit the hub message, whether it is a picture/animation (caption) or plain text."""
    msg = cb.message
    if msg is None:
        return
    try:
        if msg.photo or msg.animation or msg.video or msg.document:
            await msg.edit_caption(caption=caption, reply_markup=markup)
        else:
            await msg.edit_text(caption, reply_markup=markup)
    except TelegramBadRequest:
        pass                                                 # "message is not modified"


async def _show_home(cb: CallbackQuery) -> None:
    status = await _status(cb.message.chat.id)
    await _edit_hub(cb, hub.welcome_caption(status), hk.main_kb(status))


@router.message(Command("play", "arena"))
async def cmd_play(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return
    if not is_group(message):
        await message.answer("🏟️ The arena menu opens in groups. Add me to a group and send /play there!")
        return
    status = await _status(message.chat.id)
    await hub.send_welcome(bot, message.chat.id, hub.welcome_caption(status), hk.main_kb(status))


# ----------------------------------------------------------------------------- buttons

@router.callback_query(F.data.startswith("hub:"))
async def cb_hub(cb: CallbackQuery, bot: Bot) -> None:
    parts = (cb.data or "").split(":")
    if cb.message is None or len(parts) < 2 or cb.message.chat.type not in ("group", "supergroup"):
        await cb.answer("Open /play in a group.", show_alert=True)
        return
    action, chat = parts[1], cb.message.chat
    ident = identity_from(cb.from_user)

    if action == "home":
        await cb.answer()
        await _show_home(cb)
    elif action == "busy":
        await cb.answer("⏳ That mode is already running in this group — join its lobby or wait for it to finish.",
                        show_alert=True)
    elif action == "tag" and len(parts) == 2:
        await cb.answer()
        await _edit_hub(cb, hub.tag_menu_caption(), hk.tag_kb())
    elif action == "mitb" and len(parts) == 2:
        await cb.answer()
        await _edit_hub(cb, hub.mitb_menu_caption(), hk.mitb_kb())
    elif action == "lb" and len(parts) == 2:
        await cb.answer()
        await _edit_hub(cb, hub.leaderboard_menu_caption(), hk.lb_kb())
    elif action == "tour" and len(parts) == 2:
        await cb.answer()
        await _edit_hub(cb, tt_.menu_caption(), tk_.menu_kb())
    elif action == "belts" and len(parts) == 2:
        await cb.answer()
        await _edit_hub(cb, "👑 <b>Championships</b>\nPick a belt — a title match lobby opens here. The winner takes the belt!",
                        hk.belts_kb())
    elif action == "bth":
        await cb.answer()
        from handlers.titles import send_board
        await send_board(bot, chat.id)
    elif action == "bt" and len(parts) == 3 and parts[2] in title_svc.BELTS:
        belt = title_svc.BELTS[parts[2]]
        async with transaction() as s:
            if await title_svc.is_contested(s, belt.key):
                await cb.answer("⚔️ A title match for this belt is already running.", show_alert=True)
                return
            if belt.kind == "solo":
                result = await match_eng.create_challenge(s, ident, chat.id, chat.title)
            else:
                result = await tag_eng.create_tag(s, ident, chat.id, chat.title, team_size=2)
            note = ""
            if result.ok:
                await title_svc.link(s, belt.key, belt.kind, result.match.id, chat.id)
                note = await title_svc.prepare(s, belt, result.match.id, ident.id)
                if belt.kind == "solo":
                    result.match = await match_eng.get_snapshot(s, result.match.id) or result.match
        if not result.ok:
            await cb.answer(result.error, show_alert=True)
            return
        await cb.answer(f"{belt.emoji} Title match opened!")
        await bot.send_message(chat.id, f"{belt.emoji} <b>TITLE MATCH</b> — {belt.name}\nThe winner takes the belt!\n{note}")
        if belt.kind == "solo":
            await tm.publish_challenge(bot, chat.id, result.match)
        else:
            await tag_mgr.publish_lobby(bot, result.match)
            await _show_home(cb)
    elif action == "help" and len(parts) == 2:
        await cb.answer()
        await _edit_hub(cb, hub.help_menu_caption(), hk.help_kb())
    elif action == "lb" and len(parts) == 3:
        await cb.answer()
        await _send_board(bot, chat.id, parts[2])
    elif action == "h" and len(parts) == 3:
        await cb.answer()
        await bot.send_message(chat.id, {"1": texts.HELP, "r": rt.rules_text(), "t": tt.rules_text(),
                                         "m": mt.rules_text()}.get(parts[2], texts.HELP))
    elif action == "m1":
        async with transaction() as s:
            result = await match_eng.create_challenge(s, ident, chat.id, chat.title)
        if not result.ok:
            await cb.answer(result.error, show_alert=True)
            return
        await cb.answer("🥊 Challenge sent!")
        await tm.publish_challenge(bot, chat.id, result.match)
    elif action == "rumble":
        async with transaction() as s:
            result = await rumble_eng.create_rumble(s, ident, chat.id, chat.title)
        if not result.ok:
            await cb.answer(result.error, show_alert=True)
            return
        await cb.answer("🔔 Royal Rumble lobby opened!")
        await rumble_mgr.publish_lobby(bot, result.rumble)
        await _show_home(cb)
    elif action == "tg" and len(parts) == 3 and to_int(parts[2]) in range(tr.MIN_TEAM_SIZE, tr.MAX_TEAM_SIZE + 1):
        async with transaction() as s:
            result = await tag_eng.create_tag(s, ident, chat.id, chat.title, team_size=to_int(parts[2]))
        if not result.ok:
            await cb.answer(result.error, show_alert=True)
            return
        await cb.answer("🏷️ Tag Team lobby opened!")
        await tag_mgr.publish_lobby(bot, result.match)
        await _show_home(cb)
    elif action == "mb" and len(parts) == 4 and to_int(parts[2]) in mr.SIZES and parts[3] in mr.TIMINGS:
        async with transaction() as s:
            result = await mitb_eng.create(s, ident, chat.id, chat.title, capacity=to_int(parts[2]), mode=parts[3])
        if not result.ok:
            await cb.answer(result.error, show_alert=True)
            return
        await cb.answer("💼 Money in the Bank lobby opened!")
        await mitb_mgr.publish_lobby(bot, result.event)
        await _show_home(cb)
    else:
        await cb.answer("Invalid or outdated button.", show_alert=True)


async def _send_board(bot: Bot, chat_id: int, which: str) -> None:
    async with transaction() as s:
        if which == "r":
            text = rt.leaderboard_text(await rumble_eng.rumble_leaderboard(s))
        elif which == "t":
            text = tt.leaderboard_text(await tag_eng.tag_leaderboard(s))
        elif which == "m":
            text = mt.leaderboard_text(await mitb_eng.mitb_leaderboard(s))
        else:
            rows, pages = await stats.leaderboard_page(s, 0)
            text = texts.leaderboard_text(rows, 0, stats.PAGE_SIZE, pages)
    await bot.send_message(chat_id, text)
