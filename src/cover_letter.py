from __future__ import annotations

import sqlite3

from src.models import Vacancy
from src.utils import normalize_text


def build_cover_letter(vacancy: Vacancy, settings: sqlite3.Row) -> str:
    text = f"{vacancy.title} {vacancy.description}".lower().replace("ё", "е")
    base = settings["cover_letter_template"] or (
        "Здравствуйте! Заинтересовала вакансия. Мне близки задачи, связанные с технической поддержкой, "
        "поиском причин проблем, тестированием, автоматизацией и работой с IT-инструментами. Сейчас активно "
        "развиваюсь в этом направлении, собираю собственные проекты и использую AI-инструменты для ускорения "
        "разработки и анализа. Готов быстро вникнуть в процессы, аккуратно выполнять задачи и развиваться внутри команды."
    )
    focus = ""
    if any(word in text for word in ["qa", "тестиров", "баг", "test", "quality"]):
        focus = "В этой роли особенно интересны тестирование, внимательная проверка сценариев и аккуратная работа с багами."
    elif any(word in text for word in ["support", "helpdesk", "service desk", "поддерж", "инцидент", "диагност"]):
        focus = "В этой роли особенно интересны диагностика проблем, разбор обращений и помощь пользователям без потери технической глубины."
    elif any(word in text for word in ["crm", "внедрен", "implementation", "интеграц", "настройк"]):
        focus = "В этой роли особенно интересны настройка систем, внедрение процессов, интеграции и понятная коммуникация с командой."
    elif any(word in text for word in ["low-code", "no-code", "автоматизац", "mvp", "скрипт"]):
        focus = "В этой роли особенно интересны быстрые MVP, автоматизация рутины и связки сервисов."

    parts = [base.strip()]
    if focus:
        parts.append(focus)
    if settings["portfolio_url"]:
        parts.append(f"Портфолио: {settings['portfolio_url']}")
    return normalize_text("\n\n".join(parts))
