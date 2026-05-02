from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import aiohttp
from dotenv import load_dotenv


API_URL = "https://api.hh.ru"


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def normalize_proxy(proxy: str) -> str | None:
    if not proxy:
        return None
    if "://" in proxy:
        return proxy
    return f"http://{proxy}"


async def fetch_json(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    proxy: str | None = None,
) -> tuple[int, dict[str, Any] | str]:
    async with session.request(
        method,
        url,
        params=params,
        proxy=proxy,
        timeout=aiohttp.ClientTimeout(total=20),
    ) as response:
        text = await response.text()
        try:
            return response.status, await response.json()
        except Exception:
            return response.status, text[:800]


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
            explanations.append("Проверь HH_USER_AGENT: он должен быть передан и не должен быть шаблонным мусором.")
        elif error_type == "bad_argument":
            explanations.append(f"Некорректный параметр запроса: {value or 'не указан'}.")
        elif error_type == "oauth":
            explanations.append(f"Проблема с OAuth-токеном: {value or 'без уточнения'}.")
        elif error_type == "captcha_required":
            explanations.append("HH требует капчу. Скрипт не обходит капчу, нужно снизить частоту или проверить маршрут.")
        elif error_type == "forbidden":
            explanations.append("HH запретил доступ к методу с текущего маршрута или клиента.")
        else:
            explanations.append(f"Неизвестный тип ошибки: {error_type}, значение: {value}.")
    return "\n".join(explanations) or "Нет подробного объяснения."


async def main() -> None:
    load_dotenv()

    hh_host = env("HH_HOST", "hh.ru") or "hh.ru"
    hh_area = env("HH_AREA", "113") or "113"
    user_agent = env("HH_USER_AGENT", "JobRadar/1.0 (email@example.com)") or "JobRadar/1.0 (email@example.com)"
    access_token = env("HH_ACCESS_TOKEN")
    proxy = normalize_proxy(env("HH_PROXY"))

    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    print("Проверка HH API для JobRadar")
    print(f"HH_HOST={hh_host}")
    print(f"HH_AREA={hh_area}")
    print(f"HH_USER_AGENT={user_agent}")
    print(f"HH_ACCESS_TOKEN={'задан' if access_token else 'не задан'}")
    print(f"HH_PROXY={'задан' if proxy else 'не задан'}")

    async with aiohttp.ClientSession(headers=headers) as session:
        status, payload = await fetch_json(
            session,
            "GET",
            f"{API_URL}/vacancies",
            params={"host": hh_host, "text": "python", "area": hh_area, "per_page": 5},
            proxy=proxy,
        )
        print_result("GET /vacancies", status, payload)

        if access_token:
            status, payload = await fetch_json(session, "GET", f"{API_URL}/me", proxy=proxy)
            print_result("GET /me", status, payload)
        else:
            print("\n== GET /me ==")
            print("Пропущено: HH_ACCESS_TOKEN не задан.")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
