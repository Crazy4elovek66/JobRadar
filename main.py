from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from src.bot import build_dispatcher
from src.config import Settings, load_settings
from src.database import Database
from src.db_migrations import migrate_database
from src.apply_service import ApplyService
from src.hh_client import HHClient
from src.oauth_server import create_web_app
from src.scheduler import create_scheduler
from src.search_service import SearchService
from aiohttp import web


logger = logging.getLogger(__name__)


def warn_if_local_oauth_route_is_cloud(settings: Settings) -> None:
    app_base_host = (urlparse(settings.app_base_url).hostname or "").lower()
    web_host = settings.web_server_host.strip().lower()
    local_web_hosts = {"", "0.0.0.0", "::", "localhost", "127.0.0.1", "::1"}
    cloud_suffixes = (
        ".vercel.app",
        ".netlify.app",
        ".render.com",
        ".fly.dev",
        ".railway.app",
    )
    is_cloud_app_base = any(app_base_host == suffix.lstrip(".") or app_base_host.endswith(suffix) for suffix in cloud_suffixes)
    is_local_web_server = web_host in local_web_hosts

    if is_local_web_server and is_cloud_app_base:
        logger.warning(
            "ВАЖНО: локальный aiohttp-сервер запущен на %s:%s, но APP_BASE_URL=%s указывает на облачный хост. "
            "OAuth-авторизация HH не дойдет до локального /auth/hh/callback без ngrok или обратного прокси. "
            "Для локальной разработки укажите публичный адрес туннеля в APP_BASE_URL и HH_REDIRECT_URI либо используйте "
            "http://127.0.0.1:%s и такой же адрес возврата в dev.hh.ru.",
            settings.web_server_host,
            settings.web_server_port,
            settings.app_base_url,
            settings.web_server_port,
        )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    warn_if_local_oauth_route_is_cloud(settings)
    db = Database(settings.database_path)
    db.init()
    migrate_database(db)

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
