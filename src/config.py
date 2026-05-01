from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    telegram_user_id: int
    hh_area: str = "113"
    hh_proxy: str | None = None
    search_interval_minutes: int = 180
    min_score_to_send: int = 60
    database_path: Path = BASE_DIR / "data" / "jobradar.db"


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    user_id_raw = os.getenv("TELEGRAM_USER_ID", "").strip()
    if not token:
        raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN в .env")
    if not user_id_raw:
        raise RuntimeError("Не задан TELEGRAM_USER_ID в .env")

    try:
        user_id = int(user_id_raw)
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_USER_ID должен быть числом") from exc

    db_path_raw = os.getenv("DATABASE_PATH", "data/jobradar.db").strip()
    db_path = Path(db_path_raw)
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    return Settings(
        telegram_bot_token=token,
        telegram_user_id=user_id,
        hh_area=os.getenv("HH_AREA", "113").strip() or "113",
        hh_proxy=os.getenv("HH_PROXY", "").strip() or None,
        search_interval_minutes=int(os.getenv("SEARCH_INTERVAL_MINUTES", "180")),
        min_score_to_send=int(os.getenv("MIN_SCORE_TO_SEND", "60")),
        database_path=db_path,
    )
