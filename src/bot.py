from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup

from src.config import Settings
from src.database import Database
from src.search_service import SearchService
from src.scoring import SEARCH_QUERIES
from src.utils import escape_html


logger = logging.getLogger(__name__)
router = Router()
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🔎 Найти вакансии")]],
    resize_keyboard=True,
    is_persistent=True,
)


def build_dispatcher(settings: Settings, db: Database, search_service: SearchService) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher["settings"] = settings
    dispatcher["db"] = db
    dispatcher["search_service"] = search_service
    dispatcher.include_router(router)
    return dispatcher


def is_allowed_user(message: Message, settings: Settings) -> bool:
    return bool(message.from_user and message.from_user.id == settings.telegram_user_id)


@router.message(Command("start"))
async def start_handler(message: Message, settings: Settings) -> None:
    if not is_allowed_user(message, settings):
        await message.answer("Этот JobRadar настроен для одного пользователя.", parse_mode="HTML")
        return
    await message.answer(
        "JobRadar включён.\n\n"
        "Я ищу вакансии на HH.ru, отсекаю колл-центры и продажи, оцениваю IT-релевантность "
        "и присылаю только те варианты, которые могут помочь перейти в техническую среду.\n\n"
        "Команда /search запускает поиск вручную.",
        parse_mode="HTML",
        reply_markup=main_keyboard,
    )


@router.message(Command("help"))
async def help_handler(message: Message, settings: Settings) -> None:
    if not is_allowed_user(message, settings):
        return
    await message.answer(
        "Команды JobRadar:\n\n"
        "/search — найти свежие подходящие вакансии\n"
        "/stats — показать статистику базы\n"
        "/settings — показать настройки поиска\n"
        "/help — открыть эту подсказку\n\n"
        "Я не присылаю повторно вакансии, которые уже были отправлены или отмечены как неподходящие.",
        parse_mode="HTML",
    )


@router.message(Command("search"))
async def search_handler(message: Message, settings: Settings, search_service: SearchService, bot: Bot) -> None:
    if not is_allowed_user(message, settings):
        await message.answer("Этот JobRadar настроен для одного пользователя.", parse_mode="HTML")
        return
    if search_service.is_running:
        await message.answer(
            "Поиск уже выполняется. Пожалуйста, подожди завершения.",
            parse_mode="HTML",
        )
        return
    await message.answer(
        "Запускаю поиск по HH.ru. Процесс детального анализа занимает около 5–10 минут. "
        "Я пришлю результаты по готовности.",
        parse_mode="HTML",
    )
    try:
        summary = await search_service.run(bot, settings.telegram_user_id)
    except Exception:
        logger.exception("Manual search failed")
        await message.answer(
            "Поиск сейчас не получился: HH.ru или сеть временно недоступны. Попробуй позже.",
            parse_mode="HTML",
        )
        return
    if summary is None:
        await message.answer(
            "Поиск уже выполняется. Пожалуйста, подожди завершения.",
            parse_mode="HTML",
        )
        return
    await message.answer(
        "Поиск завершён.\n\n"
        f"Найдено на HH.ru: {summary.found}\n"
        f"Сохранено в базу: {summary.saved}\n"
        f"Отсеяно как мусор: {summary.rejected}\n"
        f"Отправлено новых вакансий: {summary.sent}",
        parse_mode="HTML",
    )


@router.message(F.text == "🔎 Найти вакансии")
async def search_button_handler(message: Message, settings: Settings, search_service: SearchService, bot: Bot) -> None:
    await search_handler(message, settings, search_service, bot)


@router.message(Command("stats"))
async def stats_handler(message: Message, settings: Settings, db: Database) -> None:
    if not is_allowed_user(message, settings):
        return
    stats = db.stats()
    await message.answer(
        "Статистика JobRadar:\n\n"
        f"Всего вакансий в базе: {stats['total']}\n"
        f"Горячие: {stats['HOT']}\n"
        f"Хорошие: {stats['GOOD']}\n"
        f"Возможно: {stats['MAYBE']}\n"
        f"Отклонены: {stats['REJECT']}\n"
        f"Отправлено пользователю: {stats['sent']}",
        parse_mode="HTML",
    )


@router.message(Command("settings"))
async def settings_handler(message: Message, settings: Settings) -> None:
    if not is_allowed_user(message, settings):
        return
    queries = "\n".join(f"• {escape_html(query)}" for query in SEARCH_QUERIES)
    await message.answer(
        "Текущие настройки поиска:\n\n"
        f"Регион HH: {escape_html(settings.hh_area)}\n"
        f"Интервал автопоиска: {settings.search_interval_minutes} мин.\n"
        f"Минимальный балл для отправки: {settings.min_score_to_send}/100\n\n"
        f"Поисковые запросы:\n{queries}",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("favorite:"))
async def favorite_handler(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not callback.from_user or callback.from_user.id != settings.telegram_user_id:
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    vacancy_id = int(callback.data.split(":", 1)[1])
    db.set_favorite(vacancy_id, True)
    await callback.answer("Сохранил в избранное.")


@router.callback_query(F.data.startswith("reject:"))
async def reject_handler(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not callback.from_user or callback.from_user.id != settings.telegram_user_id:
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    vacancy_id = int(callback.data.split(":", 1)[1])
    db.reject_by_user(vacancy_id)
    if callback.message:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            logger.warning("Rejected vacancy message is already unavailable for deletion: %s", vacancy_id)
    await callback.answer("Больше не буду присылать эту вакансию.")
