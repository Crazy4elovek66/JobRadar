from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from src.bot import build_dispatcher
from src.config import load_settings
from src.database import Database
from src.hh_client import HHClient
from src.scheduler import create_scheduler
from src.search_service import SearchService


logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    db = Database(settings.database_path)
    db.init()

    # Телеграм идет через локальный прокси Happ (Финляндия), чтобы обойти блокировку РКН.
    session = AiohttpSession(proxy="http://127.0.0.1:10809")
    bot = Bot(token=settings.telegram_bot_token, session=session)
    hh_client = HHClient(area=settings.hh_area, proxy=settings.hh_proxy)
    await hh_client.start()

    search_service = SearchService(db=db, hh_client=hh_client, min_score_to_send=settings.min_score_to_send)
    dispatcher = build_dispatcher(settings=settings, db=db, search_service=search_service)
    scheduler = create_scheduler(bot=bot, settings=settings, search_service=search_service)

    try:
        scheduler.start()

        if await hh_client.ping():
            logger.info("Связь с HH API успешно установлена")
        else:
            logger.warning("Не удалось подключиться к HH API. Проверьте настройки прокси или сеть!")

        logger.info("JobRadar started")
        await dispatcher.start_polling(bot)
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await hh_client.close()


if __name__ == "__main__":
    asyncio.run(main())
