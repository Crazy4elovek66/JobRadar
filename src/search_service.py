from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import LinkPreviewOptions

from src.apply_service import ApplyService
from src.cover_letter import build_cover_letter
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
    queued: int = 0
    auto_applied: int = 0


class SearchService:
    def __init__(self, db: Database, hh_client: HHClient, apply_service: ApplyService, min_score_to_send: int) -> None:
        self.db = db
        self.hh_client = hh_client
        self.apply_service = apply_service
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
        summary = SearchSummary()
        settings = self.db.ensure_user_settings(user_id)
        keywords = json.loads(settings["keywords"] or "[]")
        areas = json.loads(settings["areas"] or '["113"]')
        ignored_external_ids = self.db.ignored_external_ids(source="hh")
        max_messages = 10

        async for vacancy in self.hh_client.search_all(
            ignored_external_ids=ignored_external_ids,
            keywords=keywords,
            areas=areas,
            only_remote=bool(settings["only_remote"]),
        ):
            summary.found += 1
            if vacancy.external_id in ignored_external_ids:
                continue
            score = calculate_score(vacancy)
            row = self.db.upsert_vacancy(vacancy, score)
            summary.saved += 1
            self._log_found(user_id, vacancy, score)

            if score.status == "REJECT":
                summary.rejected += 1
                self.db.upsert_vacancy_log(user_id, vacancy.external_id, status="skipped", skip_reason="Скоринг отклонил вакансию")
                continue
            if row["sent_at"] or row["is_rejected_by_user"]:
                continue

            mode = settings["apply_mode"]
            if mode == "auto" and await self._try_auto_apply(bot, user_id, vacancy, score, settings):
                summary.auto_applied += 1
                continue
            if mode == "semi_auto" and score.score >= settings["min_score_for_semi_auto"] and settings["selected_resume_id"]:
                letter = build_cover_letter(vacancy, settings)
                self.db.add_to_queue(user_id, vacancy.external_id, settings["selected_resume_id"], score.score, letter)
                self.db.upsert_vacancy_log(user_id, vacancy.external_id, status="queued", apply_mode="semi_auto", cover_letter=letter)
                summary.queued += 1
                continue
            if score.score < settings["min_score_for_show"]:
                continue

            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=format_vacancy_message(vacancy, score),
                    reply_markup=vacancy_keyboard(row["id"], vacancy.external_id, vacancy.url),
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception("Failed to send vacancy %s", vacancy.external_id)
                continue

            self.db.mark_sent(row["id"])
            self.db.upsert_vacancy_log(user_id, vacancy.external_id, status="shown", apply_mode=mode)
            summary.sent += 1
            if summary.sent >= max_messages:
                break

        if summary.queued:
            await bot.send_message(
                user_id,
                f"Нашёл {summary.queued} сильных вакансий и добавил их в очередь полуавтоотклика. Открой «🧾 Мои отклики», чтобы выбрать, куда отправить отклик.",
                parse_mode="HTML",
            )
        self.db.update_user_settings(user_id, last_search_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        return summary

    async def _try_auto_apply(self, bot: Bot, user_id: int, vacancy: Vacancy, score: ScoreResult, settings) -> bool:
        if not settings["hh_connected"] or not settings["selected_resume_id"] or not settings["auto_acknowledged_at"]:
            return False
        if score.score < settings["min_score_for_auto"]:
            return False
        today_stats = self.db.application_stats_today(user_id)
        if today_stats["auto"] >= settings["auto_daily_limit"]:
            return False
        if self.db.get_vacancy_log(user_id, vacancy.external_id) and self.db.get_vacancy_log(user_id, vacancy.external_id)["status"] == "applied":
            return False
        delay_minutes = random.randint(settings["auto_delay_min_minutes"], settings["auto_delay_max_minutes"])
        scheduled_at = (datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)).isoformat(timespec="seconds")
        letter = build_cover_letter(vacancy, settings)
        self.db.add_to_queue(user_id, vacancy.external_id, settings["selected_resume_id"], score.score, letter, status="pending", scheduled_at=scheduled_at)
        self.db.upsert_vacancy_log(user_id, vacancy.external_id, status="queued", apply_mode="auto", cover_letter=letter)
        return True

    def _log_found(self, user_id: int, vacancy: Vacancy, score: ScoreResult) -> None:
        employer = (vacancy.raw.get("employer") or {}) if isinstance(vacancy.raw, dict) else {}
        self.db.upsert_vacancy_log(
            user_id,
            vacancy.external_id,
            status="found",
            vacancy_name=vacancy.title,
            employer_id=str(employer.get("id") or ""),
            employer_name=vacancy.company,
            vacancy_url=vacancy.url,
            score=score.score,
        )


def format_vacancy_message(vacancy: Vacancy, score: ScoreResult) -> str:
    icon = "🔥" if score.status == "HOT" else "🟢" if score.status == "GOOD" else "🟡"
    title = escape_html(vacancy.title)
    company = escape_html(vacancy.company)
    salary = escape_html(format_salary(vacancy.salary.salary_from, vacancy.salary.salary_to, vacancy.salary.currency))
    schedule = escape_html(schedule_to_ru(vacancy.schedule))
    experience = escape_html(experience_to_ru(vacancy.experience))
    url = escape_html(vacancy.url)
    positives = "\n".join(f"✅ {escape_html(reason)}" for reason in score.reasons_positive[:7]) or "✅ Есть признаки полезной IT-вакансии"
    negatives = "\n".join(f"⚠️ {escape_html(reason)}" for reason in score.reasons_negative[:5]) or "рисков не найдено"
    return (
        f"{icon} <b>{title}</b>\n\n"
        f"<b>Компания:</b> {company}\n"
        f"<b>Зарплата:</b> {salary}\n"
        f"<b>Формат:</b> {schedule}\n"
        f"<b>Опыт:</b> {experience}\n"
        f"<b>Скоринг JobRadar:</b> {score.score}/100\n\n"
        f"<b>Почему подходит:</b>\n{positives}\n\n"
        f"<b>Почему может не подойти:</b>\n{negatives}\n\n"
        f"<b>Ссылка на HH:</b>\n{url}"
    )
