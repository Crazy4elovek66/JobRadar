from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def vacancy_keyboard(vacancy_id: int, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть вакансию", url=url)],
            [
                InlineKeyboardButton(text="В избранное", callback_data=f"favorite:{vacancy_id}"),
                InlineKeyboardButton(text="Не подходит", callback_data=f"reject:{vacancy_id}"),
            ],
        ]
    )
