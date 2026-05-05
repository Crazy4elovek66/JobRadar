from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urlencode

from aiohttp import web
from aiogram import Bot

from src.config import Settings
from src.database import Database
from src.hh_client import HHApiError, HHClient
from src.search_service import SearchService


logger = logging.getLogger(__name__)


def create_web_app(settings: Settings, db: Database, hh_client: HHClient, bot: Bot, search_service: SearchService) -> web.Application:
    app = web.Application()
    app["settings"] = settings
    app["db"] = db
    app["hh_client"] = hh_client
    app["bot"] = bot
    app["search_service"] = search_service
    app.router.add_get("/", root)
    app.router.add_get("/auth/hh/start", hh_start)
    app.router.add_get("/auth/hh/callback", hh_callback)
    app.router.add_get("/api/cron/search", cron_search)
    app.router.add_get("/cron/search", cron_search)
    return app


async def root(request: web.Request) -> web.Response:
    return html_page("Бэкенд JobRadar запущен", "Для авторизации перейдите в бота.")


async def hh_start(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    telegram_user_id = request.query.get("telegramUserId") or request.query.get("telegram_user_id")
    if not telegram_user_id:
        return html_page("Не хватает Telegram ID", "Открой подключение HH из Telegram-бота JobRadar.")
    if not settings.hh_client_id or not settings.hh_redirect_uri:
        return html_page("HH не настроен", "В переменных окружения нужны идентификатор приложения HH и адрес возврата.")
    state = sign_state(settings, {"telegram_user_id": int(telegram_user_id), "ts": int(time.time())})
    url = f"{settings.hh_oauth_authorize_url}?{urlencode({'response_type': 'code', 'client_id': settings.hh_client_id, 'redirect_uri': settings.hh_redirect_uri, 'state': state})}"
    raise web.HTTPFound(url)


async def hh_callback(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    db: Database = request.app["db"]
    hh_client: HHClient = request.app["hh_client"]
    bot: Bot = request.app["bot"]
    code = request.query.get("code")
    state = request.query.get("state")
    if not code or not state:
        return html_page("HH не подключен", "HH не вернул код авторизации. Попробуй начать подключение заново.")
    try:
        state_payload = verify_state(settings, state)
        telegram_user_id = int(state_payload["telegram_user_id"])
        token_payload = await hh_client.exchange_code(code)
        me = await hh_client.save_oauth_tokens(telegram_user_id, token_payload)
        db.update_user_settings(telegram_user_id, hh_connected=1)
        await bot.send_message(telegram_user_id, "HH успешно подключен. Теперь можно выбрать резюме и запускать отклики.")
        try:
            bot_info = await bot.get_me()
            redirect_url = f"tg://resolve?domain={bot_info.username}" if bot_info.username else None
        except Exception:
            logger.exception("Telegram bot info lookup failed after HH OAuth callback")
            redirect_url = None
        return html_page(
            "HH успешно подключен",
            "Ваш аккаунт HeadHunter привязан к JobRadar. Сейчас мы вернём вас в Telegram...",
            redirect_url=redirect_url,
        )
    except Exception:
        logger.exception("HH OAuth callback failed")
        return html_page("HH не подключен", "Не удалось завершить авторизацию. Проверь настройки приложения HH и попробуй ещё раз.")


async def cron_search(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    db: Database = request.app["db"]
    bot: Bot = request.app["bot"]
    search_service: SearchService = request.app["search_service"]
    secret = request.query.get("secret") or request.headers.get("X-JobRadar-Secret")
    if not settings.hh_auto_worker_secret or secret != settings.hh_auto_worker_secret:
        raise web.HTTPUnauthorized(text="Секрет cron не совпал.")
    user_settings = db.ensure_user_settings(settings.telegram_user_id)
    if not user_settings["search_enabled"]:
        return web.json_response({"ok": True, "message": "Автопоиск выключен."})
    try:
        summary = await search_service.run(bot, settings.telegram_user_id)
    except HHApiError as exc:
        return web.json_response({"ok": False, "error": exc.error_value or exc.error_type or "hh_error"}, status=502)
    if summary is None:
        return web.json_response({"ok": True, "message": "Поиск уже выполняется."})
    return web.json_response(
        {
            "ok": True,
            "found": summary.found,
            "saved": summary.saved,
            "sent": summary.sent,
            "queued": summary.queued,
            "auto_applied": summary.auto_applied,
        }
    )


def sign_state(settings: Settings, payload: dict[str, int]) -> str:
    secret = settings.oauth_state_secret or settings.hh_auto_worker_secret
    if not secret:
        raise RuntimeError("Для защиты OAuth нужен секрет подписи в переменных окружения.")
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), raw.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{raw}.{signature}"


def verify_state(settings: Settings, state: str) -> dict[str, int]:
    secret = settings.oauth_state_secret or settings.hh_auto_worker_secret
    if not secret:
        raise RuntimeError("Для защиты OAuth нужен секрет подписи в переменных окружения.")
    raw, signature = state.split(".", 1)
    expected = hmac.new(secret.encode("utf-8"), raw.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Подпись OAuth-состояния не совпала.")
    payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
    if int(time.time()) - int(payload.get("ts", 0)) > 15 * 60:
        raise ValueError("OAuth-состояние устарело.")
    return payload


def html_page(title: str, text: str, redirect_url: str | None = None) -> web.Response:
    safe_title = title.replace("<", "&lt;").replace(">", "&gt;")
    safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
    redirect_button = ""
    redirect_script = ""
    if redirect_url:
        safe_redirect_url = redirect_url.replace("&", "&amp;").replace("\"", "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        redirect_button = f"<a class=\"button\" href=\"{safe_redirect_url}\">Вернуться в Telegram</a>"
        redirect_script = f"<script>setTimeout(() => window.location.href = {json.dumps(redirect_url)}, 2000);</script>"
    return web.Response(
        text=(
            "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{safe_title}</title>"
            "<style>body{margin:0;font-family:Inter,Arial,sans-serif;background:#0f172a;color:#f8fafc;display:grid;min-height:100vh;place-items:center}"
            "main{max-width:640px;padding:32px}h1{font-size:34px;margin:0 0 14px}p{font-size:18px;line-height:1.55;color:#cbd5e1}"
            ".button{display:inline-flex;align-items:center;justify-content:center;margin-top:18px;padding:13px 22px;border-radius:12px;background:#2563eb;color:#fff;text-decoration:none;font-weight:700;box-shadow:0 16px 36px rgba(37,99,235,.28)}"
            ".button:hover{background:#1d4ed8}</style>"
            f"</head><body><main><h1>{safe_title}</h1><p>{safe_text}</p>{redirect_button}</main>{redirect_script}</body></html>"
        ),
        content_type="text/html",
    )
