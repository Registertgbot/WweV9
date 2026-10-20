"""WWE Arena — entry point."""
from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, ErrorEvent

import database
from handlers.access import GroupGate
from services import access, titles
from config import load_settings, set_settings
from handlers import routers
from services import match_engine, media_manager, mitb_engine, mitb_manager, rumble_engine, rumble_manager, tag_engine, tag_manager, tour_manager, turn_manager

log = logging.getLogger("wwe_arena")

COMMANDS = [                      # the "/" menu stays short; /match /rumble /tag /mitb ... still work when typed
    BotCommand(command="play", description="Open the arena menu (all game modes)"),
    BotCommand(command="start", description="Welcome screen"),
    BotCommand(command="help", description="Rules and controls"),
    BotCommand(command="profile", description="Your superstar profile"),
    BotCommand(command="leaderboard", description="Champions board"),
]


async def on_error(event: ErrorEvent) -> bool:
    log.exception("Unhandled error while processing update", exc_info=event.exception)
    update = event.update
    with contextlib.suppress(Exception):
        if update.callback_query:
            await update.callback_query.answer("⚠️ Something went wrong. Please try again.", show_alert=True)
        elif update.message:
            await update.message.answer("⚠️ Something went wrong. Please try again.")
    return True


async def main() -> None:
    settings = load_settings(require_token=True)
    set_settings(settings)
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)   # never log SQL parameters/secrets
    database.init_engine()

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.message.outer_middleware(GroupGate())
    dp.callback_query.outer_middleware(GroupGate())
    for router in routers:
        dp.include_router(router)
    dp.errors.register(on_error)

    async with database.transaction() as s:
        await media_manager.ensure_assets(s)
        await access.load(s)
        recovered = await match_engine.recover_deadlines(s)
        recovered += await rumble_engine.recover_deadlines(s)
        recovered += await tag_engine.recover_deadlines(s)
        recovered += await mitb_engine.recover_deadlines(s)
    if recovered:
        log.info("Gave a restart grace period to %d match deadline(s)", recovered)
    missing = media_manager.missing_assets()
    if missing:
        log.warning("GIFs missing (text fallback will be used): %s", ", ".join(missing))

    me = await bot.get_me()
    await bot.set_my_commands(COMMANDS)
    log.info("Starting @%s", me.username)

    loop_task = asyncio.create_task(turn_manager.deadline_loop(bot), name="deadline-loop")
    rumble_task = asyncio.create_task(rumble_manager.deadline_loop(bot), name="rumble-loop")
    tag_task = asyncio.create_task(tag_manager.deadline_loop(bot), name="tag-loop")
    mitb_task = asyncio.create_task(mitb_manager.deadline_loop(bot), name="mitb-loop")
    tour_task = asyncio.create_task(tour_manager.deadline_loop(bot), name="tour-loop")
    title_task = asyncio.create_task(titles.deadline_loop(bot), name="title-loop")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        loop_task.cancel()
        rumble_task.cancel()
        tag_task.cancel()
        mitb_task.cancel()
        tour_task.cancel()
        title_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task
        with contextlib.suppress(asyncio.CancelledError):
            await rumble_task
        with contextlib.suppress(asyncio.CancelledError):
            await tag_task
        with contextlib.suppress(asyncio.CancelledError):
            await mitb_task
        with contextlib.suppress(asyncio.CancelledError):
            await tour_task
        with contextlib.suppress(asyncio.CancelledError):
            await title_task
        await bot.session.close()
        await database.dispose_engine()
        log.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
