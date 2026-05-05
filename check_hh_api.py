from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_API_URL = "https://api.hh.ru"
IPIFY_URL = "https://api.ipify.org"
IP_API_URL = "http://ip-api.com/json/{ip}"


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def normalize_proxy(proxy: str) -> str | None:
    proxy = proxy.strip()
    if not proxy:
        return None
    if "://" in proxy:
        return proxy
    return f"http://{proxy}"


def mask_proxy(proxy: str | None) -> str:
    if not proxy:
        return "прямое соединение"
    parts = urlsplit(proxy)
    if not parts.hostname:
        return "прокси скрыт"
    netloc = parts.hostname
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, "", "", ""))


def load_hh_proxies() -> tuple[str, ...]:
    raw_values: list[str] = []
    if hh_proxy := env("HH_PROXY"):
        raw_values.append(hh_proxy)

    env_pool = env("HH_PROXIES")
    for separator in (";", "\n"):
        env_pool = env_pool.replace(separator, ",")
    raw_values.extend(part.strip() for part in env_pool.split(",") if part.strip())

    proxy_file_raw = env("HH_PROXY_FILE", "good_proxies.txt") or "good_proxies.txt"
    proxy_file = Path(proxy_file_raw)
    if not proxy_file.is_absolute():
        proxy_file = BASE_DIR / proxy_file
    if proxy_file.exists():
        raw_values.extend(
            line.strip()
            for line in proxy_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )

    proxies: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        proxy = normalize_proxy(value)
        if proxy and proxy not in seen:
            proxies.append(proxy)
            seen.add(proxy)
    return tuple(proxies)


async def fetch_json(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    proxy: str | None = None,
    timeout: int = 20,
) -> tuple[int, dict[str, Any] | str]:
    async with session.request(
        method,
        url,
        params=params,
        proxy=proxy,
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as response:
        text = await response.text()
        try:
            return response.status, await response.json()
        except Exception:
            return response.status, text[:800]


async def get_route_info(session: aiohttp.ClientSession, proxy: str | None) -> dict[str, Any]:
    status, ip_payload = await fetch_json(
        session,
        "GET",
        IPIFY_URL,
        params={"format": "json"},
        proxy=proxy,
        timeout=12,
    )
    if status >= 400 or not isinstance(ip_payload, dict):
        return {"ok": False, "error": f"не удалось определить внешний IP: HTTP {status}"}

    ip = str(ip_payload.get("ip") or "").strip()
    if not ip:
        return {"ok": False, "error": "сервис определения IP вернул пустой адрес"}

    status, geo_payload = await fetch_json(
        session,
        "GET",
        IP_API_URL.format(ip=ip),
        params={"fields": "status,message,country,regionName,city,query,isp,org,proxy,hosting,mobile,timezone"},
        proxy=proxy,
        timeout=12,
    )
    if status >= 400 or not isinstance(geo_payload, dict):
        return {"ok": True, "ip": ip, "geo_error": f"геобаза недоступна: HTTP {status}"}
    if geo_payload.get("status") == "fail":
        return {"ok": True, "ip": ip, "geo_error": geo_payload.get("message") or "геобаза не распознала IP"}

    return {"ok": True, "ip": ip, **geo_payload}


def print_route_info(title: str, proxy: str | None, route: dict[str, Any]) -> None:
    print(f"\n== {title} ==")
    print(f"Маршрут: {mask_proxy(proxy)}")
    if not route.get("ok"):
        print(f"Ошибка: {route.get('error')}")
        return

    location = ", ".join(
        part
        for part in (
            route.get("country"),
            route.get("regionName"),
            route.get("city"),
        )
        if part
    )
    print(f"Внешний IP: {route.get('ip') or route.get('query')}")
    print(f"Регион выхода: {location or 'не определён'}")
    if route.get("isp") or route.get("org"):
        print(f"Провайдер/организация: {route.get('isp') or 'не указано'} / {route.get('org') or 'не указано'}")
    flags = []
    if route.get("proxy"):
        flags.append("proxy/VPN")
    if route.get("hosting"):
        flags.append("hosting/DC")
    if route.get("mobile"):
        flags.append("mobile")
    print(f"Признаки маршрута: {', '.join(flags) if flags else 'похоже на обычное соединение'}")
    if route.get("geo_error"):
        print(f"Геобаза: {route['geo_error']}")


def print_result(title: str, status: int, payload: dict[str, Any] | str) -> None:
    print(f"\n== {title} ==")
    print(f"HTTP {status}")
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if errors:
            print(f"Ошибки HH: {errors}")
            print(explain_errors(errors))
            return
        if "items" in payload:
            print(f"Получено элементов: {len(payload.get('items') or [])}")
            print(f"Всего найдено по версии HH: {payload.get('found', 'не указано')}")
            return
        print(payload)
        return
    print(payload)


def explain_errors(errors: Any) -> str:
    if not isinstance(errors, list):
        return "Не удалось разобрать ошибку HH."

    explanations: list[str] = []
    for error in errors:
        if not isinstance(error, dict):
            continue
        error_type = error.get("type")
        value = error.get("value")
        if error_type == "bad_user_agent":
            explanations.append("Проверь HH_USER_AGENT: он должен быть передан и не должен быть шаблонным.")
        elif error_type == "bad_argument":
            explanations.append(f"Некорректный параметр запроса: {value or 'не указан'}.")
        elif error_type == "oauth":
            explanations.append(f"Проблема с OAuth-токеном: {value or 'без уточнения'}.")
        elif error_type == "captcha_required":
            explanations.append("HH требует капчу. Нужно снизить частоту запросов или проверить сетевой маршрут.")
        elif error_type == "forbidden":
            explanations.append("HH запретил доступ к методу с текущего маршрута или клиента.")
        else:
            explanations.append(f"Неизвестный тип ошибки: {error_type}, значение: {value}.")
    return "\n".join(explanations) or "Нет подробного объяснения."


async def check_hh(
    session: aiohttp.ClientSession,
    *,
    api_url: str,
    hh_host: str,
    hh_area: str,
    access_token: str,
    proxy: str | None,
) -> None:
    status, payload = await fetch_json(
        session,
        "GET",
        f"{api_url}/vacancies",
        params={"host": hh_host, "text": "python", "area": hh_area, "per_page": 5},
        proxy=proxy,
    )
    print_result(f"GET /vacancies через {mask_proxy(proxy)}", status, payload)

    if access_token:
        status, payload = await fetch_json(session, "GET", f"{api_url}/me", proxy=proxy)
        print_result(f"GET /me через {mask_proxy(proxy)}", status, payload)
    else:
        print("\n== GET /me ==")
        print("Пропущено: HH_ACCESS_TOKEN не задан.")


async def main() -> None:
    load_dotenv(BASE_DIR / ".env", override=True)

    api_url = (env("HH_API_BASE", DEFAULT_API_URL) or DEFAULT_API_URL).rstrip("/")
    hh_host = env("HH_HOST", "hh.ru") or "hh.ru"
    hh_area = env("HH_AREA", "113") or "113"
    user_agent = env("HH_USER_AGENT", "JobRadar/1.0 (email@example.com)") or "JobRadar/1.0 (email@example.com)"
    access_token = env("HH_ACCESS_TOKEN")
    proxies = load_hh_proxies()
    route_limit = max(1, int(env("CHECK_HH_ROUTE_LIMIT", "3") or "3"))

    headers = {
        "User-Agent": user_agent,
        "HH-User-Agent": user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    print("Проверка HH API для JobRadar")
    print(f"HH_API_BASE={api_url}")
    print(f"HH_HOST={hh_host}")
    print(f"HH_AREA={hh_area}")
    print(f"HH_USER_AGENT={user_agent}")
    print(f"HH_ACCESS_TOKEN={'задан' if access_token else 'не задан'}")
    print(f"HH-прокси загружено: {len(proxies)}")
    if proxies:
        print("Важно: основной бот будет ходить в HH через загруженные прокси, а не через чистое прямое соединение.")

    async with aiohttp.ClientSession(headers=headers) as session:
        direct_route = await get_route_info(session, proxy=None)
        print_route_info("Прямой выход в интернет", None, direct_route)

        candidates: list[str | None] = list(proxies[:route_limit]) if proxies else [None]
        if len(proxies) > route_limit:
            print(f"\nПроверяю первые {route_limit} прокси из {len(proxies)}. Лимит можно изменить через CHECK_HH_ROUTE_LIMIT.")

        for proxy in candidates:
            if proxy:
                route = await get_route_info(session, proxy=proxy)
                print_route_info("Выход через HH-прокси", proxy, route)
            await check_hh(
                session,
                api_url=api_url,
                hh_host=hh_host,
                hh_area=hh_area,
                access_token=access_token,
                proxy=proxy,
            )


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
