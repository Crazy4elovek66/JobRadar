from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import aiohttp


INPUT_FILE = "proxies.txt"
OUTPUT_FILE = "good_proxies.txt"
TEST_URL = "https://hh.ru/search/vacancy?text=test&area=113&per_page=1"
CONCURRENCY = 20
TIMEOUT = 12
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}


def normalize_proxy(proxy: str) -> str:
    proxy = proxy.strip()
    if "://" in proxy:
        return proxy
    return f"http://{proxy}"


async def worker(
    queue: asyncio.Queue[str],
    session: aiohttp.ClientSession,
    working_proxies: list[str],
) -> None:
    while True:
        try:
            proxy = queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        proxy_url = normalize_proxy(proxy)
        try:
            async with session.get(
                TEST_URL,
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            ) as response:
                body = await response.text()
                if response.status == 200:
                    print(f"[OK] HH пропускает прокси: {proxy_url}")
                    working_proxies.append(proxy_url)
                elif response.status == 403:
                    print(f"[403] HH блокирует этот маршрут: {proxy_url}")
                else:
                    print(f"[{response.status}] Неожиданный ответ через {proxy_url}: {body[:120]}")
        except asyncio.TimeoutError:
            print(f"[TIMEOUT] Прокси не ответил вовремя: {proxy_url}")
        except aiohttp.ClientHttpProxyError as exc:
            print(f"[PROXY] Некорректный HTTP-прокси {proxy_url}: {exc.status}")
        except aiohttp.ClientError as exc:
            print(f"[NET] Сетевая ошибка через {proxy_url}: {type(exc).__name__}")
        finally:
            queue.task_done()


async def main() -> None:
    input_path = Path(INPUT_FILE)
    if not input_path.exists():
        input_path.write_text("", encoding="utf-8")
        print(f"Положи список HTTP-прокси в {INPUT_FILE} и запусти проверку снова.")
        return

    proxies = [
        line.strip()
        for line in input_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not proxies:
        print(f"Файл {INPUT_FILE} пуст.")
        return

    print(f"Загружено прокси: {len(proxies)}.")
    print("Проверяю HTML-выдачу /search/vacancy, потому что JobRadar теперь работает со страницами hh.ru.\n")

    queue: asyncio.Queue[str] = asyncio.Queue()
    for proxy in proxies:
        queue.put_nowait(proxy)

    working_proxies: list[str] = []
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        workers = [asyncio.create_task(worker(queue, session, working_proxies)) for _ in range(CONCURRENCY)]
        await asyncio.gather(*workers)

    if working_proxies:
        Path(OUTPUT_FILE).write_text("\n".join(working_proxies) + "\n", encoding="utf-8")
        print(f"\nГотово. Подходящих прокси: {len(working_proxies)}. Список сохранен в {OUTPUT_FILE}.")
        print(f"Для запуска бота можно оставить HH_PROXY_FILE={OUTPUT_FILE} или указать первый прокси в HH_PROXY.")
    else:
        print("\nПодходящих прокси не найдено. Нужен российский резидентный, мобильный или серверный IP, который HH не режет.")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
