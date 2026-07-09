from __future__ import annotations

import asyncio
import json
import logging
import re

import aiohttp

from src.config import Settings
from src.database import Database
from src.extension_prompt import SYSTEM_PROMPT, build_user_prompt
from src.utils import utc_now_iso

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _parse_ai_response(raw: str) -> dict:
    """Parse JSON from AI response, handling ```json fences and extra text."""
    text = raw.strip()
    # Remove ```json ... ``` wrapper
    fence_match = re.search(r'```(?:json)?\s*\n?(\{.*?\})\s*```', text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        # Try to extract JSON object from text
        brace_match = re.search(r'\{.*\}', text, re.DOTALL)
        if brace_match:
            text = brace_match.group(0)
    return json.loads(text)


async def _call_openrouter(system_prompt: str, user_prompt: str, api_key: str, model: str) -> str:
    logger.info("Отправка запроса к OpenRouter. Модель: %s", model)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.4,
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    if resp.status == 402:
                        logger.error(
                            "Ошибка OpenRouter: 402 Payment Required. На вашем аккаунте нет средств, "
                            "либо выбранная модель требует оплаты. Для решения проблемы смените модель в .env "
                            "на бесплатную (например, google/gemini-2.5-flash:free)."
                        )
                    else:
                        logger.error(
                            "Ошибка OpenRouter API: HTTP %s, Ответ: %s, Модель: %s",
                            resp.status,
                            error_text,
                            model
                        )
                    raise RuntimeError(f"OpenRouter API returned HTTP {resp.status}: {error_text}")
                
                data = await resp.json()
                if "choices" not in data or not data["choices"]:
                    logger.error("Некорректный формат ответа OpenRouter (отсутствует choices): %s", data)
                    raise RuntimeError(f"Invalid OpenRouter response format (missing choices): {data}")
                
                content = data["choices"][0]["message"]["content"]
                logger.info(
                    "Получен успешный ответ от модели %s (длина ответа: %d символов)",
                    model,
                    len(content)
                )
                return content
        except asyncio.TimeoutError:
            logger.error("Превышено время ожидания ответа от OpenRouter (60 секунд). Модель: %s", model)
            raise RuntimeError("Превышено время ожидания ответа от OpenRouter (60 секунд)")
        except aiohttp.ClientError as exc:
            logger.error("Сетевая ошибка при обращении к OpenRouter: %s", exc)
            raise


async def analyze_vacancy(db: Database, settings: Settings, vacancy_data: dict) -> dict:
    hh_vacancy_id = vacancy_data["hh_vacancy_id"]

    # Check cache
    cached = db.get_extension_analysis(hh_vacancy_id)
    if cached is not None:
        logger.info("Extension analysis cache hit for vacancy %s", hh_vacancy_id)
        return cached

    # Get OpenRouter config from settings
    api_key = settings.openrouter_api_key
    model = settings.openrouter_model
    if not api_key:
        logger.error("Запрос отклонен: OPENROUTER_API_KEY не задан в .env")
        return {"error": "OPENROUTER_API_KEY не задан в .env"}

    # Build prompts
    logger.info("Сборка промпта для вакансии %s (%s)", hh_vacancy_id, vacancy_data.get("title", "без названия"))
    user_prompt = build_user_prompt(vacancy_data)

    # Call OpenRouter
    try:
        raw_response = await _call_openrouter(SYSTEM_PROMPT, user_prompt, api_key, model)
    except Exception as exc:
        logger.error("Ошибка при выполнении запроса к OpenRouter для вакансии %s: %s", hh_vacancy_id, exc)
        return {"error": f"Ошибка вызова ИИ: {exc}"}

    # Parse response
    try:
        parsed = _parse_ai_response(raw_response)
    except (json.JSONDecodeError, Exception) as exc:
        logger.error(
            "Не удалось распарсить JSON ответа ИИ для вакансии %s: %s\nСырой ответ модели:\n%s",
            hh_vacancy_id,
            exc,
            raw_response
        )
        return {"error": "Не удалось распарсить ответ ИИ", "raw_response": raw_response[:1000]}

    # Build result
    result = {
        "hh_vacancy_id": hh_vacancy_id,
        "url": vacancy_data.get("url", ""),
        "title": vacancy_data.get("title"),
        "fit": bool(parsed.get("fit", False)),
        "confidence": parsed.get("confidence", "низкая"),
        "reasons": parsed.get("reasons", []),
        "cover_letter": parsed.get("cover_letter"),
    }

    # Save to DB
    try:
        db.save_extension_analysis({
            **result,
            "raw_vacancy_json": json.dumps(vacancy_data, ensure_ascii=False),
            "created_at": utc_now_iso(),
        })
        logger.info("Результаты анализа вакансии %s успешно сохранены в кэш БД", hh_vacancy_id)
    except Exception as exc:
        logger.error("Не удалось сохранить результаты анализа вакансии %s в БД: %s", hh_vacancy_id, exc)

    return result
