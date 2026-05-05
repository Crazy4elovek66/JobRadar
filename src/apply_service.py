from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.cover_letter import build_cover_letter
from src.database import Database
from src.hh_client import HHApiError, HHClient
from src.models import Salary, Vacancy


@dataclass(slots=True)
class ApplyResult:
    ok: bool
    message: str
    error_type: str | None = None
    error_value: str | None = None


class ApplyService:
    def __init__(self, db: Database, hh_client: HHClient) -> None:
        self.db = db
        self.hh_client = hh_client

    async def prepare_cover_letter(self, telegram_user_id: int, vacancy_id: str) -> tuple[Vacancy, str]:
        settings = self.db.ensure_user_settings(telegram_user_id)
        resume_id = settings["selected_resume_id"]
        detail = await self.hh_client.get_vacancy(telegram_user_id, vacancy_id, resume_id=resume_id)
        vacancy = vacancy_from_detail(detail)
        return vacancy, build_cover_letter(vacancy, settings)

    async def apply_to_vacancy(
        self,
        telegram_user_id: int,
        vacancy_id: str,
        resume_id: str,
        message: str,
        mode: str,
        test_mode: bool = False,
    ) -> ApplyResult:
        settings = self.db.ensure_user_settings(telegram_user_id)
        existing = self.db.get_vacancy_log(telegram_user_id, vacancy_id)
        if existing and existing["status"] == "applied":
            return ApplyResult(False, "На эту вакансию уже был отклик.", error_value="already_applied")
        if not message.strip():
            return ApplyResult(False, "Сопроводительное письмо пустое. Нужно изменить текст.", error_value="empty_message")
        if len(message) > 4000:
            return ApplyResult(False, "Сопроводительное письмо слишком длинное. Нужно сократить текст.", error_value="too_long_message")

        try:
            detail = await self.hh_client.get_vacancy(telegram_user_id, vacancy_id, resume_id=resume_id)
        except HHApiError as exc:
            self._log_failure(telegram_user_id, vacancy_id, mode, message, exc)
            return ApplyResult(False, exc.args[0], exc.error_type, exc.error_value)

        vacancy = vacancy_from_detail(detail)
        block_reason = self._blocking_reason(detail, settings)
        if block_reason:
            self.db.upsert_vacancy_log(
                telegram_user_id,
                vacancy_id,
                status="skipped",
                vacancy_name=vacancy.title,
                employer_id=str((detail.get("employer") or {}).get("id") or ""),
                employer_name=vacancy.company,
                vacancy_url=vacancy.url,
                skip_reason=block_reason,
                apply_mode=mode,
                cover_letter=message,
            )
            return ApplyResult(False, block_reason)

        if test_mode or settings["test_mode"]:
            self.db.upsert_vacancy_log(
                telegram_user_id,
                vacancy_id,
                status="approved",
                vacancy_name=vacancy.title,
                employer_id=str((detail.get("employer") or {}).get("id") or ""),
                employer_name=vacancy.company,
                vacancy_url=vacancy.url,
                apply_mode=mode,
                cover_letter=message,
            )
            return ApplyResult(True, f"Тестовый режим: отклик на «{vacancy.title}» был бы отправлен, но реальный запрос в HH не выполнен.")

        payload: dict[str, Any] = {
            "vacancy_id": vacancy_id,
            "resume_id": resume_id,
            "message": message,
        }
        action_url = self._response_action_url(detail)
        if action_url:
            payload["action_url"] = action_url

        try:
            await self.hh_client.apply_to_vacancy(telegram_user_id, payload)
        except HHApiError as exc:
            self._log_failure(telegram_user_id, vacancy_id, mode, message, exc)
            if settings["auto_stop_on_error"] and mode == "auto" and exc.error_value in {"captcha_required", "limit_exceeded", "token_revoked", "token-revoked"}:
                self.db.update_user_settings(telegram_user_id, apply_mode="button", auto_acknowledged_at=None)
            return ApplyResult(False, exc.args[0], exc.error_type, exc.error_value)

        self.db.upsert_vacancy_log(
            telegram_user_id,
            vacancy_id,
            status="applied",
            vacancy_name=vacancy.title,
            employer_id=str((detail.get("employer") or {}).get("id") or ""),
            employer_name=vacancy.company,
            vacancy_url=vacancy.url,
            apply_mode=mode,
            cover_letter=message,
            applied_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        return ApplyResult(True, f"Отклик отправлен: {vacancy.title} — {vacancy.company}")

    def _blocking_reason(self, detail: dict[str, Any], settings: Any) -> str | None:
        if detail.get("archived"):
            return "Вакансия уже в архиве. Отклик не отправляю."
        if detail.get("has_test") and settings["auto_skip_has_test"]:
            return "Для вакансии нужен тест. Через API такой отклик не отправляю, открой вакансию на HH."
        if detail.get("response_url") and settings["auto_skip_external_response_url"]:
            return "Работодатель просит внешний отклик. Автоматически не отправляю."
        if detail.get("response_letter_required") and settings["auto_skip_required_letter"]:
            return "Работодатель требует письмо, а в настройках такие вакансии пропускаются."
        employer_id = str((detail.get("employer") or {}).get("id") or "")
        if self.db.is_employer_blacklisted(settings["telegram_user_id"], employer_id):
            return "Работодатель скрыт в JobRadar."
        return None

    def _response_action_url(self, detail: dict[str, Any]) -> str | None:
        for action in detail.get("negotiations_actions") or []:
            action_id = str(action.get("id") or action.get("name") or "").lower()
            if "response" in action_id or "отклик" in action_id:
                return action.get("url")
        return None

    def _log_failure(self, telegram_user_id: int, vacancy_id: str, mode: str, message: str, exc: HHApiError) -> None:
        self.db.upsert_vacancy_log(
            telegram_user_id,
            vacancy_id,
            status="failed",
            apply_mode=mode,
            cover_letter=message,
            error_type=exc.error_type,
            error_value=exc.error_value,
        )


def vacancy_from_detail(detail: dict[str, Any]) -> Vacancy:
    salary_raw = detail.get("salary") or {}
    employer = detail.get("employer") or {}
    area = detail.get("area") or {}
    schedule = detail.get("schedule") or {}
    experience = detail.get("experience") or {}
    employment = detail.get("employment") or {}
    return Vacancy(
        source="hh",
        external_id=str(detail.get("id") or ""),
        title=detail.get("name") or "Без названия",
        company=employer.get("name") or "Компания не указана",
        url=detail.get("alternate_url") or "",
        area=area.get("name"),
        schedule=schedule.get("name") or schedule.get("id"),
        experience=experience.get("id") or experience.get("name"),
        employment=employment.get("name") or employment.get("id"),
        salary=Salary(salary_from=salary_raw.get("from"), salary_to=salary_raw.get("to"), currency=salary_raw.get("currency")),
        description=str(detail.get("description") or ""),
        raw=detail,
    )
