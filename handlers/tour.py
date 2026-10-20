"""Tournament buttons, the owner's admin panel and the reply-prompts (name / IST times / announcements)."""
from __future__ import annotations

import time

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ForceReply, Message

from config import get_settings
from database import transaction
from handlers.common import edit_callback, identity_from, to_int
from keyboards import tour as tk
from services import tour_engine as eng
from services import tour_manager as mgr
from services import tour_rules as tr
from services import tour_text as tt
from services.tg import safe_delete, safe_send
from services.turn_ping import mention

router = Router(name="tour")

PROMPT_TTL = 1800                                   # seconds an owner has to answer a reply-prompt
_pending: dict[tuple[int, int], dict] = {}          # (chat_id, prompt message id) -> what we are waiting for
_names: dict[tuple[int, int], str] = {}             # (chat_id, owner id) -> tournament name waiting for its size


def _is_owner(user_id: int) -> bool:
    owner = get_settings().owner_id
    return bool(owner) and user_id == owner


def _remember(chat_id: int, message_id: int, **info) -> None:
    now = time.time()
    for key in [k for k, v in _pending.items() if now - v["at"] > PROMPT_TTL]:
        _pending.pop(key, None)
    _pending[(chat_id, message_id)] = {"at": now, **info}


async def _prompt(bot: Bot, chat_id: int, text: str, user_id: int, **info) -> None:
    """Send a force-reply prompt aimed at the owner and remember what the answer is for."""
    sent = await bot.send_message(chat_id, f"{mention(user_id, 'Owner')} — {text}",
                                  reply_markup=ForceReply(selective=True, input_field_placeholder="Reply here"))
    _remember(chat_id, sent.message_id, user_id=user_id, **info)


async def _snap(tid: int):
    async with transaction() as s:
        return await eng.get_snapshot(s, tid)


async def _panel(cb: CallbackQuery, t, text: str | None = None, markup=None) -> None:
    await edit_callback(cb, text or tt.admin_text(t), markup or tk.admin_kb(t))


async def _apply(bot: Bot, res: eng.Result) -> None:
    if res.t is not None and res.events:
        await mgr.handle_events(bot, res.t, res.events)


# ---------------------------------------------------------------------------- command

@router.message(Command("tournament", "tour"))
async def cmd_tournament(message: Message) -> None:
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("🏆 Tournaments run in groups. Add me to a group and send /tournament there!")
        return
    await message.answer(tt.menu_caption(), reply_markup=tk.menu_kb())


# --------------------------------------------------------------------------- callbacks

@router.callback_query(F.data.startswith("tn:"))
async def cb_tour(cb: CallbackQuery, bot: Bot) -> None:
    parts = (cb.data or "").split(":")
    if cb.message is None or len(parts) < 2:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    action, chat, user = parts[1], cb.message.chat, cb.from_user
    in_group = chat.type in ("group", "supergroup")

    # ------------------------------------------------------------- menu (group only)
    if action in ("solo", "team", "hist", "stats", "lead", "mk", "mkx"):
        if not in_group:
            await cb.answer("Open /play in a group.", show_alert=True)
            return
        await _menu_action(cb, bot, action, parts)
        return

    # ------------------------------------------------------------------ registration
    if action in ("j", "l", "a") and len(parts) == 3 and to_int(parts[2]) is not None:
        await _registration_action(cb, bot, action, to_int(parts[2]))
        return

    # ------------------------------------------------- fighters: my match, standings, reschedule
    if action in ("mm", "sd") and len(parts) == 3 and to_int(parts[2]) is not None:
        await (_my_match if action == "mm" else _standings)(cb, bot, to_int(parts[2]))
        return
    if action in ("rq", "rw", "ry", "rn") and len(parts) >= 3 and all(to_int(x) is not None for x in parts[2:]):
        await _reschedule_action(cb, bot, action, [to_int(x) for x in parts[2:]])
        return

    # --------------------------------------------------------------------- owner only
    if not _is_owner(user.id):
        await cb.answer("🔒 Only the tournament owner can use this.", show_alert=True)
        return
    if action == "ad" and len(parts) >= 4 and to_int(parts[2]) is not None:
        await _admin_action(cb, bot, to_int(parts[2]), parts[3], parts[4:])
    elif action in ("fx", "ft", "fw") and len(parts) >= 4 and to_int(parts[2]) is not None and to_int(parts[3]) is not None:
        await _fixture_action(cb, bot, action, to_int(parts[2]), to_int(parts[3]), parts[4:])
    else:
        await cb.answer("Invalid or outdated button.", show_alert=True)


_last_standings: dict[int, float] = {}                # tournament id -> last time the picture was requested


async def _my_match(cb: CallbackQuery, bot: Bot, tid: int) -> None:
    t = await _snap(tid)
    uid = cb.from_user.id
    if t is None or t.status != tr.ACTIVE:
        await cb.answer("This tournament is not running.", show_alert=True)
        return
    p = t.player(uid)
    if p is None or p.status == tr.P_WAITLIST:
        await cb.answer("You are not playing in this tournament.", show_alert=True)
        return
    mine = t.my_fixtures(uid)
    sent = await safe_send(bot, uid, tt.my_match_text(t, uid), reply_markup=tk.my_match_kb(t, mine[0] if mine else None, uid))
    if sent.ok:
        await cb.answer("📩 Your match card is in your DM.")
    else:
        await cb.answer(tt.my_match_alert(t, uid), show_alert=True)


async def _standings(cb: CallbackQuery, bot: Bot, tid: int) -> None:
    t = await _snap(tid)
    if t is None or t.format != tr.LEAGUE or t.status not in tr.OPEN_STATES:
        await cb.answer("No league table to show.", show_alert=True)
        return
    now = time.time()
    if now - _last_standings.get(tid, 0) < 20:
        await cb.answer("📊 Just posted — scroll up a little.", show_alert=True)
        return
    _last_standings[tid] = now
    await cb.answer("📊 Making the table…")
    await mgr.publish_standings(bot, t, mgr.standings_subtitle(t), chat_id=cb.message.chat.id)


async def _reschedule_action(cb: CallbackQuery, bot: Bot, action: str, ids: list[int]) -> None:
    uid = cb.from_user.id
    if action == "rq" and len(ids) == 2:
        t = await _snap(ids[0])
        f = t.fixture(ids[1]) if t else None
        if t is None or f is None or t.status != tr.ACTIVE or f.status != tr.SCHEDULED or uid not in (f.p1_id, f.p2_id):
            await cb.answer("This match can't be moved.", show_alert=True)
            return
        if t.pending_reschedule(f.id) is not None:
            await cb.answer("A request is already waiting for an answer.", show_alert=True)
            return
        await cb.answer()
        sent = await bot.send_message(cb.message.chat.id, tt.reschedule_prompt(t, f, uid),
                                      reply_markup=ForceReply(selective=True, input_field_placeholder="e.g. 9:30pm"))
        _remember(cb.message.chat.id, sent.message_id, user_id=uid, kind="rs", tid=t.id, fid=f.id)
        return
    if len(ids) != 1:
        await cb.answer("Invalid or outdated button.", show_alert=True)
        return
    async with transaction() as s:
        if action == "rw":
            res = await eng.withdraw_reschedule(s, ids[0], uid)
        else:
            res = await eng.answer_reschedule(s, ids[0], uid, accept=action == "ry")
    if res.events:
        await _apply(bot, res)
    if not res.ok:
        await cb.answer(res.error, show_alert=True)
        return
    await cb.answer("Done.")
    await edit_callback(cb, tt.reschedule_closed_text(res.events[-1].note if res.events else ""))


async def _menu_action(cb: CallbackQuery, bot: Bot, action: str, parts: list[str]) -> None:
    chat, user = cb.message.chat, cb.from_user
    if action == "team":
        await cb.answer("👥 Team Mode is coming soon!", show_alert=True)
    elif action == "hist":
        async with transaction() as s:
            rows = await eng.history(s, chat.id)
        await cb.answer()
        await safe_send(bot, chat.id, tt.history_text(rows))
    elif action == "lead":
        async with transaction() as s:
            rows = await eng.leaderboard(s)
        await cb.answer()
        await safe_send(bot, chat.id, tt.leaderboard_text(rows))
    elif action == "stats":
        async with transaction() as s:
            data = await eng.user_stats(s, user.id)
        await cb.answer()
        await safe_send(bot, chat.id, tt.stats_text(identity_from(user).name, data))
    elif action == "solo":
        async with transaction() as s:
            open_t = await eng.open_snapshot_for_chat(s, chat.id)
        if open_t is not None:
            await cb.answer(f"🏆 \"{open_t.title}\" is already open in this group — use its card to join.", show_alert=True)
            return
        if not _is_owner(user.id):
            await cb.answer("🔒 Only the bot owner can create a tournament. Watch for the registration card!", show_alert=True)
            return
        await cb.answer()
        await _prompt(bot, chat.id, tt.name_prompt(), user.id, kind="name")
    elif action == "mkx":
        if _is_owner(user.id):
            _names.pop((chat.id, user.id), None)
            await safe_delete(bot, chat.id, cb.message.message_id)
        await cb.answer()
    elif action == "mk" and len(parts) == 3:
        if not _is_owner(user.id):
            await cb.answer("🔒 Only the owner can pick this.", show_alert=True)
            return
        name = _names.pop((chat.id, user.id), None)
        size = to_int(parts[2])
        if name is None or size is None:
            await cb.answer("That prompt expired — press Solo Mode again.", show_alert=True)
            return
        async with transaction() as s:
            res = await eng.create(s, identity_from(user), chat.id, chat.title, name, size)
        if not res.ok:
            await cb.answer(res.error, show_alert=True)
            return
        await cb.answer("🏆 Tournament created!")
        await safe_delete(bot, chat.id, cb.message.message_id)
        await mgr.publish_registration(bot, res.t)
        sent = await mgr.send_admin_panel(bot, res.t, user.id)
        if not sent:                                          # owner never opened the bot privately
            await safe_send(bot, chat.id, "👑 Owner: open the bot privately and press Start to get the admin panel, "
                            "or tap <b>Admin panel</b> on the registration card.")


async def _registration_action(cb: CallbackQuery, bot: Bot, action: str, tid: int) -> None:
    user = cb.from_user
    if action == "a":
        if not _is_owner(user.id):
            await cb.answer("🔒 Only the tournament owner can open the admin panel.", show_alert=True)
            return
        t = await _snap(tid)
        if t is None or t.status not in tr.OPEN_STATES:
            await cb.answer("This tournament is no longer running.", show_alert=True)
            return
        if await mgr.send_admin_panel(bot, t, user.id):
            await cb.answer("👑 Admin panel sent to your DM.")
        else:
            await cb.answer("Open the bot privately and press Start first.", show_alert=True)
        return
    async with transaction() as s:
        res = (await eng.join(s, tid, identity_from(user)) if action == "j"
               else await eng.leave(s, tid, user.id))
    if not res.ok:
        if res.error == "dm":
            await cb.answer("📩 Open the bot privately, press Start, then tap Join again.", show_alert=True)
        else:
            await cb.answer(res.error, show_alert=True)
        return
    await cb.answer({"registered": "✅ You're in!", "waitlist": "⏳ Added to the waiting list.",
                     "left": "👋 You left the tournament."}.get(res.kind, "Done."))
    await mgr.refresh_registration(bot, res.t)


async def _admin_action(cb: CallbackQuery, bot: Bot, tid: int, act: str, args: list[str]) -> None:
    uid = cb.from_user.id
    chat = cb.message.chat
    t = await _snap(tid)
    if t is None or t.status not in tr.OPEN_STATES:
        await cb.answer("This tournament is no longer running.", show_alert=True)
        return

    async def run(coro_factory, ok_text: str = ""):
        async with transaction() as s:
            res = await coro_factory(s)
        if not res.ok:
            await cb.answer(res.error, show_alert=True)
            return None
        await cb.answer(ok_text)
        await _apply(bot, res)
        return res

    if act == "home":
        await cb.answer()
        await _panel(cb, t)
    elif act in ("reg", "st"):
        await cb.answer()
        what = "Registration closes at" if act == "reg" else "First match starts at"
        await _prompt(bot, chat.id, tt.time_prompt(what), uid, kind=act, tid=tid)
    elif act == "mx" and args and to_int(args[0]) is not None:
        res = await run(lambda s: eng.set_max_players(s, tid, uid, to_int(args[0])), "👥 Updated.")
        if res:
            await _panel(cb, res.t)
            await mgr.refresh_registration(bot, res.t)
    elif act == "go":
        res = await run(lambda s: eng.start(s, tid, uid), "🚀 Tournament started!")
        if res:
            await _panel(cb, res.t)
    elif act == "dq":
        await cb.answer()
        await _panel(cb, t, tt.pick_player_text("Kick / disqualify"), tk.players_kb(t, "kk"))
    elif act == "kk" and args and to_int(args[0]) is not None:
        res = await run(lambda s: eng.disqualify(s, tid, uid, to_int(args[0])), "🚫 Done.")
        if res:
            await _panel(cb, res.t)
    elif act == "fx":
        await cb.answer()
        await _panel(cb, t, tt.fixtures_admin_text(t), tk.fixtures_kb(t))
    elif act in ("pause", "resume"):
        res = await run(lambda s: eng.set_paused(s, tid, uid, act == "pause"), "⏸️ Paused." if act == "pause" else "▶️ Resumed.")
        if res:
            await _panel(cb, res.t)
    elif act == "gaps":
        await cb.answer()
        await _panel(cb, t, tt.gaps_text(t), tk.gaps_kb(t))
    elif act in ("rg", "mg", "ns") and args and to_int(args[0]) is not None:
        key = {"rg": "round_gap", "mg": "match_gap", "ns": "noshow"}[act]
        res = await run(lambda s: eng.set_gaps(s, tid, uid, **{key: to_int(args[0])}), "⏱️ Saved.")
        if res:
            await _panel(cb, res.t, tt.gaps_text(res.t), tk.gaps_kb(res.t))
    elif act == "redraw":
        res = await run(lambda s: eng.redraw(s, tid, uid), "🎲 Bracket redrawn.")
        if res:
            await _panel(cb, res.t)
    elif act == "ann":
        await cb.answer()
        await _prompt(bot, chat.id, tt.announce_prompt(), uid, kind="ann", tid=tid)
    elif act == "bk":
        await cb.answer("💾 Sending backup…")
        await mgr.send_backup(bot, t)
    elif act == "cancel":
        await cb.answer()
        await _panel(cb, t, f"🗑️ Cancel <b>{tt.esc(t.title)}</b>? Running matches will be stopped.", tk.confirm_cancel_kb(t))
    elif act == "cancel_yes":
        res = await run(lambda s: eng.cancel(s, tid, uid), "🛑 Cancelled.")
        if res:
            await edit_callback(cb, f"🛑 <b>{tt.esc(t.title)}</b> was cancelled.")
    else:
        await cb.answer("Invalid or outdated button.", show_alert=True)


async def _fixture_action(cb: CallbackQuery, bot: Bot, action: str, tid: int, fid: int, args: list[str]) -> None:
    uid = cb.from_user.id
    t = await _snap(tid)
    f = t.fixture(fid) if t else None
    if t is None or f is None or t.status != tr.ACTIVE:
        await cb.answer("That fixture is no longer available.", show_alert=True)
        return
    if action == "fx":
        await cb.answer()
        await edit_callback(cb, tt.fixture_admin_text(t, f), tk.fixture_kb(t, f))
    elif action == "ft":
        await cb.answer()
        await _prompt(bot, cb.message.chat.id, tt.time_prompt("New match time"), uid, kind="ft", tid=tid, fid=fid)
    elif action == "fw" and args and to_int(args[0]) is not None:
        async with transaction() as s:
            res = await eng.set_fixture_winner(s, tid, uid, fid, to_int(args[0]))
        if not res.ok:
            await cb.answer(res.error, show_alert=True)
            return
        await cb.answer("🏁 Result set.")
        await _apply(bot, res)
        await edit_callback(cb, tt.fixtures_admin_text(res.t), tk.fixtures_kb(res.t))


# ------------------------------------------------------------------ reply to a prompt

def _is_answer(message: Message) -> bool:
    reply = message.reply_to_message
    if reply is None or message.from_user is None or not message.text:
        return False
    info = _pending.get((message.chat.id, reply.message_id))
    return info is not None and info["user_id"] == message.from_user.id


@router.message(_is_answer)
async def on_prompt_answer(message: Message, bot: Bot) -> None:
    from models import utcnow
    key = (message.chat.id, message.reply_to_message.message_id)
    info = _pending[key]
    uid, text, kind = message.from_user.id, message.text.strip(), info["kind"]
    now = utcnow()

    if kind == "name":
        name = tr.clean_name(text)
        if name is None:
            await message.reply("❌ The name needs at least 2 characters. Reply again.")
            return
        _pending.pop(key, None)
        _names[(message.chat.id, uid)] = name
        await bot.send_message(message.chat.id, tt.size_prompt(name), reply_markup=tk.size_kb())
        return

    tid = info["tid"]
    if kind == "rs":
        when = tr.parse_ist(text, now)
        if when is None:
            await message.reply(tt.bad_time())
            return
        async with transaction() as s:
            res = await eng.request_reschedule(s, tid, uid, info["fid"], when, now)
        if not res.ok:
            await message.reply(res.error)
            return
        _pending.pop(key, None)
        rr = res.t.pending_reschedule(info["fid"])
        await message.reply(tt.reschedule_sent_text(res.t, res.t.fixture(info["fid"]), rr),
                            reply_markup=tk.my_match_kb(res.t, res.t.fixture(info["fid"]), uid))
        await _apply(bot, res)
        return
    if kind == "ann":
        _pending.pop(key, None)
        t = await _snap(tid)
        if t is not None and t.status in tr.OPEN_STATES:
            await mgr.announce(bot, t, text[:600])
            await message.reply("📣 Posted.")
        return

    when = tr.parse_ist(text, now)
    if when is None:
        await message.reply(tt.bad_time())                     # keep waiting: the owner can reply again
        return
    async with transaction() as s:
        if kind == "reg":
            res = await eng.set_reg_close(s, tid, uid, when, now)
        elif kind == "st":
            res = await eng.set_start_time(s, tid, uid, when, now)
        elif kind == "ft":
            res = await eng.set_fixture_time(s, tid, uid, info["fid"], when, now)
        else:
            return
    if not res.ok:
        await message.reply(res.error)
        return
    _pending.pop(key, None)
    await message.reply(f"✅ Saved: <b>{tr.fmt_ist(when)}</b>")
    await mgr.refresh_registration(bot, res.t)
    await _apply(bot, res)
    await mgr.send_admin_panel(bot, res.t, message.chat.id)
