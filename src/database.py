from __future__ import annotations

from contextlib import closing
import json
import sqlite3
from pathlib import Path
from typing import Any

from src.models import ScoreResult, Vacancy
from src.utils import utc_now_iso


DEFAULT_KEYWORDS = [
    "QA junior",
    "тестировщик junior",
    "manual qa",
    "техническая поддержка L2",
    "support engineer",
    "helpdesk",
    "implementation specialist",
    "специалист по внедрению",
    "CRM",
    "low-code",
    "no-code",
    "automation",
    "junior python",
    "junior frontend",
    "стажер тестировщик",
]

DEFAULT_NEGATIVE_KEYWORDS = [
    "горячая линия",
    "колл-центр",
    "call-центр",
    "оператор call",
    "оператор колл",
    "исходящие звонки",
    "холодные звонки",
    "продажи по телефону",
    "менеджер по продажам",
    "продавец-консультант",
    "кассир",
    "официант",
    "курьер",
    "комплектовщик",
    "оператор поддержки без технической части",
    "массовый обзвон",
]

DEFAULT_POSITIVE_KEYWORDS = [
    "API",
    "SQL",
    "CRM",
    "Jira",
    "Helpdesk",
    "Service Desk",
    "баг",
    "баги",
    "тестирование",
    "QA",
    "логи",
    "интеграции",
    "автоматизация",
    "скрипты",
    "Python",
    "JavaScript",
    "Postman",
    "Git",
    "техническая диагностика",
    "настройка",
    "внедрение",
    "low-code",
    "no-code",
]

DEFAULT_COVER_LETTER = (
    "Здравствуйте! Заинтересовала вакансия. Мне близки задачи, связанные с технической поддержкой, "
    "поиском причин проблем, тестированием, автоматизацией и работой с IT-инструментами. Сейчас активно "
    "развиваюсь в этом направлении, собираю собственные проекты и использую AI-инструменты для ускорения "
    "разработки и анализа. Готов быстро вникнуть в процессы, аккуратно выполнять задачи и развиваться внутри команды."
)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vacancies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT,
                    salary_from INTEGER,
                    salary_to INTEGER,
                    currency TEXT,
                    url TEXT,
                    area TEXT,
                    schedule TEXT,
                    experience TEXT,
                    score INTEGER,
                    status TEXT,
                    career_value INTEGER,
                    reasons_positive TEXT,
                    reasons_negative TEXT,
                    raw_json TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    sent_at TEXT,
                    is_favorite INTEGER NOT NULL DEFAULT 0,
                    is_rejected_by_user INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(source, external_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hh_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL UNIQUE,
                    hh_user_id TEXT,
                    hh_user_type TEXT,
                    access_token_encrypted TEXT NOT NULL,
                    refresh_token_encrypted TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    telegram_user_id INTEGER PRIMARY KEY,
                    hh_connected INTEGER NOT NULL DEFAULT 0,
                    selected_resume_id TEXT,
                    selected_resume_title TEXT,
                    search_enabled INTEGER NOT NULL DEFAULT 1,
                    search_interval_minutes INTEGER NOT NULL DEFAULT 60,
                    apply_mode TEXT NOT NULL DEFAULT 'button',
                    min_score_for_show INTEGER NOT NULL DEFAULT 55,
                    min_score_for_semi_auto INTEGER NOT NULL DEFAULT 70,
                    min_score_for_auto INTEGER NOT NULL DEFAULT 85,
                    auto_daily_limit INTEGER NOT NULL DEFAULT 5,
                    auto_run_limit INTEGER NOT NULL DEFAULT 2,
                    auto_delay_min_minutes INTEGER NOT NULL DEFAULT 7,
                    auto_delay_max_minutes INTEGER NOT NULL DEFAULT 25,
                    auto_stop_on_error INTEGER NOT NULL DEFAULT 1,
                    auto_skip_has_test INTEGER NOT NULL DEFAULT 1,
                    auto_skip_external_response_url INTEGER NOT NULL DEFAULT 1,
                    auto_skip_required_letter INTEGER NOT NULL DEFAULT 0,
                    require_preview_before_apply INTEGER NOT NULL DEFAULT 1,
                    test_mode INTEGER NOT NULL DEFAULT 1,
                    areas TEXT NOT NULL DEFAULT '["113"]',
                    only_remote INTEGER NOT NULL DEFAULT 1,
                    salary_from INTEGER,
                    keywords TEXT NOT NULL DEFAULT '[]',
                    negative_keywords TEXT NOT NULL DEFAULT '[]',
                    positive_keywords TEXT NOT NULL DEFAULT '[]',
                    portfolio_url TEXT,
                    cover_letter_template TEXT,
                    auto_acknowledged_at TEXT,
                    last_search_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vacancy_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    vacancy_id TEXT NOT NULL,
                    vacancy_name TEXT,
                    employer_id TEXT,
                    employer_name TEXT,
                    vacancy_url TEXT,
                    score INTEGER,
                    status TEXT NOT NULL,
                    skip_reason TEXT,
                    apply_mode TEXT,
                    cover_letter TEXT,
                    error_type TEXT,
                    error_value TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    applied_at TEXT,
                    UNIQUE(telegram_user_id, vacancy_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS apply_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    vacancy_id TEXT NOT NULL,
                    resume_id TEXT NOT NULL,
                    score INTEGER,
                    cover_letter TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    scheduled_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(telegram_user_id, vacancy_id, resume_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS employer_blacklist (
                    telegram_user_id INTEGER NOT NULL,
                    employer_id TEXT NOT NULL,
                    employer_name TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (telegram_user_id, employer_id)
                )
                """
            )

    def upsert_vacancy(self, vacancy: Vacancy, score: ScoreResult) -> sqlite3.Row:
        now = utc_now_iso()
        with closing(self.connect()) as connection, connection:
            existing = connection.execute(
                "SELECT * FROM vacancies WHERE source = ? AND external_id = ?",
                (vacancy.source, vacancy.external_id),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE vacancies
                    SET title = ?, company = ?, salary_from = ?, salary_to = ?, currency = ?,
                        url = ?, area = ?, schedule = ?, experience = ?, score = ?, status = ?,
                        career_value = ?, reasons_positive = ?, reasons_negative = ?,
                        raw_json = ?, last_seen_at = ?
                    WHERE source = ? AND external_id = ?
                    """,
                    self._update_values(vacancy, score, now) + (vacancy.source, vacancy.external_id),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO vacancies (
                        source, external_id, title, company, salary_from, salary_to, currency,
                        url, area, schedule, experience, score, status, career_value,
                        reasons_positive, reasons_negative, raw_json, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._values(vacancy, score, now),
                )
            return connection.execute(
                "SELECT * FROM vacancies WHERE source = ? AND external_id = ?",
                (vacancy.source, vacancy.external_id),
            ).fetchone()

    def mark_sent(self, vacancy_id: int) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute("UPDATE vacancies SET sent_at = ? WHERE id = ?", (utc_now_iso(), vacancy_id))

    def set_favorite(self, vacancy_id: int, value: bool = True) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute("UPDATE vacancies SET is_favorite = ? WHERE id = ?", (1 if value else 0, vacancy_id))

    def reject_by_user(self, vacancy_id: int) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute("UPDATE vacancies SET is_rejected_by_user = 1 WHERE id = ?", (vacancy_id,))

    def ignored_external_ids(self, source: str = "hh") -> set[str]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT external_id
                FROM vacancies
                WHERE source = ?
                  AND (sent_at IS NOT NULL OR is_rejected_by_user = 1)
                """,
                (source,),
            ).fetchall()
        return {str(row["external_id"]) for row in rows}

    def stats(self) -> dict[str, int]:
        with closing(self.connect()) as connection, connection:
            total = connection.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0]
            sent = connection.execute("SELECT COUNT(*) FROM vacancies WHERE sent_at IS NOT NULL").fetchone()[0]
            status_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM vacancies GROUP BY status"
            ).fetchall()
        result = {"total": total, "sent": sent, "HOT": 0, "GOOD": 0, "MAYBE": 0, "REJECT": 0}
        for row in status_rows:
            result[row["status"]] = row["count"]
        return result

    def ensure_user_settings(self, telegram_user_id: int) -> sqlite3.Row:
        now = utc_now_iso()
        with closing(self.connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM user_settings WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
            if row:
                return row
            connection.execute(
                """
                INSERT INTO user_settings (
                    telegram_user_id, keywords, negative_keywords, positive_keywords,
                    cover_letter_template, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    telegram_user_id,
                    json.dumps(DEFAULT_KEYWORDS, ensure_ascii=False),
                    json.dumps(DEFAULT_NEGATIVE_KEYWORDS, ensure_ascii=False),
                    json.dumps(DEFAULT_POSITIVE_KEYWORDS, ensure_ascii=False),
                    DEFAULT_COVER_LETTER,
                    now,
                    now,
                ),
            )
            return connection.execute(
                "SELECT * FROM user_settings WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()

    def update_user_settings(self, telegram_user_id: int, **values: Any) -> sqlite3.Row:
        self.ensure_user_settings(telegram_user_id)
        if not values:
            return self.ensure_user_settings(telegram_user_id)
        values["updated_at"] = utc_now_iso()
        assignments = ", ".join(f"{key} = ?" for key in values)
        params = [self._json_or_scalar(value) for value in values.values()]
        params.append(telegram_user_id)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                f"UPDATE user_settings SET {assignments} WHERE telegram_user_id = ?",
                params,
            )
            return connection.execute(
                "SELECT * FROM user_settings WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()

    def save_hh_tokens(
        self,
        telegram_user_id: int,
        hh_user_id: str | None,
        hh_user_type: str | None,
        access_token_encrypted: str,
        refresh_token_encrypted: str,
        expires_at: str,
    ) -> None:
        now = utc_now_iso()
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO hh_tokens (
                    telegram_user_id, hh_user_id, hh_user_type, access_token_encrypted,
                    refresh_token_encrypted, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    hh_user_id = excluded.hh_user_id,
                    hh_user_type = excluded.hh_user_type,
                    access_token_encrypted = excluded.access_token_encrypted,
                    refresh_token_encrypted = excluded.refresh_token_encrypted,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (
                    telegram_user_id,
                    hh_user_id,
                    hh_user_type,
                    access_token_encrypted,
                    refresh_token_encrypted,
                    expires_at,
                    now,
                    now,
                ),
            )
        self.update_user_settings(telegram_user_id, hh_connected=1)

    def get_hh_token_row(self, telegram_user_id: int) -> sqlite3.Row | None:
        with closing(self.connect()) as connection:
            return connection.execute(
                "SELECT * FROM hh_tokens WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()

    def delete_hh_tokens(self, telegram_user_id: int) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute("DELETE FROM hh_tokens WHERE telegram_user_id = ?", (telegram_user_id,))
        self.update_user_settings(
            telegram_user_id,
            hh_connected=0,
            selected_resume_id=None,
            selected_resume_title=None,
            apply_mode="button",
            auto_acknowledged_at=None,
        )

    def upsert_vacancy_log(
        self,
        telegram_user_id: int,
        vacancy_id: str,
        status: str,
        vacancy_name: str | None = None,
        employer_id: str | None = None,
        employer_name: str | None = None,
        vacancy_url: str | None = None,
        score: int | None = None,
        skip_reason: str | None = None,
        apply_mode: str | None = None,
        cover_letter: str | None = None,
        error_type: str | None = None,
        error_value: str | None = None,
        applied_at: str | None = None,
    ) -> None:
        now = utc_now_iso()
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO vacancy_log (
                    telegram_user_id, vacancy_id, vacancy_name, employer_id, employer_name,
                    vacancy_url, score, status, skip_reason, apply_mode, cover_letter,
                    error_type, error_value, created_at, updated_at, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id, vacancy_id) DO UPDATE SET
                    vacancy_name = COALESCE(excluded.vacancy_name, vacancy_log.vacancy_name),
                    employer_id = COALESCE(excluded.employer_id, vacancy_log.employer_id),
                    employer_name = COALESCE(excluded.employer_name, vacancy_log.employer_name),
                    vacancy_url = COALESCE(excluded.vacancy_url, vacancy_log.vacancy_url),
                    score = COALESCE(excluded.score, vacancy_log.score),
                    status = excluded.status,
                    skip_reason = excluded.skip_reason,
                    apply_mode = excluded.apply_mode,
                    cover_letter = COALESCE(excluded.cover_letter, vacancy_log.cover_letter),
                    error_type = excluded.error_type,
                    error_value = excluded.error_value,
                    updated_at = excluded.updated_at,
                    applied_at = COALESCE(excluded.applied_at, vacancy_log.applied_at)
                """,
                (
                    telegram_user_id,
                    vacancy_id,
                    vacancy_name,
                    employer_id,
                    employer_name,
                    vacancy_url,
                    score,
                    status,
                    skip_reason,
                    apply_mode,
                    cover_letter,
                    error_type,
                    error_value,
                    now,
                    now,
                    applied_at,
                ),
            )

    def get_vacancy_log(self, telegram_user_id: int, vacancy_id: str) -> sqlite3.Row | None:
        with closing(self.connect()) as connection:
            return connection.execute(
                "SELECT * FROM vacancy_log WHERE telegram_user_id = ? AND vacancy_id = ?",
                (telegram_user_id, vacancy_id),
            ).fetchone()

    def recent_applications(self, telegram_user_id: int, limit: int = 20) -> list[sqlite3.Row]:
        with closing(self.connect()) as connection:
            return connection.execute(
                """
                SELECT * FROM vacancy_log
                WHERE telegram_user_id = ?
                  AND status IN ('queued', 'approved', 'applied', 'failed', 'skipped')
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (telegram_user_id, limit),
            ).fetchall()

    def add_to_queue(
        self,
        telegram_user_id: int,
        vacancy_id: str,
        resume_id: str,
        score: int,
        cover_letter: str,
        status: str = "pending",
        scheduled_at: str | None = None,
    ) -> None:
        now = utc_now_iso()
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO apply_queue (
                    telegram_user_id, vacancy_id, resume_id, score, cover_letter,
                    status, scheduled_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id, vacancy_id, resume_id) DO UPDATE SET
                    score = excluded.score,
                    cover_letter = excluded.cover_letter,
                    status = excluded.status,
                    scheduled_at = excluded.scheduled_at,
                    updated_at = excluded.updated_at
                """,
                (telegram_user_id, vacancy_id, resume_id, score, cover_letter, status, scheduled_at, now, now),
            )

    def queue_items(self, telegram_user_id: int, status: str = "pending", limit: int = 20) -> list[sqlite3.Row]:
        with closing(self.connect()) as connection:
            return connection.execute(
                """
                SELECT * FROM apply_queue
                WHERE telegram_user_id = ? AND status = ?
                ORDER BY score DESC, created_at ASC
                LIMIT ?
                """,
                (telegram_user_id, status, limit),
            ).fetchall()

    def due_queue_items(self, telegram_user_id: int, limit: int = 2) -> list[sqlite3.Row]:
        now = utc_now_iso()
        with closing(self.connect()) as connection:
            return connection.execute(
                """
                SELECT q.*
                FROM apply_queue q
                LEFT JOIN vacancy_log v
                  ON v.telegram_user_id = q.telegram_user_id AND v.vacancy_id = q.vacancy_id
                WHERE q.telegram_user_id = ?
                  AND q.status = 'pending'
                  AND (q.scheduled_at IS NULL OR q.scheduled_at <= ?)
                  AND COALESCE(v.apply_mode, '') = 'auto'
                ORDER BY q.scheduled_at ASC, q.score DESC
                LIMIT ?
                """,
                (telegram_user_id, now, limit),
            ).fetchall()

    def update_queue_status(self, queue_id: int, status: str) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "UPDATE apply_queue SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now_iso(), queue_id),
            )

    def is_employer_blacklisted(self, telegram_user_id: int, employer_id: str | None) -> bool:
        if not employer_id:
            return False
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM employer_blacklist WHERE telegram_user_id = ? AND employer_id = ?",
                (telegram_user_id, employer_id),
            ).fetchone()
        return row is not None

    def add_employer_blacklist(self, telegram_user_id: int, employer_id: str, employer_name: str | None) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO employer_blacklist (telegram_user_id, employer_id, employer_name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (telegram_user_id, employer_id, employer_name, utc_now_iso()),
            )

    def application_stats_today(self, telegram_user_id: int) -> dict[str, int]:
        today = utc_now_iso()[:10]
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT status, apply_mode, COUNT(*) AS count
                FROM vacancy_log
                WHERE telegram_user_id = ? AND substr(updated_at, 1, 10) = ?
                GROUP BY status, apply_mode
                """,
                (telegram_user_id, today),
            ).fetchall()
            found = connection.execute(
                "SELECT COUNT(*) FROM vacancy_log WHERE telegram_user_id = ? AND substr(created_at, 1, 10) = ?",
                (telegram_user_id, today),
            ).fetchone()[0]
        stats = {
            "found_today": found,
            "shown": 0,
            "hidden": 0,
            "queued": 0,
            "manual": 0,
            "semi_auto": 0,
            "auto": 0,
            "errors": 0,
        }
        for row in rows:
            if row["status"] == "shown":
                stats["shown"] += row["count"]
            elif row["status"] == "hidden":
                stats["hidden"] += row["count"]
            elif row["status"] == "queued":
                stats["queued"] += row["count"]
            elif row["status"] == "failed":
                stats["errors"] += row["count"]
            elif row["status"] == "applied" and row["apply_mode"] in stats:
                stats[row["apply_mode"]] += row["count"]
        return stats

    @staticmethod
    def _json_or_scalar(value: Any) -> Any:
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool):
            return 1 if value else 0
        return value

    def _values(self, vacancy: Vacancy, score: ScoreResult, now: str) -> tuple[Any, ...]:
        return (
            vacancy.source,
            vacancy.external_id,
            vacancy.title,
            vacancy.company,
            vacancy.salary.salary_from,
            vacancy.salary.salary_to,
            vacancy.salary.currency,
            vacancy.url,
            vacancy.area,
            vacancy.schedule,
            vacancy.experience,
            score.score,
            score.status,
            score.career_value,
            json.dumps(score.reasons_positive, ensure_ascii=False),
            json.dumps(score.reasons_negative, ensure_ascii=False),
            json.dumps(vacancy.raw, ensure_ascii=False),
            now,
            now,
        )

    def _update_values(self, vacancy: Vacancy, score: ScoreResult, now: str) -> tuple[Any, ...]:
        return (
            vacancy.title,
            vacancy.company,
            vacancy.salary.salary_from,
            vacancy.salary.salary_to,
            vacancy.salary.currency,
            vacancy.url,
            vacancy.area,
            vacancy.schedule,
            vacancy.experience,
            score.score,
            score.status,
            score.career_value,
            json.dumps(score.reasons_positive, ensure_ascii=False),
            json.dumps(score.reasons_negative, ensure_ascii=False),
            json.dumps(vacancy.raw, ensure_ascii=False),
            now,
        )
