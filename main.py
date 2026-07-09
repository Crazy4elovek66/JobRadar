from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from src.bot import build_dispatcher
from src.config import Settings, load_settings
from src.database import Database
from src.apply_service import ApplyService
from src.hh_client import HHApiError, HHClient
from src.oauth_server import create_web_app
from src.scheduler import create_scheduler
from src.search_service import SearchService
from aiohttp import web


logger = logging.getLogger(__name__)


HH_COOKIE_REFRESH_TEXT = (
    "Сессия HH не работает.\n\n"
    "Как обновить:\n"
    "1. Открой hh.ru в браузере и войди в нужный аккаунт.\n"
    "2. Открой страницу https://hh.ru/applicant/resumes.\n"
    "3. В DevTools → Network выбери запрос к /applicant/resumes.\n"
    "4. Скопируй весь заголовок Cookie из Request Headers.\n"
    "5. Вставь его в .env в строку HH_SESSION_COOKIE=...\n"
    "6. Перезапусти JobRadar и нажми в боте «🔗 HH подключение» → «Проверить cookie HH».\n\n"
    "Пока cookie не обновлены, поиск продолжит работать по публичным страницам, но JobRadar не увидит внешние отклики и резюме."
)


def warn_if_hh_user_agent_is_placeholder(settings: Settings) -> None:
    user_agent = settings.hh_user_agent.strip().lower()
    if not user_agent:
        logger.warning(
            "HH_USER_AGENT выглядит пустым или служебным: %s. Для HTML-режима лучше указать обычный браузерный User-Agent.",
            settings.hh_user_agent,
        )


def warn_if_hh_cookie_missing(settings: Settings) -> None:
    if not settings.hh_session_cookie:
        logger.warning("HH_SESSION_COOKIE не задан. Поиск вакансий может работать, но резюме и личные страницы HH будут недоступны.")


async def check_hh_session_on_start(settings: Settings, db: Database, hh_client: HHClient, bot: Bot) -> None:
    if not settings.hh_session_cookie:
        db.mark_hh_connected(settings.telegram_user_id, False)
        logger.warning("%s", HH_COOKIE_REFRESH_TEXT)
        await notify_user_about_hh_session(bot, settings.telegram_user_id, HH_COOKIE_REFRESH_TEXT)
        return

    try:
        await hh_client.get_me(settings.telegram_user_id)
    except HHApiError as exc:
        db.mark_hh_connected(settings.telegram_user_id, False)
        logger.warning(
            "HH session check failed on startup: status=%s type=%s value=%s. %s",
            exc.status,
            exc.error_type,
            exc.error_value,
            HH_COOKIE_REFRESH_TEXT,
        )
        await notify_user_about_hh_session(bot, settings.telegram_user_id, HH_COOKIE_REFRESH_TEXT)
        return
    except Exception:
        db.mark_hh_connected(settings.telegram_user_id, False)
        logger.exception("HH session check failed on startup with unexpected error")
        await notify_user_about_hh_session(bot, settings.telegram_user_id, HH_COOKIE_REFRESH_TEXT)
        return

    db.mark_hh_connected(settings.telegram_user_id, True)
    logger.info("HH session check passed on startup: cookie accepted")


async def notify_user_about_hh_session(bot: Bot, telegram_user_id: int, text: str) -> None:
    try:
        await bot.send_message(telegram_user_id, text)
    except Exception:
        logger.exception("Failed to send HH session warning to Telegram")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    warn_if_hh_user_agent_is_placeholder(settings)
    warn_if_hh_cookie_missing(settings)
    db = Database(settings.database_path)
    db.init()

    if settings.extension_only:
        logger.info("Запуск JobRadar в автономном режиме расширения (EXTENSION_ONLY)")
        web_app = create_web_app(settings=settings, db=db, hh_client=None, bot=None, search_service=None)
        web_runner = web.AppRunner(web_app)
        try:
            await web_runner.setup()
            web_site = web.TCPSite(web_runner, settings.web_server_host, settings.web_server_port)
            await web_site.start()
            logger.info("Веб-сервер JobRadar запущен на %s:%s", settings.web_server_host, settings.web_server_port)
            # Держим цикл событий активным
            await asyncio.Event().wait()
        finally:
            await web_runner.cleanup()
        return

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
            logger.info("Связь с HTML-страницами HH успешно установлена")
        else:
            logger.warning("Проверка поиска вакансий HH не прошла. Смотри строку HH HTML ping failed выше: там есть status/type/value, маршрут и рекомендация.")

        await check_hh_session_on_start(settings=settings, db=db, hh_client=hh_client, bot=bot)

        logger.info("JobRadar started")
        await dispatcher.start_polling(bot)
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await web_runner.cleanup()
        if hh_client:
            await hh_client.close()


if __name__ == "__main__":
    asyncio.run(main())
