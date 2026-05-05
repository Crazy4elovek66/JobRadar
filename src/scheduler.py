from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config import Settings
from src.search_service import SearchService


logger = logging.getLogger(__name__)


def create_scheduler(bot: Bot, settings: Settings, search_service: SearchService) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    async def scheduled_search() -> None:
        logger.info("Scheduled vacancy search started")
        try:
            summary = await search_service.run(bot, settings.telegram_user_id)
        except Exception:
            logger.exception("Scheduled vacancy search failed")
            return
        if summary is None:
            logger.warning("Scheduled search skipped because another search is already running")
            return
        logger.info(
            "Scheduled vacancy search finished: found=%s saved=%s rejected=%s sent=%s",
            summary.found,
            summary.saved,
            summary.rejected,
            summary.sent,
        )
        await process_auto_queue()

    async def process_auto_queue() -> None:
        db = search_service.db
        apply_service = search_service.apply_service
        user_settings = db.ensure_user_settings(settings.telegram_user_id)
        if user_settings["apply_mode"] != "auto" or not user_settings["auto_acknowledged_at"]:
            return
        daily_stats = db.application_stats_today(settings.telegram_user_id)
        remaining = max(0, user_settings["auto_daily_limit"] - daily_stats["auto"])
        limit = min(user_settings["auto_run_limit"], remaining)
        if limit <= 0:
            return
        for item in db.due_queue_items(settings.telegram_user_id, limit=limit):
            result = await apply_service.apply_to_vacancy(
                settings.telegram_user_id,
                item["vacancy_id"],
                item["resume_id"],
                item["cover_letter"],
                mode="auto",
                test_mode=bool(user_settings["test_mode"]),
            )
            db.update_queue_status(item["id"], "sent" if result.ok else "failed")
            await bot.send_message(settings.telegram_user_id, result.message)

    scheduler.add_job(
        scheduled_search,
        trigger="interval",
        minutes=settings.search_interval_minutes,
        id="hh_search",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        process_auto_queue,
        trigger="interval",
        minutes=5,
        id="hh_auto_queue",
        replace_existing=True,
        max_instances=1,
    )
    return scheduler
