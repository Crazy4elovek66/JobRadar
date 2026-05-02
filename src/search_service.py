from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram import Bot
from aiogram.types import LinkPreviewOptions

from src.database import Database
from src.hh_client import HHClient
from src.keyboards import vacancy_keyboard
from src.models import ScoreResult, Vacancy
from src.scoring import calculate_score
from src.utils import escape_html, experience_to_ru, format_salary, schedule_to_ru


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SearchSummary:
    found: int = 0
    saved: int = 0
    sent: int = 0
    rejected: int = 0


class SearchService:
    def __init__(self, db: Database, hh_client: HHClient, min_score_to_send: int) -> None:
        self.db = db
        self.hh_client = hh_client
        self.min_score_to_send = min_score_to_send
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def run(self, bot: Bot, user_id: int) -> SearchSummary | None:
        if self._is_running:
            return None

        self._is_running = True
        try:
            return await self._run(bot, user_id)
        finally:
            self._is_running = False

    async def _run(self, bot: Bot, user_id: int) -> SearchSummary:
        MAX_VACANCIES_PER_RUN = 10

        summary = SearchSummary()
        ignored_external_ids = self.db.ignored_external_ids(source="hh")
        vacancies = await self.hh_client.search_all(ignored_external_ids=ignored_external_ids)
        summary.found = len(vacancies)

        for vacancy in vacancies:
            if vacancy.external_id in ignored_external_ids:
                continue
            score = calculate_score(vacancy)
            row = self.db.upsert_vacancy(vacancy, score)
            summary.saved += 1

            if score.status == "REJECT":
                summary.rejected += 1
                continue
            if score.score < self.min_score_to_send:
                continue
            if row["sent_at"] or row["is_rejected_by_user"]:
                continue

            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=format_vacancy_message(vacancy, score),
                    reply_markup=vacancy_keyboard(row["id"], vacancy.url),
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception("Failed to send vacancy %s", vacancy.external_id)
                continue

            self.db.mark_sent(row["id"])
            summary.sent += 1
            if summary.sent >= MAX_VACANCIES_PER_RUN:
                break

        return summary


def format_vacancy_message(vacancy: Vacancy, score: ScoreResult) -> str:
    icon = "🔥" if score.status == "HOT" else "🟢" if score.status == "GOOD" else "🟡"
    title = escape_html(vacancy.title)
    company = escape_html(vacancy.company)
    salary = escape_html(format_salary(vacancy.salary.salary_from, vacancy.salary.salary_to, vacancy.salary.currency))
    schedule = escape_html(schedule_to_ru(vacancy.schedule))
    experience = escape_html(experience_to_ru(vacancy.experience))
    url = escape_html(vacancy.url)
    positives = "\n".join(f"✅ {escape_html(reason)}" for reason in score.reasons_positive[:7])
    negatives = "\n".join(f"⚠️ {escape_html(reason)}" for reason in score.reasons_negative[:5])
    positives = positives or "✅ есть признаки полезной IT-вакансии"
    negatives = negatives or "рисков не найдено"

    return (
        f"{icon} {score.score}/100 — <b>{title}</b>\n\n"
        f"<b>Компания:</b> {company}\n"
        f"<b>Зарплата:</b> {salary}\n"
        f"Формат: {schedule}\n"
        f"Опыт: {experience}\n"
        f"Карьерная польза: {score.career_value}/10\n\n"
        f"<b>Почему подходит:</b>\n{positives}\n\n"
        f"<b>Риски:</b>\n{negatives}\n\n"
        f"Ссылка:\n{url}"
    )
