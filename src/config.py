from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_HH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    telegram_user_id: int
    hh_area: str = "113"
    hh_host: str = "hh.ru"
    hh_user_agent: str = DEFAULT_HH_USER_AGENT
    hh_session_cookie: str | None = None
    hh_auto_worker_secret: str | None = None
    app_base_url: str = "http://localhost:8080"
    web_server_host: str = "0.0.0.0"
    web_server_port: int = 8080
    hh_proxy: str | None = None
    hh_proxies: tuple[str, ...] = ()
    hh_proxy_file: Path = BASE_DIR / "good_proxies.txt"
    hh_proxy_mode: str = "direct_then_proxy"
    telegram_proxy: str | None = None
    search_interval_minutes: int = 180
    min_score_to_send: int = 60
    database_path: Path = BASE_DIR / "data" / "jobradar.db"
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemini-2.5-flash-lite"
    extension_endpoint_secret: str | None = None
    disable_hh_ssl_verification: bool = False
    extension_only: bool = False





def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env", override=True)

    extension_only = os.getenv("EXTENSION_ONLY", "false").lower() == "true"

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    user_id_raw = os.getenv("TELEGRAM_USER_ID", "").strip()
    
    if not extension_only:
        if not token:
            raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN в .env")
        if not user_id_raw:
            raise RuntimeError("Не задан TELEGRAM_USER_ID в .env")

    user_id = 0
    if user_id_raw:
        try:
            user_id = int(user_id_raw)
        except ValueError as exc:
            if not extension_only:
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
    hh_proxy_mode = os.getenv("HH_PROXY_MODE", "direct_then_proxy").strip() or "direct_then_proxy"
    if hh_proxy_mode not in {"direct_only", "proxy_only", "proxy_then_direct", "direct_then_proxy"}:
        raise RuntimeError("HH_PROXY_MODE должен быть direct_only, proxy_only, proxy_then_direct или direct_then_proxy")

    return Settings(
        telegram_bot_token=token,
        telegram_user_id=user_id,
        hh_area=os.getenv("HH_AREA", "113").strip() or "113",
        hh_host=os.getenv("HH_HOST", "hh.ru").strip() or "hh.ru",
        hh_user_agent=_browser_user_agent(os.getenv("HH_USER_AGENT", "")),
        hh_session_cookie=os.getenv("HH_SESSION_COOKIE", "").strip() or None,
        hh_auto_worker_secret=os.getenv("HH_AUTO_WORKER_SECRET", "").strip() or None,
        app_base_url=os.getenv("APP_BASE_URL", "http://localhost:8080").strip().rstrip("/") or "http://localhost:8080",
        web_server_host=os.getenv("WEB_SERVER_HOST", "0.0.0.0").strip() or "0.0.0.0",
        web_server_port=int(os.getenv("WEB_SERVER_PORT", "8080")),
        hh_proxy=hh_proxy,
        hh_proxies=hh_proxies,
        hh_proxy_file=hh_proxy_file,
        hh_proxy_mode=hh_proxy_mode,
        telegram_proxy=os.getenv("TELEGRAM_PROXY", "").strip() or None,
        search_interval_minutes=int(os.getenv("SEARCH_INTERVAL_MINUTES", "180")),
        min_score_to_send=int(os.getenv("MIN_SCORE_TO_SEND", "60")),
        database_path=db_path,
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip() or None,
        openrouter_model=os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite").strip() or "google/gemini-2.5-flash-lite",
        extension_endpoint_secret=os.getenv("EXTENSION_ENDPOINT_SECRET", "").strip() or None,
        disable_hh_ssl_verification=os.getenv("DISABLE_HH_SSL_VERIFICATION", "false").lower() == "true",
        extension_only=extension_only,
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


def _browser_user_agent(value: str) -> str:
    value = value.strip()
    if not value or value.lower().startswith("jobradar/"):
        return DEFAULT_HH_USER_AGENT
    return value
