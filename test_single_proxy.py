import asyncio
import aiohttp
import sys

async def check_single_proxy(proxy_url: str):
    print(f"\n[~] Проверяем прокси: {proxy_url}")
    print("[~] Подключение к api.hh.ru...\n")
    
    headers = {"User-Agent": "JobRadar/1.0 (admin@jobradar.ru)"}
    
    # Добавляем http:// если забыли
    if not proxy_url.startswith("http") and not proxy_url.startswith("socks5"):
        proxy_url = f"http://{proxy_url}"
        
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                "https://api.hh.ru/vacancies?text=test&per_page=1",
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    print("✅ УСПЕХ! Прокси ИДЕАЛЬНО работает и подходит для бота.")
                elif response.status == 403:
                    print("❌ ОШИБКА 403 (Forbidden): Прокси рабочий, НО он заблокирован защитой HH.ru (DDOS-Guard).")
                    print("👉 Решение: Этот прокси не подойдет, HH.ru банит этот IP. Нужен другой прокси (желательно мобильный или резидентный).")
                else:
                    print(f"⚠️ НЕИЗВЕСТНЫЙ ОТВЕТ: HTTP {response.status}")
                    
    except aiohttp.client_exceptions.ClientHttpProxyError as e:
        if e.status == 301 or e.status == 302:
            print("❌ ЭТО НЕ ПРОКСИ (Ошибка 301/302 Redirect).")
            print("👉 Объяснение: Ты пытаешься использовать обычный сайт или открытый порт 80 как прокси. Это мусорный IP из бесплатной базы, он не умеет пропускать трафик.")
        else:
            print(f"❌ ОШИБКА ПРОКСИ: {e}")
            
    except asyncio.TimeoutError:
        print("❌ ОШИБКА ТАЙМАУТА (TimeoutError).")
        print("👉 Объяснение: Прокси мертв, недоступен или завис. Он не отвечает.")
        
    except Exception as e:
        print(f"❌ СЕТЕВАЯ ОШИБКА: {type(e).__name__}")
        print(f"👉 Детали: {e}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    print("=== ТЕСТ ОДНОГО ПРОКСИ ДЛЯ HH.RU ===")
    proxy_input = input("Введи прокси (например, 12.34.56.78:8080 или http://log:pass@ip:port): ").strip()
    
    if proxy_input:
        asyncio.run(check_single_proxy(proxy_input))
    else:
        print("Ты ничего не ввел.")
