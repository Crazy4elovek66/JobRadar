from __future__ import annotations

import json
import logging

from aiohttp import web
from aiogram import Bot

from src.config import Settings
from src.database import Database
from src.hh_client import HHApiError, HHClient
from src.extension_service import analyze_vacancy
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
    app.router.add_get("/api/cron/search", cron_search)
    app.router.add_get("/cron/search", cron_search)
    app.router.add_post("/api/extension/analyze", handle_extension_analyze)
    return app


async def root(request: web.Request) -> web.Response:
    return html_page("Бэкенд JobRadar запущен", "HH работает через сессионные cookie из .env. OAuth-маршруты отключены.")


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

async def handle_extension_analyze(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    secret = settings.extension_endpoint_secret
    if not secret or request.headers.get("X-Extension-Secret") != secret:
        return web.json_response({"error": "forbidden"}, status=403)
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)
    if not payload.get("hh_vacancy_id"):
        return web.json_response({"error": "hh_vacancy_id required"}, status=400)
    db: Database = request.app["db"]
    result = await analyze_vacancy(db, settings, payload)
    return web.json_response(result)

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
