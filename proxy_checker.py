import asyncio
import aiohttp
from pathlib import Path
import sys

INPUT_FILE = "proxies.txt"
OUTPUT_FILE = "good_proxies.txt"
TEST_URL = "https://api.hh.ru/vacancies?text=test&per_page=1"

# Настройки сети
CONCURRENCY = 30  # Снизили до 30, чтобы не забивать роутер
TIMEOUT = 5

async def worker(queue: asyncio.Queue, session: aiohttp.ClientSession, stop_event: asyncio.Event, working_proxies: list):
    while not stop_event.is_set():
        try:
            proxy = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
            
        formatted_proxy = proxy.strip()
        if not formatted_proxy.startswith("http"):
            formatted_proxy = f"http://{formatted_proxy}"
            
        try:
            async with session.get(
                TEST_URL,
                proxy=formatted_proxy,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT)
            ) as response:
                if response.status == 200:
                    print(f"\n[+] НАЙДЕН РАБОЧИЙ ПРОКСИ: {formatted_proxy}")
                    working_proxies.append(formatted_proxy)
                    stop_event.set() # Сигнал всем остальным немедленно остановиться
        except Exception:
            # Ошибки таймаута или соединения тихо игнорируем
            pass
        finally:
            queue.task_done()

async def main():
    input_path = Path(INPUT_FILE)
    if not input_path.exists():
        input_path.write_text("")
        print(f"Положи список прокси в {INPUT_FILE} и запусти снова.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        proxies = [line.strip() for line in f if line.strip()]

    if not proxies:
        print(f"Файл {INPUT_FILE} пуст.")
        return

    print(f"Загружено прокси: {len(proxies)} шт.")
    print("Ищем первый рабочий прокси и сразу останавливаемся...\n")

    queue = asyncio.Queue()
    for p in proxies:
        queue.put_nowait(p)

    stop_event = asyncio.Event()
    working_proxies = []
    headers = {"User-Agent": "JobRadar/1.0 (admin@jobradar.ru)"}

    async with aiohttp.ClientSession(headers=headers) as session:
        # Запускаем фиксированное число воркеров (потоков проверки)
        workers = [
            asyncio.create_task(worker(queue, session, stop_event, working_proxies))
            for _ in range(CONCURRENCY)
        ]
        
        # Ждем, пока либо опустеет очередь, либо сработает stop_event
        # gather сработает, когда воркеры выйдут из цикла while
        await asyncio.gather(*workers)

    if working_proxies:
        best_proxy = working_proxies[0]
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(f"{best_proxy}\n")
        print(f"\nГотово! Рабочий прокси '{best_proxy}' сохранен в {OUTPUT_FILE}.")
        print("Скопируй его в свой .env файл: HH_PROXY=" + best_proxy)
    else:
        print("\nНи одного подходящего прокси не найдено в списке :(")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
