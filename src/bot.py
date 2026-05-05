from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.apply_service import ApplyService
from src.config import Settings
from src.database import Database
from src.hh_client import HHApiError, HHClient
from src.keyboards import (
    apply_confirm_keyboard,
    apply_mode_keyboard,
    auto_confirm_keyboard,
    hh_keyboard,
    main_keyboard,
    settings_keyboard,
)
from src.search_service import SearchService
from src.utils import escape_html


logger = logging.getLogger(__name__)
router = Router()
pending_letters: dict[int, str] = {}
prepared_letters: dict[tuple[int, str], str] = {}
pending_settings: dict[int, str] = {}


def build_dispatcher(settings: Settings, db: Database, hh_client: HHClient, apply_service: ApplyService, search_service: SearchService) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher["settings"] = settings
    dispatcher["db"] = db
    dispatcher["hh_client"] = hh_client
    dispatcher["apply_service"] = apply_service
    dispatcher["search_service"] = search_service
    dispatcher.include_router(router)
    return dispatcher


def is_allowed_user(message: Message, settings: Settings) -> bool:
    return bool(message.from_user and message.from_user.id == settings.telegram_user_id)


def is_allowed_callback(callback: CallbackQuery, settings: Settings) -> bool:
    return bool(callback.from_user and callback.from_user.id == settings.telegram_user_id)


@router.message(Command("start"))
async def start_handler(message: Message, settings: Settings, db: Database) -> None:
    if not is_allowed_user(message, settings):
        await message.answer("Этот JobRadar настроен для одного пользователя.", parse_mode="HTML")
        return
    user_settings = db.ensure_user_settings(settings.telegram_user_id)
    status = "подключен" if user_settings["hh_connected"] else "не подключен"
    resume = user_settings["selected_resume_title"] or "не выбрано"
    await message.answer(
        "JobRadar на связи.\n\n"
        "Я ищу IT и около-IT вакансии на HH: QA Manual Junior, L2/support engineer, helpdesk с инженерной частью, CRM, low-code/no-code, automation и junior-роли с API, SQL, багами, логами и интеграциями. "
        "Колл-центры, горячие линии, массовые обзвоны и телефонные продажи отсекаю.\n\n"
        f"Статус HH: <b>{status}</b>\n"
        f"Резюме: <b>{escape_html(resume)}</b>\n\n"
        "Если HH ещё не подключен, начни с раздела «🔗 HH подключение». Если всё готово, запускай поиск.",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


@router.message(Command("help"))
async def help_handler(message: Message, settings: Settings) -> None:
    if not is_allowed_user(message, settings):
        return
    await message.answer(
        "Команды JobRadar:\n\n"
        "/search — ручной поиск вакансий\n"
        "/settings — настройки поиска и откликов\n"
        "/hh — подключение HH и выбор резюме\n"
        "/stats — статистика\n"
        "/applications — журнал откликов\n"
        "/stop_auto — экстренно выключить автоотклики",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


@router.message(Command("search"))
@router.message(F.text == "🔎 Поиск вакансий")
async def search_handler(message: Message, settings: Settings, search_service: SearchService, bot: Bot) -> None:
    if not is_allowed_user(message, settings):
        await message.answer("Этот JobRadar настроен для одного пользователя.", parse_mode="HTML")
        return
    if search_service.is_running:
        await message.answer("Поиск уже выполняется. Подожди завершения.", parse_mode="HTML")
        return
    await message.answer("Запускаю поиск по официальному HH API. Пришлю сильные варианты по готовности.", parse_mode="HTML")
    try:
        summary = await search_service.run(bot, settings.telegram_user_id)
    except Exception:
        logger.exception("Manual search failed")
        await message.answer("Поиск сейчас не получился: HH или сеть временно недоступны. Попробуй позже.", parse_mode="HTML")
        return
    if summary is None:
        await message.answer("Поиск уже выполняется. Подожди завершения.", parse_mode="HTML")
        return
    await message.answer(
        "Поиск завершён.\n\n"
        f"Найдено на HH: {summary.found}\n"
        f"Сохранено в базу: {summary.saved}\n"
        f"Отсеяно: {summary.rejected}\n"
        f"Показано: {summary.sent}\n"
        f"В очереди: {summary.queued}\n"
        f"Автооткликов поставлено в очередь: {summary.auto_applied}",
        parse_mode="HTML",
    )


@router.message(Command("settings"))
@router.message(F.text == "⚙️ Настройки")
async def settings_handler(message: Message, settings: Settings, db: Database) -> None:
    if not is_allowed_user(message, settings):
        return
    user_settings = db.ensure_user_settings(settings.telegram_user_id)
    await message.answer(format_settings(user_settings), parse_mode="HTML", reply_markup=settings_keyboard(bool(user_settings["hh_connected"])))


@router.message(Command("hh"))
@router.message(F.text == "🔗 HH подключение")
async def hh_handler(message: Message, settings: Settings, db: Database) -> None:
    if not is_allowed_user(message, settings):
        return
    user_settings = db.ensure_user_settings(settings.telegram_user_id)
    await message.answer(format_hh_status(user_settings, settings), parse_mode="HTML", reply_markup=hh_keyboard(bool(user_settings["hh_connected"])))


@router.message(Command("stats"))
@router.message(F.text == "📊 Статистика")
async def stats_handler(message: Message, settings: Settings, db: Database) -> None:
    if not is_allowed_user(message, settings):
        return
    user_settings = db.ensure_user_settings(settings.telegram_user_id)
    base = db.stats()
    today = db.application_stats_today(settings.telegram_user_id)
    remaining_auto = max(0, user_settings["auto_daily_limit"] - today["auto"])
    await message.answer(
        "Статистика JobRadar:\n\n"
        f"Найдено сегодня: {today['found_today']}\n"
        f"Показано: {today['shown']}\n"
        f"Скрыто: {today['hidden']}\n"
        f"В очереди: {today['queued']}\n"
        f"Ручных откликов: {today['manual']}\n"
        f"Полуавтооткликов: {today['semi_auto']}\n"
        f"Автооткликов: {today['auto']}\n"
        f"Ошибок: {today['errors']}\n"
        f"Остаток автооткликов на сегодня: {remaining_auto}\n"
        f"Последний запуск поиска: {escape_html(user_settings['last_search_at'] or 'ещё не запускался')}\n"
        f"Текущий режим: {apply_mode_label(user_settings['apply_mode'])}\n\n"
        f"Всего вакансий в базе: {base['total']}",
        parse_mode="HTML",
    )


@router.message(Command("applications"))
@router.message(F.text == "🧾 Мои отклики")
async def applications_handler(message: Message, settings: Settings, db: Database) -> None:
    if not is_allowed_user(message, settings):
        return
    rows = db.recent_applications(settings.telegram_user_id, limit=20)
    queue = db.queue_items(settings.telegram_user_id, "pending", 10)
    if not rows and not queue:
        await message.answer("Журнал откликов пока пуст. Запусти поиск, и я начну сохранять найденные вакансии и действия.", parse_mode="HTML")
        return
    lines = ["Последние действия JobRadar:\n"]
    for row in rows[:20]:
        error = f" · ошибка: {escape_html(row['error_value'])}" if row["error_value"] else ""
        lines.append(
            f"• {escape_html(row['updated_at'])} · {escape_html(row['vacancy_name'] or row['vacancy_id'])} · "
            f"{escape_html(row['employer_name'] or 'компания не указана')} · {status_label(row['status'])} · {apply_mode_label(row['apply_mode'])}{error}"
        )
    if queue:
        lines.append("\nОчередь полуавто/авто:")
        for item in queue[:10]:
            lines.append(f"• #{item['id']} · вакансия {escape_html(item['vacancy_id'])} · скоринг {item['score']}")
        lines.append("\nПолуавто отправит очередь только после финального подтверждения.")
    keyboard = None
    if queue:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Отправить выбранные отклики", callback_data="queue:confirm")],
                [InlineKeyboardButton(text="🧹 Очистить очередь", callback_data="queue:clear")],
            ]
        )
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("stop_auto"))
async def stop_auto_handler(message: Message, settings: Settings, db: Database) -> None:
    if not is_allowed_user(message, settings):
        return
    db.update_user_settings(settings.telegram_user_id, apply_mode="button", auto_acknowledged_at=None)
    await message.answer("Автоотклики выключены. JobRadar вернулся в режим «по кнопке».", parse_mode="HTML")


@router.message(F.text)
async def text_input_handler(message: Message, settings: Settings, db: Database) -> None:
    if not is_allowed_user(message, settings) or not message.from_user:
        return
    vacancy_id = pending_letters.pop(message.from_user.id, None)
    if not vacancy_id:
        setting_key = pending_settings.pop(message.from_user.id, None)
        if setting_key:
            await apply_setting_text(message, db, settings.telegram_user_id, setting_key)
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Письмо получилось пустым. Пришли новый текст одним сообщением.")
        pending_letters[message.from_user.id] = vacancy_id
        return
    prepared_letters[(message.from_user.id, vacancy_id)] = text
    await message.answer(
        f"Письмо обновлено. Проверь перед отправкой:\n\n{escape_html(text)}",
        parse_mode="HTML",
        reply_markup=apply_confirm_keyboard(vacancy_id),
    )


@router.callback_query(F.data == "settings:open")
async def settings_callback(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    user_settings = db.ensure_user_settings(settings.telegram_user_id)
    await callback.message.answer(format_settings(user_settings), parse_mode="HTML", reply_markup=settings_keyboard(bool(user_settings["hh_connected"])))
    await callback.answer()


@router.callback_query(F.data == "settings:apply_mode")
async def apply_mode_callback(callback: CallbackQuery, settings: Settings) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    await callback.message.answer("Выбери режим откликов. Автоотклики включаются только через отдельное подтверждение риска.", reply_markup=apply_mode_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("settings:mode:"))
async def set_apply_mode_callback(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    mode = callback.data.split(":")[-1]
    if mode == "auto_warning":
        await callback.message.answer(
            "Автоотклик будет самостоятельно отправлять отклики на вакансии, которые проходят фильтры и скоринг. "
            "Рекомендуется использовать лимиты. Вакансии с тестами, внешним откликом, капчей и сомнительной релевантностью будут пропускаться.",
            reply_markup=auto_confirm_keyboard(),
        )
    elif mode == "auto_confirm":
        db.update_user_settings(
            settings.telegram_user_id,
            apply_mode="auto",
            require_preview_before_apply=0,
            auto_acknowledged_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        await callback.message.answer("Автоотклики включены с лимитами и журналом действий. Тестовый режим можно оставить включённым для безопасной проверки.")
    else:
        db.update_user_settings(settings.telegram_user_id, apply_mode=mode, auto_acknowledged_at=None if mode != "auto" else datetime.now(timezone.utc).isoformat(timespec="seconds"))
        await callback.message.answer(f"Режим откликов изменён: {apply_mode_label(mode)}.")
    await callback.answer()


@router.callback_query(F.data.startswith("settings:"))
async def simple_settings_callback(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    action = callback.data.split(":", 1)[1]
    if action == "remote":
        row = db.ensure_user_settings(settings.telegram_user_id)
        updated = db.update_user_settings(settings.telegram_user_id, only_remote=0 if row["only_remote"] else 1)
        await callback.message.answer(f"Только удалёнка: {'да' if updated['only_remote'] else 'нет'}.")
    elif action == "test_mode":
        row = db.ensure_user_settings(settings.telegram_user_id)
        updated = db.update_user_settings(settings.telegram_user_id, test_mode=0 if row["test_mode"] else 1)
        await callback.message.answer(f"Тестовый режим: {'включён' if updated['test_mode'] else 'выключен'}.")
    elif action == "stop_auto":
        db.update_user_settings(settings.telegram_user_id, apply_mode="button", auto_acknowledged_at=None)
        await callback.message.answer("Автоотклики экстренно выключены.")
    elif action in {"search", "score", "auto_limits", "letter", "positive", "negative", "areas", "salary", "interval"}:
        pending_settings[settings.telegram_user_id] = action
        await callback.message.answer(setting_prompt(action))
    else:
        await callback.message.answer("Эта настройка меняется через раздел «Настройки». Секреты HH здесь недоступны.")
    await callback.answer()


@router.callback_query(F.data.startswith("hh:"))
async def hh_callback(callback: CallbackQuery, settings: Settings, db: Database, hh_client: HHClient) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    action = callback.data.split(":", 1)[1]
    if action == "connect":
        url = f"{settings.app_base_url}/auth/hh/start?{urlencode({'telegramUserId': settings.telegram_user_id})}"
        await callback.message.answer(f"Открой ссылку для подключения HH:\n{escape_html(url)}", parse_mode="HTML")
    elif action == "check":
        try:
            me = await hh_client.get_me(settings.telegram_user_id)
            await callback.message.answer(f"HH подключен. Тип аккаунта: {escape_html(me.get('user_type') or 'не указан')}.", parse_mode="HTML")
        except Exception as exc:
            await callback.message.answer(f"HH не проверился: {escape_html(str(exc))}", parse_mode="HTML")
    elif action == "disconnect":
        db.delete_hh_tokens(settings.telegram_user_id)
        await callback.message.answer("HH отключен. Токены удалены из локального хранилища.")
    elif action == "resumes":
        await show_resumes(callback, settings, hh_client)
    await callback.answer()


async def show_resumes(callback: CallbackQuery, settings: Settings, hh_client: HHClient) -> None:
    try:
        resumes = await hh_client.get_my_resumes(settings.telegram_user_id)
    except Exception as exc:
        await callback.message.answer(f"Не удалось получить резюме HH: {escape_html(str(exc))}", parse_mode="HTML")
        return
    if not resumes:
        await callback.message.answer("HH не вернул активных резюме. Проверь резюме на HH и попробуй снова.")
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=(resume.get("title") or "Резюме без названия")[:60], callback_data=f"hh_resume:{resume.get('id')}")]
            for resume in resumes[:10]
        ]
    )
    await callback.message.answer("Выбери резюме для откликов:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("hh_resume:"))
async def resume_selected_callback(callback: CallbackQuery, settings: Settings, db: Database, hh_client: HHClient) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    resume_id = callback.data.split(":", 1)[1]
    resumes = await hh_client.get_my_resumes(settings.telegram_user_id)
    resume = next((item for item in resumes if str(item.get("id")) == resume_id), None)
    title = (resume or {}).get("title") or "Резюме HH"
    db.update_user_settings(settings.telegram_user_id, selected_resume_id=resume_id, selected_resume_title=title)
    await callback.message.answer(f"Выбрано резюме: {escape_html(title)}", parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("apply_preview:"))
async def apply_preview_callback(callback: CallbackQuery, settings: Settings, db: Database, apply_service: ApplyService) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    user_settings = db.ensure_user_settings(settings.telegram_user_id)
    if not user_settings["hh_connected"]:
        await callback.message.answer("HH не подключен. Сначала подключи HH в разделе «🔗 HH подключение».")
        await callback.answer()
        return
    if not user_settings["selected_resume_id"]:
        await callback.message.answer("Резюме не выбрано. Открой «🔗 HH подключение» и выбери резюме.")
        await callback.answer()
        return
    vacancy_id = callback.data.split(":", 1)[1]
    try:
        _, letter = await apply_service.prepare_cover_letter(settings.telegram_user_id, vacancy_id)
    except Exception as exc:
        await callback.message.answer(f"Не удалось подготовить отклик: {escape_html(str(exc))}", parse_mode="HTML")
        await callback.answer()
        return
    prepared_letters[(settings.telegram_user_id, vacancy_id)] = letter
    await callback.message.answer(
        f"Предпросмотр сопроводительного письма:\n\n{escape_html(letter)}",
        parse_mode="HTML",
        reply_markup=apply_confirm_keyboard(vacancy_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("letter:"))
async def edit_letter_callback(callback: CallbackQuery, settings: Settings) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    vacancy_id = callback.data.split(":", 1)[1]
    pending_letters[settings.telegram_user_id] = vacancy_id
    await callback.message.answer("Пришли новый текст сопроводительного одним сообщением. Я покажу предпросмотр перед отправкой.")
    await callback.answer()


@router.callback_query(F.data.startswith("apply_send:"))
async def apply_send_callback(callback: CallbackQuery, settings: Settings, db: Database, apply_service: ApplyService) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    vacancy_id = callback.data.split(":", 1)[1]
    user_settings = db.ensure_user_settings(settings.telegram_user_id)
    letter = prepared_letters.get((settings.telegram_user_id, vacancy_id))
    if not letter:
        await callback.message.answer("Нет подготовленного письма. Сначала нажми «Откликнуться» и проверь предпросмотр.")
        await callback.answer()
        return
    result = await apply_service.apply_to_vacancy(
        settings.telegram_user_id,
        vacancy_id,
        user_settings["selected_resume_id"],
        letter,
        mode="manual",
        test_mode=bool(user_settings["test_mode"]),
    )
    await callback.message.answer(result.message)
    await callback.answer()


@router.callback_query(F.data == "queue:confirm")
async def queue_confirm_callback(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    queue = db.queue_items(settings.telegram_user_id, "pending", 20)
    if not queue:
        await callback.message.answer("Очередь пуста.")
        await callback.answer()
        return
    await callback.message.answer(
        f"К отправке подготовлено откликов: {len(queue)}.\n\n"
        "Это финальное подтверждение полуавто режима. Реальные запросы в HH уйдут только после кнопки ниже.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтверждаю отправку выбранных откликов", callback_data="queue:send")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="apply_cancel")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "queue:send")
async def queue_send_callback(callback: CallbackQuery, settings: Settings, db: Database, apply_service: ApplyService) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    user_settings = db.ensure_user_settings(settings.telegram_user_id)
    queue = db.queue_items(settings.telegram_user_id, "pending", 20)
    if not queue:
        await callback.message.answer("Очередь пуста.")
        await callback.answer()
        return
    sent = 0
    failed = 0
    for item in queue:
        result = await apply_service.apply_to_vacancy(
            settings.telegram_user_id,
            item["vacancy_id"],
            item["resume_id"],
            item["cover_letter"],
            mode="semi_auto",
            test_mode=bool(user_settings["test_mode"]),
        )
        db.update_queue_status(item["id"], "sent" if result.ok else "failed")
        if result.ok:
            sent += 1
        else:
            failed += 1
    await callback.message.answer(f"Очередь обработана. Успешно: {sent}. Ошибок: {failed}.")
    await callback.answer()


@router.callback_query(F.data == "queue:clear")
async def queue_clear_callback(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    for item in db.queue_items(settings.telegram_user_id, "pending", 100):
        db.update_queue_status(item["id"], "rejected")
    await callback.message.answer("Очередь очищена. Реальные отклики не отправлялись.")
    await callback.answer()


@router.callback_query(F.data == "apply_cancel")
async def apply_cancel_callback(callback: CallbackQuery, settings: Settings) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    await callback.message.answer("Отклик отменён. Реальный запрос в HH не отправлялся.")
    await callback.answer()


@router.callback_query(F.data.startswith("favorite:"))
async def favorite_handler(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    vacancy_id = int(callback.data.split(":", 1)[1])
    db.set_favorite(vacancy_id, True)
    await callback.answer("Сохранил в избранное.")


@router.callback_query(F.data.startswith("reject:"))
async def reject_handler(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not is_allowed_callback(callback, settings):
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


@router.callback_query(F.data.startswith("hide_employer:"))
async def hide_employer_handler(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if not is_allowed_callback(callback, settings):
        await callback.answer("Кнопка доступна только владельцу JobRadar.", show_alert=True)
        return
    vacancy_id = callback.data.split(":", 1)[1]
    row = db.get_vacancy_log(settings.telegram_user_id, vacancy_id)
    if row and row["employer_id"]:
        db.add_employer_blacklist(settings.telegram_user_id, row["employer_id"], row["employer_name"])
        await callback.answer("Работодатель скрыт.")
    else:
        await callback.answer("Пока не знаю ID работодателя для этой вакансии.", show_alert=True)


def format_hh_status(user_settings, settings: Settings) -> str:
    status = "подключен" if user_settings["hh_connected"] else "не подключен"
    resume = user_settings["selected_resume_title"] or "не выбрано"
    return (
        "HH подключение:\n\n"
        f"Статус: <b>{status}</b>\n"
        f"Резюме для откликов: <b>{escape_html(resume)}</b>\n\n"
        "Секреты приложения здесь не меняются: идентификатор клиента, секрет клиента, адрес возврата и ключ шифрования хранятся только в переменных окружения."
    )


def format_settings(row) -> str:
    return (
        "Настройки JobRadar:\n\n"
        f"Статус HH: <b>{'подключен' if row['hh_connected'] else 'не подключен'}</b>\n"
        f"Выбранное резюме: <b>{escape_html(row['selected_resume_title'] or 'не выбрано')}</b>\n"
        f"Режим откликов: <b>{apply_mode_label(row['apply_mode'])}</b>\n"
        f"Автопоиск: <b>{'включен' if row['search_enabled'] else 'выключен'}</b>\n"
        f"Интервал поиска: <b>{row['search_interval_minutes']} мин.</b>\n"
        f"Минимальный скоринг для показа: <b>{row['min_score_for_show']}</b>\n"
        f"Минимальный скоринг для полуавто: <b>{row['min_score_for_semi_auto']}</b>\n"
        f"Минимальный скоринг для авто: <b>{row['min_score_for_auto']}</b>\n"
        f"Лимит автооткликов в день: <b>{row['auto_daily_limit']}</b>\n"
        f"Только удаленка: <b>{'да' if row['only_remote'] else 'нет'}</b>\n"
        f"Регион поиска: <b>{escape_html(row['areas'])}</b>\n"
        f"Минус-слова: <b>{escape_html(row['negative_keywords'])}</b>\n"
        f"Плюс-слова: <b>{escape_html(row['positive_keywords'])}</b>\n"
        f"Ссылка на портфолио: <b>{escape_html(row['portfolio_url'] or 'не указана')}</b>\n"
        f"Тестовый режим: <b>{'включен' if row['test_mode'] else 'выключен'}</b>\n\n"
        "Настройки меняются только через рабочие кнопки бота. Секреты HH здесь недоступны."
    )


def apply_mode_label(value: str | None) -> str:
    return {
        "off": "выключено",
        "button": "по кнопке",
        "semi_auto": "полуавто",
        "auto": "авто",
        "manual": "по кнопке",
    }.get(value or "", "не задан")


def status_label(value: str) -> str:
    return {
        "found": "найдена",
        "shown": "показана",
        "skipped": "пропущена",
        "queued": "в очереди",
        "approved": "одобрена",
        "applied": "отклик отправлен",
        "failed": "ошибка",
        "hidden": "скрыта",
    }.get(value, value)


async def apply_setting_text(message: Message, db: Database, telegram_user_id: int, action: str) -> None:
    text = (message.text or "").strip()
    try:
        if action == "interval":
            value = bounded_int(text, 15, 1440)
            db.update_user_settings(telegram_user_id, search_interval_minutes=value)
            await message.answer(f"Интервал поиска обновлён: {value} мин.")
        elif action == "salary":
            value = None if text.lower() in {"нет", "сброс", "пусто", "0"} else bounded_int(text, 0, 1000000)
            db.update_user_settings(telegram_user_id, salary_from=value)
            await message.answer(f"Минимальная зарплата: {value if value else 'не задана'}.")
        elif action == "areas":
            areas = [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
            if not areas:
                raise ValueError("Нужно указать хотя бы один регион HH.")
            db.update_user_settings(telegram_user_id, areas=areas)
            await message.answer(f"Регионы поиска обновлены: {', '.join(areas)}.")
        elif action == "positive":
            values = split_words(text)
            db.update_user_settings(telegram_user_id, positive_keywords=values)
            await message.answer(f"Плюс-слова обновлены: {len(values)}.")
        elif action == "negative":
            values = split_words(text)
            db.update_user_settings(telegram_user_id, negative_keywords=values)
            await message.answer(f"Минус-слова обновлены: {len(values)}.")
        elif action == "letter":
            if not text:
                raise ValueError("Шаблон письма не должен быть пустым.")
            db.update_user_settings(telegram_user_id, cover_letter_template=text)
            await message.answer("Шаблон сопроводительного обновлён.")
        elif action == "search":
            values = split_words(text)
            db.update_user_settings(telegram_user_id, keywords=values)
            await message.answer(f"Поисковые запросы обновлены: {len(values)}.")
        elif action == "score":
            show, semi, auto = [bounded_int(part.strip(), 0, 100) for part in text.replace(";", ",").split(",")[:3]]
            db.update_user_settings(
                telegram_user_id,
                min_score_for_show=show,
                min_score_for_semi_auto=semi,
                min_score_for_auto=auto,
            )
            await message.answer(f"Пороги обновлены: показ {show}, полуавто {semi}, авто {auto}.")
        elif action == "auto_limits":
            daily, run_limit, delay_min, delay_max = [bounded_int(part.strip(), 1, 200) for part in text.replace(";", ",").split(",")[:4]]
            if delay_min > delay_max:
                raise ValueError("Минимальная задержка не должна быть больше максимальной.")
            db.update_user_settings(
                telegram_user_id,
                auto_daily_limit=daily,
                auto_run_limit=run_limit,
                auto_delay_min_minutes=delay_min,
                auto_delay_max_minutes=delay_max,
            )
            await message.answer(f"Лимиты обновлены: {daily} в день, {run_limit} за запуск, задержка {delay_min}-{delay_max} мин.")
    except Exception as exc:
        pending_settings[telegram_user_id] = action
        await message.answer(f"Не получилось сохранить: {escape_html(str(exc))}\nПришли значение ещё раз.", parse_mode="HTML")


def setting_prompt(action: str) -> str:
    prompts = {
        "interval": "Пришли интервал поиска в минутах. Например: 60",
        "salary": "Пришли минимальную зарплату числом или «сброс», чтобы убрать фильтр.",
        "areas": "Пришли ID регионов HH через запятую. Россия — 113.",
        "positive": "Пришли плюс-слова через запятую. Например: API, SQL, Jira, тестирование",
        "negative": "Пришли минус-слова через запятую. Например: колл-центр, холодные звонки, продажи по телефону",
        "letter": "Пришли новый шаблон сопроводительного письма одним сообщением.",
        "search": "Пришли поисковые запросы через запятую. Например: QA junior, helpdesk, support engineer",
        "score": "Пришли три порога через запятую: показ, полуавто, авто. Например: 55, 70, 85",
        "auto_limits": "Пришли четыре числа через запятую: лимит в день, лимит за запуск, задержка минимум, задержка максимум. Например: 5, 2, 7, 25",
    }
    return prompts.get(action, "Пришли новое значение настройки.")


def split_words(text: str) -> list[str]:
    values = [part.strip() for part in text.replace("\n", ",").replace(";", ",").split(",") if part.strip()]
    if not values:
        raise ValueError("Список не должен быть пустым.")
    return values


def bounded_int(text: str, minimum: int, maximum: int) -> int:
    value = int(text)
    if value < minimum or value > maximum:
        raise ValueError(f"Значение должно быть от {minimum} до {maximum}.")
    return value
