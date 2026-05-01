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

    scheduler.add_job(
        scheduled_search,
        trigger="interval",
        minutes=settings.search_interval_minutes,
        id="hh_search",
        replace_existing=True,
        max_instances=1,
    )
    return scheduler
