from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔎 Поиск вакансий"), KeyboardButton(text="⚙️ Настройки")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="🔗 HH подключение")],
            [KeyboardButton(text="🧾 Мои отклики")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def vacancy_keyboard(row_id: int, vacancy_external_id: str, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Откликнуться", callback_data=f"apply_preview:{vacancy_external_id}"),
                InlineKeyboardButton(text="✍️ Изменить письмо", callback_data=f"letter:{vacancy_external_id}"),
            ],
            [
                InlineKeyboardButton(text="👀 Открыть на HH", url=url),
                InlineKeyboardButton(text="🚫 Скрыть вакансию", callback_data=f"reject:{row_id}"),
            ],
            [
                InlineKeyboardButton(text="🏢 Скрыть работодателя", callback_data=f"hide_employer:{vacancy_external_id}"),
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings:open"),
            ],
        ]
    )


def settings_keyboard(hh_connected: bool) -> InlineKeyboardMarkup:
    connect_text = "🔄 Проверить cookie HH" if hh_connected else "🔗 Подключить HH по cookie"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=connect_text, callback_data="hh:connect")],
            [InlineKeyboardButton(text="🧾 Выбрать резюме", callback_data="hh:resumes")],
            [InlineKeyboardButton(text="🔁 Режим откликов", callback_data="settings:apply_mode")],
            [InlineKeyboardButton(text="🔎 Настройки поиска", callback_data="settings:search")],
            [InlineKeyboardButton(text="🎯 Пороги скоринга", callback_data="settings:score")],
            [InlineKeyboardButton(text="🚦 Лимиты автооткликов", callback_data="settings:auto_limits")],
            [InlineKeyboardButton(text="🧠 Шаблон сопроводительного", callback_data="settings:letter")],
            [InlineKeyboardButton(text="➕ Плюс-слова", callback_data="settings:positive")],
            [InlineKeyboardButton(text="➖ Минус-слова", callback_data="settings:negative")],
            [InlineKeyboardButton(text="🏙 Регион", callback_data="settings:areas")],
            [InlineKeyboardButton(text="💰 Зарплата", callback_data="settings:salary")],
            [InlineKeyboardButton(text="🌍 Только удаленка", callback_data="settings:remote")],
            [InlineKeyboardButton(text="⏱ Интервал поиска", callback_data="settings:interval")],
            [InlineKeyboardButton(text="🧪 Тестовый режим", callback_data="settings:test_mode")],
            [InlineKeyboardButton(text="🧯 Экстренно выключить автоотклики", callback_data="settings:stop_auto")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
        ]
    )


def apply_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚫 Выключено", callback_data="settings:mode:off")],
            [InlineKeyboardButton(text="✅ По кнопке", callback_data="settings:mode:button")],
            [InlineKeyboardButton(text="🟡 Полуавто", callback_data="settings:mode:semi_auto")],
            [InlineKeyboardButton(text="🤖 Авто", callback_data="settings:mode:auto_warning")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings:open")],
        ]
    )


def auto_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я понимаю, включить автоотклики", callback_data="settings:mode:auto_confirm")],
            [InlineKeyboardButton(text="❌ Оставить ручной режим", callback_data="settings:mode:button")],
        ]
    )


def apply_confirm_keyboard(vacancy_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Отправить отклик", callback_data=f"apply_send:{vacancy_id}")],
            [InlineKeyboardButton(text="✍️ Изменить письмо", callback_data=f"letter:{vacancy_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="apply_cancel")],
        ]
    )


def hh_keyboard(hh_connected: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Подключить HH по cookie", callback_data="hh:connect")],
            [InlineKeyboardButton(text="🔄 Обновить проверку cookie", callback_data="hh:connect")],
            [InlineKeyboardButton(text="✅ Проверить подключение", callback_data="hh:check")],
            [InlineKeyboardButton(text="🩺 Диагностика HH", callback_data="hh:diag")],
            [InlineKeyboardButton(text="🧾 Выбрать резюме", callback_data="hh:resumes")],
            [InlineKeyboardButton(text="❌ Отключить HH", callback_data="hh:disconnect")],
        ]
    )
