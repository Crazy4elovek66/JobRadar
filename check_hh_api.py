from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from bs4 import BeautifulSoup
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
HH_BASE_URL = "https://hh.ru"
IPIFY_URL = "https://api.ipify.org"
IP_API_URL = "http://ip-api.com/json/{ip}"
DEFAULT_HH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass(slots=True)
class CheckResult:
    route: str
    name: str
    ok: bool
    status: int | None
    details: str = ""


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
        return "direct"
    parts = urlsplit(proxy)
    if not parts.hostname:
        return "proxy"
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


def html_headers() -> dict[str, str]:
    user_agent = browser_user_agent(env("HH_USER_AGENT"))
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.6,en;q=0.4",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }
    if cookie := env("HH_SESSION_COOKIE"):
        headers["Cookie"] = cookie
    return headers


def browser_user_agent(value: str) -> str:
    value = value.strip()
    if not value or value.lower().startswith("jobradar/"):
        return DEFAULT_HH_USER_AGENT
    return value


async def fetch_text(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    proxy: str | None = None,
    timeout: int = 20,
) -> tuple[int | None, str, str]:
    try:
        async with session.get(
            url,
            params=params,
            proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
        ) as response:
            return response.status, str(response.url), await response.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return None, url, str(exc)


async def get_route_info(session: aiohttp.ClientSession, proxy: str | None) -> dict[str, Any]:
    status, _, text = await fetch_text(session, IPIFY_URL, params={"format": "json"}, proxy=proxy, timeout=12)
    if status != 200:
        return {"ok": False, "error": f"не удалось определить внешний IP: {status or text}"}
    try:
        import json

        ip = str(json.loads(text).get("ip") or "").strip()
    except Exception:
        return {"ok": False, "error": "сервис определения IP вернул неожиданный ответ"}
    if not ip:
        return {"ok": False, "error": "сервис определения IP вернул пустой адрес"}

    status, _, geo_text = await fetch_text(
        session,
        IP_API_URL.format(ip=ip),
        params={"fields": "status,message,country,regionName,city,query,isp,org,proxy,hosting,mobile,timezone"},
        proxy=proxy,
        timeout=12,
    )
    if status != 200:
        return {"ok": True, "ip": ip, "geo_error": f"геобаза недоступна: {status or geo_text}"}
    try:
        import json

        geo = json.loads(geo_text)
    except Exception:
        return {"ok": True, "ip": ip, "geo_error": "геобаза вернула неожиданный ответ"}
    if geo.get("status") == "fail":
        return {"ok": True, "ip": ip, "geo_error": geo.get("message") or "геобаза не распознала IP"}
    return {"ok": True, "ip": ip, **geo}


def print_route_info(proxy: str | None, route: dict[str, Any]) -> None:
    print(f"\n== Маршрут {mask_proxy(proxy)} ==")
    if not route.get("ok"):
        print(f"IP: ошибка, {route.get('error')}")
        return
    location = ", ".join(str(route.get(key)) for key in ("country", "regionName", "city") if route.get(key))
    print(f"Внешний IP: {route.get('ip') or route.get('query')}")
    print(f"Регион выхода: {location or 'не определён'}")
    print(f"Провайдер/организация: {route.get('isp') or 'не указано'} / {route.get('org') or 'не указано'}")
    flags = []
    if route.get("proxy"):
        flags.append("proxy/VPN")
    if route.get("hosting"):
        flags.append("hosting/DC")
    if route.get("mobile"):
        flags.append("mobile")
    print(f"Признаки маршрута: {', '.join(flags) if flags else 'обычное соединение'}")
    if route.get("geo_error"):
        print(f"Геобаза: {route['geo_error']}")


async def check_search(session: aiohttp.ClientSession, proxy: str | None) -> CheckResult:
    status, final_url, text = await fetch_text(
        session,
        f"{HH_BASE_URL}/search/vacancy",
        params={"text": "python", "area": "113", "per_page": 1},
        proxy=proxy,
    )
    if status is None or status >= 400:
        return CheckResult(mask_proxy(proxy), "GET /search/vacancy", False, status, final_url)
    soup = BeautifulSoup(text, "lxml")
    count = len(soup.select('[data-qa="vacancy-serp__vacancy"], [data-vacancy-id]'))
    return CheckResult(mask_proxy(proxy), "GET /search/vacancy", True, status, f"карточек найдено: {count}")


async def check_resumes(session: aiohttp.ClientSession, proxy: str | None) -> CheckResult:
    if not env("HH_SESSION_COOKIE"):
        return CheckResult(mask_proxy(proxy), "GET /applicant/resumes", False, None, "HH_SESSION_COOKIE не задан")
    status, final_url, text = await fetch_text(session, f"{HH_BASE_URL}/applicant/resumes", proxy=proxy)
    if status is None or status >= 400:
        return CheckResult(mask_proxy(proxy), "GET /applicant/resumes", False, status, final_url)
    lowered = f"{final_url}\n{text[:2000]}".lower()
    if "/account/login" in lowered or "войти в личный кабинет" in lowered:
        return CheckResult(mask_proxy(proxy), "GET /applicant/resumes", False, status, "HH не принял cookie и показал вход")
    soup = BeautifulSoup(text, "lxml")
    resume_links = len(soup.select('a[href*="/resume/"]'))
    return CheckResult(mask_proxy(proxy), "GET /applicant/resumes", True, status, f"ссылок на резюме найдено: {resume_links}")


def print_result(result: CheckResult) -> None:
    verdict = "ОК" if result.ok else "СБОЙ"
    status = result.status if result.status is not None else "нет HTTP-ответа"
    print(f"{verdict} {result.name}: статус {status}. {result.details}")


def final_diagnosis(results: list[CheckResult]) -> str:
    search_ok = any(item.ok and item.name == "GET /search/vacancy" for item in results)
    resumes = [item for item in results if item.name == "GET /applicant/resumes"]
    resumes_ok = any(item.ok for item in resumes)
    if search_ok and resumes_ok:
        return "HTML-доступ к HH работает, cookie приняты."
    if search_ok and not env("HH_SESSION_COOKIE"):
        return "Публичный поиск работает, но HH_SESSION_COOKIE не задан: личные страницы недоступны."
    if search_ok:
        return "Публичный поиск работает, но cookie для личных страниц не прошли проверку."
    return "HTML-доступ к HH не прошёл проверку. Проверь IP-маршрут, VPN/прокси и актуальность cookie."


async def main() -> None:
    load_dotenv(BASE_DIR / ".env", override=True)
    proxies = load_hh_proxies()
    proxy_mode = env("HH_PROXY_MODE", "direct_then_proxy") or "direct_then_proxy"

    print("Проверка HTML-доступа HH для JobRadar")
    print(f"HH_USER_AGENT={browser_user_agent(env('HH_USER_AGENT'))}")
    print(f"HH_SESSION_COOKIE={'задан' if env('HH_SESSION_COOKIE') else 'не задан'}")
    print(f"HH_PROXY_MODE={proxy_mode}")
    print(f"HH-прокси загружено: {len(proxies)}")

    candidates: list[str | None] = [None, *proxies]
    all_results: list[CheckResult] = []
    async with aiohttp.ClientSession(headers=html_headers()) as session:
        for proxy in candidates:
            route = await get_route_info(session, proxy)
            print_route_info(proxy, route)
            for result in (await check_search(session, proxy), await check_resumes(session, proxy)):
                print_result(result)
                all_results.append(result)

    print("\n== Итог ==")
    print(final_diagnosis(all_results))


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
