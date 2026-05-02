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
    hh_host: str = "hh.ru"
    hh_user_agent: str = "JobRadar/1.0 (email@example.com)"
    hh_client_id: str | None = None
    hh_client_secret: str | None = None
    hh_access_token: str | None = None
    hh_redirect_uri: str | None = None
    hh_proxy: str | None = None
    hh_proxies: tuple[str, ...] = ()
    hh_proxy_file: Path = BASE_DIR / "good_proxies.txt"
    telegram_proxy: str | None = None
    search_interval_minutes: int = 180
    min_score_to_send: int = 60
    database_path: Path = BASE_DIR / "data" / "jobradar.db"


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env", override=True)

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

    hh_proxy = os.getenv("HH_PROXY", "").strip() or None
    hh_proxy_file_raw = os.getenv("HH_PROXY_FILE", "good_proxies.txt").strip()
    hh_proxy_file = Path(hh_proxy_file_raw)
    if not hh_proxy_file.is_absolute():
        hh_proxy_file = BASE_DIR / hh_proxy_file

    hh_proxies = _load_hh_proxies(hh_proxy=hh_proxy, hh_proxy_file=hh_proxy_file)

    return Settings(
        telegram_bot_token=token,
        telegram_user_id=user_id,
        hh_area=os.getenv("HH_AREA", "113").strip() or "113",
        hh_host=os.getenv("HH_HOST", "hh.ru").strip() or "hh.ru",
        hh_user_agent=os.getenv("HH_USER_AGENT", "JobRadar/1.0 (email@example.com)").strip()
        or "JobRadar/1.0 (email@example.com)",
        hh_client_id=os.getenv("HH_CLIENT_ID", "").strip() or None,
        hh_client_secret=os.getenv("HH_CLIENT_SECRET", "").strip() or None,
        hh_access_token=os.getenv("HH_ACCESS_TOKEN", "").strip() or None,
        hh_redirect_uri=os.getenv("HH_REDIRECT_URI", "").strip() or None,
        hh_proxy=hh_proxy,
        hh_proxies=hh_proxies,
        hh_proxy_file=hh_proxy_file,
        telegram_proxy=os.getenv("TELEGRAM_PROXY", "").strip() or None,
        search_interval_minutes=int(os.getenv("SEARCH_INTERVAL_MINUTES", "180")),
        min_score_to_send=int(os.getenv("MIN_SCORE_TO_SEND", "60")),
        database_path=db_path,
    )


def _load_hh_proxies(hh_proxy: str | None, hh_proxy_file: Path) -> tuple[str, ...]:
    raw_values: list[str] = []
    if hh_proxy:
        raw_values.append(hh_proxy)

    env_pool = os.getenv("HH_PROXIES", "")
    for separator in (";", "\n"):
        env_pool = env_pool.replace(separator, ",")
    raw_values.extend(part.strip() for part in env_pool.split(",") if part.strip())

    if hh_proxy_file.exists():
        raw_values.extend(
            line.strip()
            for line in hh_proxy_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )

    proxies: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        proxy = _normalize_proxy_url(value)
        if proxy not in seen:
            proxies.append(proxy)
            seen.add(proxy)
    return tuple(proxies)


def _normalize_proxy_url(value: str) -> str:
    value = value.strip()
    if "://" in value:
        return value
    return f"http://{value}"
