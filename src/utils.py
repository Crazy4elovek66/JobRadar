from __future__ import annotations

import html
import re
from datetime import datetime, timezone


TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def clean_html(value: str | None) -> str:
    if not value:
        return ""
    text = TAG_RE.sub(" ", value)
    return normalize_text(html.unescape(text))


def escape_html(value: object | None) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return SPACE_RE.sub(" ", value).strip()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_salary(salary_from: int | None, salary_to: int | None, currency: str | None) -> str:
    if not salary_from and not salary_to:
        return "не указана"
    currency_label = "₽" if currency == "RUR" or not currency else currency
    if salary_from and salary_to:
        return f"{salary_from:,}–{salary_to:,} {currency_label}".replace(",", " ")
    if salary_from:
        return f"от {salary_from:,} {currency_label}".replace(",", " ")
    return f"до {salary_to:,} {currency_label}".replace(",", " ")


def schedule_to_ru(schedule: str | None) -> str:
    if not schedule:
        return "не указан"
    value = schedule.lower()
    if "remote" in value or "удален" in value or "удалён" in value:
        return "удалённо"
    if "hybrid" in value or "гибрид" in value:
        return "гибрид"
    if "full" in value or "office" in value:
        return "офис"
    return schedule


def experience_to_ru(experience: str | None) -> str:
    if not experience:
        return "не указан"
    mapping = {
        "noExperience": "без опыта",
        "between1And3": "1-3 года",
        "between3And6": "3-6 лет",
        "moreThan6": "более 6 лет",
    }
    return mapping.get(experience, experience)
