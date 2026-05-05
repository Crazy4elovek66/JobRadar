from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from typing import Any

from src.database import Database


logger = logging.getLogger(__name__)


ColumnSpec = tuple[str, str]


REQUIRED_COLUMNS: dict[str, tuple[ColumnSpec, ...]] = {
    "vacancies": (
        ("source", "TEXT NOT NULL DEFAULT 'hh'"),
        ("external_id", "TEXT NOT NULL DEFAULT ''"),
        ("title", "TEXT NOT NULL DEFAULT 'Без названия'"),
        ("company", "TEXT"),
        ("salary_from", "INTEGER"),
        ("salary_to", "INTEGER"),
        ("currency", "TEXT"),
        ("url", "TEXT"),
        ("area", "TEXT"),
        ("schedule", "TEXT"),
        ("experience", "TEXT"),
        ("score", "INTEGER"),
        ("status", "TEXT"),
        ("career_value", "INTEGER"),
        ("reasons_positive", "TEXT"),
        ("reasons_negative", "TEXT"),
        ("raw_json", "TEXT"),
        ("first_seen_at", "TEXT NOT NULL DEFAULT ''"),
        ("last_seen_at", "TEXT NOT NULL DEFAULT ''"),
        ("sent_at", "TEXT"),
        ("is_favorite", "INTEGER NOT NULL DEFAULT 0"),
        ("is_rejected_by_user", "INTEGER NOT NULL DEFAULT 0"),
    ),
    "hh_tokens": (
        ("telegram_user_id", "INTEGER NOT NULL DEFAULT 0"),
        ("hh_user_id", "TEXT"),
        ("hh_user_type", "TEXT"),
        ("access_token_encrypted", "TEXT NOT NULL DEFAULT ''"),
        ("refresh_token_encrypted", "TEXT NOT NULL DEFAULT ''"),
        ("expires_at", "TEXT NOT NULL DEFAULT ''"),
        ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ("updated_at", "TEXT NOT NULL DEFAULT ''"),
    ),
    "user_settings": (
        ("hh_connected", "INTEGER NOT NULL DEFAULT 0"),
        ("selected_resume_id", "TEXT"),
        ("selected_resume_title", "TEXT"),
        ("search_enabled", "INTEGER NOT NULL DEFAULT 1"),
        ("search_interval_minutes", "INTEGER NOT NULL DEFAULT 60"),
        ("apply_mode", "TEXT NOT NULL DEFAULT 'button'"),
        ("min_score_for_show", "INTEGER NOT NULL DEFAULT 55"),
        ("min_score_for_semi_auto", "INTEGER NOT NULL DEFAULT 70"),
        ("min_score_for_auto", "INTEGER NOT NULL DEFAULT 85"),
        ("auto_daily_limit", "INTEGER NOT NULL DEFAULT 5"),
        ("auto_run_limit", "INTEGER NOT NULL DEFAULT 2"),
        ("auto_delay_min_minutes", "INTEGER NOT NULL DEFAULT 7"),
        ("auto_delay_max_minutes", "INTEGER NOT NULL DEFAULT 25"),
        ("auto_stop_on_error", "INTEGER NOT NULL DEFAULT 1"),
        ("auto_skip_has_test", "INTEGER NOT NULL DEFAULT 1"),
        ("auto_skip_external_response_url", "INTEGER NOT NULL DEFAULT 1"),
        ("auto_skip_required_letter", "INTEGER NOT NULL DEFAULT 0"),
        ("require_preview_before_apply", "INTEGER NOT NULL DEFAULT 1"),
        ("test_mode", "INTEGER NOT NULL DEFAULT 1"),
        ("areas", "TEXT NOT NULL DEFAULT '[\"113\"]'"),
        ("only_remote", "INTEGER NOT NULL DEFAULT 1"),
        ("salary_from", "INTEGER"),
        ("keywords", "TEXT NOT NULL DEFAULT '[]'"),
        ("negative_keywords", "TEXT NOT NULL DEFAULT '[]'"),
        ("positive_keywords", "TEXT NOT NULL DEFAULT '[]'"),
        ("portfolio_url", "TEXT"),
        ("cover_letter_template", "TEXT"),
        ("auto_acknowledged_at", "TEXT"),
        ("last_search_at", "TEXT"),
        ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ("updated_at", "TEXT NOT NULL DEFAULT ''"),
    ),
    "vacancy_log": (
        ("telegram_user_id", "INTEGER NOT NULL DEFAULT 0"),
        ("vacancy_id", "TEXT NOT NULL DEFAULT ''"),
        ("vacancy_name", "TEXT"),
        ("employer_id", "TEXT"),
        ("employer_name", "TEXT"),
        ("vacancy_url", "TEXT"),
        ("score", "INTEGER"),
        ("status", "TEXT NOT NULL DEFAULT 'found'"),
        ("skip_reason", "TEXT"),
        ("apply_mode", "TEXT"),
        ("cover_letter", "TEXT"),
        ("error_type", "TEXT"),
        ("error_value", "TEXT"),
        ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ("updated_at", "TEXT NOT NULL DEFAULT ''"),
        ("applied_at", "TEXT"),
    ),
    "apply_queue": (
        ("telegram_user_id", "INTEGER NOT NULL DEFAULT 0"),
        ("vacancy_id", "TEXT NOT NULL DEFAULT ''"),
        ("resume_id", "TEXT NOT NULL DEFAULT ''"),
        ("score", "INTEGER"),
        ("cover_letter", "TEXT"),
        ("status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("scheduled_at", "TEXT"),
        ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ("updated_at", "TEXT NOT NULL DEFAULT ''"),
    ),
    "employer_blacklist": (
        ("telegram_user_id", "INTEGER NOT NULL DEFAULT 0"),
        ("employer_id", "TEXT NOT NULL DEFAULT ''"),
        ("employer_name", "TEXT"),
        ("created_at", "TEXT NOT NULL DEFAULT ''"),
    ),
}


UNIQUE_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("idx_vacancies_source_external_id", "vacancies", ("source", "external_id")),
    ("idx_hh_tokens_telegram_user_id", "hh_tokens", ("telegram_user_id",)),
    ("idx_vacancy_log_user_vacancy", "vacancy_log", ("telegram_user_id", "vacancy_id")),
    ("idx_apply_queue_user_vacancy_resume", "apply_queue", ("telegram_user_id", "vacancy_id", "resume_id")),
    ("idx_employer_blacklist_user_employer", "employer_blacklist", ("telegram_user_id", "employer_id")),
)


def migrate_database(db: Database) -> None:
    """Bring an existing SQLite database up to the current JobRadar schema.

    `CREATE TABLE IF NOT EXISTS` does not update tables created by older versions of
    the bot. These lightweight migrations keep existing data and add the columns used
    by the current HH OAuth, resume selection, search log and apply queue flows.
    """

    with closing(db.connect()) as connection, connection:
        for table_name, columns in REQUIRED_COLUMNS.items():
            if not _table_exists(connection, table_name):
                logger.warning("Table %s does not exist during migration; Database.init() should create it first", table_name)
                continue
            _add_missing_columns(connection, table_name, columns)

        _fill_empty_timestamps(connection)
        _create_unique_indexes(connection)


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _existing_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({_quote_identifier(table_name)})").fetchall()
    return {str(row[1]) for row in rows}


def _add_missing_columns(connection: sqlite3.Connection, table_name: str, columns: Iterable[ColumnSpec]) -> None:
    existing = _existing_columns(connection, table_name)
    for column_name, column_ddl in columns:
        if column_name in existing:
            continue
        logger.info("Migrating SQLite table %s: adding column %s", table_name, column_name)
        connection.execute(
            f"ALTER TABLE {_quote_identifier(table_name)} ADD COLUMN {_quote_identifier(column_name)} {column_ddl}"
        )
        existing.add(column_name)


def _fill_empty_timestamps(connection: sqlite3.Connection) -> None:
    for table_name in ("vacancies", "hh_tokens", "user_settings", "vacancy_log", "apply_queue", "employer_blacklist"):
        columns = _existing_columns(connection, table_name)
        for column_name in ("created_at", "updated_at", "first_seen_at", "last_seen_at"):
            if column_name in columns:
                connection.execute(
                    f"UPDATE {_quote_identifier(table_name)} SET {_quote_identifier(column_name)} = datetime('now') "
                    f"WHERE {_quote_identifier(column_name)} IS NULL OR {_quote_identifier(column_name)} = ''"
                )


def _create_unique_indexes(connection: sqlite3.Connection) -> None:
    for index_name, table_name, columns in UNIQUE_INDEXES:
        if not _table_exists(connection, table_name):
            continue
        existing = _existing_columns(connection, table_name)
        if any(column not in existing for column in columns):
            continue
        try:
            column_sql = ", ".join(_quote_identifier(column) for column in columns)
            connection.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {_quote_identifier(index_name)} "
                f"ON {_quote_identifier(table_name)} ({column_sql})"
            )
        except sqlite3.IntegrityError:
            logger.warning(
                "Could not create unique index %s because duplicate rows already exist. "
                "The bot will still start, but old duplicate rows should be cleaned manually.",
                index_name,
            )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
