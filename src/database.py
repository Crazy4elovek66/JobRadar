from __future__ import annotations

from contextlib import closing
import json
import sqlite3
from pathlib import Path
from typing import Any

from src.models import ScoreResult, Vacancy
from src.utils import utc_now_iso


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
