from __future__ import annotations

import asyncio
import sys

import aiohttp


TEST_URL = "https://hh.ru/search/vacancy?text=test&area=113&per_page=1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}


def normalize_proxy(proxy_url: str) -> str:
    proxy_url = proxy_url.strip()
    if "://" in proxy_url:
        return proxy_url
    return f"http://{proxy_url}"


async def check_single_proxy(proxy_url: str) -> None:
    proxy_url = normalize_proxy(proxy_url)
    print(f"\nПроверяю прокси для HH: {proxy_url}")

    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(
                TEST_URL,
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=12),
            ) as response:
                body = await response.text()
                if response.status == 200:
                    print("Успех: HH пропускает запросы к поиску вакансий через этот прокси.")
                elif response.status == 403:
                    print("Ошибка 403: прокси технически отвечает, но HH блокирует этот маршрут.")
                    print("Для проекта нужен другой российский IP: резидентный, мобильный или чистый серверный.")
                else:
                    print(f"Неожиданный ответ HTTP {response.status}.")
                    print(body[:500])
    except aiohttp.ClientHttpProxyError as exc:
        print(f"Это не рабочий HTTP-прокси или прокси отказал в подключении: HTTP {exc.status}.")
    except asyncio.TimeoutError:
        print("Таймаут: прокси не ответил вовремя.")
    except aiohttp.ClientError as exc:
        print(f"Сетевая ошибка: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    print("=== Проверка одного HTTP-прокси для HH.ru ===")
    proxy_input = input("Введи прокси, например http://login:pass@ip:port или ip:port: ").strip()
    if proxy_input:
        asyncio.run(check_single_proxy(proxy_input))
    else:
        print("Прокси не указан.")
