from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from src.bot import build_dispatcher
from src.config import load_settings
from src.database import Database
from src.apply_service import ApplyService
from src.hh_client import HHClient
from src.oauth_server import create_web_app
from src.scheduler import create_scheduler
from src.search_service import SearchService
from aiohttp import web


logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    db = Database(settings.database_path)
    db.init()

    session = AiohttpSession(proxy=settings.telegram_proxy)
    bot = Bot(token=settings.telegram_bot_token, session=session)
    hh_client = HHClient(settings=settings, db=db, proxies=settings.hh_proxies)
    await hh_client.start()

    apply_service = ApplyService(db=db, hh_client=hh_client)
    search_service = SearchService(db=db, hh_client=hh_client, apply_service=apply_service, min_score_to_send=settings.min_score_to_send)
    dispatcher = build_dispatcher(settings=settings, db=db, hh_client=hh_client, apply_service=apply_service, search_service=search_service)
    scheduler = create_scheduler(bot=bot, settings=settings, search_service=search_service)
    web_app = create_web_app(settings=settings, db=db, hh_client=hh_client, bot=bot, search_service=search_service)
    web_runner = web.AppRunner(web_app)

    try:
        await web_runner.setup()
        web_site = web.TCPSite(web_runner, settings.web_server_host, settings.web_server_port)
        await web_site.start()
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
        await web_runner.cleanup()
        await hh_client.close()


if __name__ == "__main__":
    asyncio.run(main())
