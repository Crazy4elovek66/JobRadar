from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from src.models import Salary, Vacancy
from src.scoring import SEARCH_QUERIES
from src.utils import clean_html


logger = logging.getLogger(__name__)


class HHClient:
    API_URL = "https://api.hh.ru/vacancies"
    DICTIONARIES_URL = "https://api.hh.ru/dictionaries"

    def __init__(self, area: str, proxy: str | None = None) -> None:
        self.area = area
        self.proxy = proxy

    async def ping(self) -> bool:
        headers = {"User-Agent": "JobRadar/1.0 (admin@jobradar.ru)"}
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(
                    self.DICTIONARIES_URL,
                    timeout=aiohttp.ClientTimeout(total=10),
                    proxy=self.proxy,
                ) as response:
                    return response.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.warning("HH API ping failed")
            return False

    async def search_all(self) -> list[Vacancy]:
        vacancies: dict[str, Vacancy] = {}
        headers = {"User-Agent": "JobRadar/1.0 (admin@jobradar.ru)"}
        async with aiohttp.ClientSession(headers=headers) as session:
            for query in SEARCH_QUERIES:
                try:
                    found = await self.search(session, query)
                except aiohttp.ClientError:
                    logger.exception("HH API request failed for query=%s", query)
                    continue
                except asyncio.TimeoutError:
                    logger.warning("HH API timeout for query=%s", query)
                    continue
                for vacancy in found:
                    vacancies[vacancy.external_id] = vacancy
                await asyncio.sleep(0.25)
        return list(vacancies.values())

    async def search(self, session: aiohttp.ClientSession, query: str) -> list[Vacancy]:
        params = {
            "text": query,
            "area": self.area,
            "experience": "noExperience",
            "per_page": 50,
            "order_by": "publication_time",
        }
        async with session.get(
            self.API_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=20),
            proxy=self.proxy,
        ) as response:
            response.raise_for_status()
            payload = await response.json()

        items = payload.get("items", [])
        semaphore = asyncio.Semaphore(4)

        async def fetch_detail(item: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                detail = await self._fetch_detail(session, item.get("url"))
                await asyncio.sleep(0.1)
                return detail

        details = await asyncio.gather(*(fetch_detail(item) for item in items))
        return [self._parse_vacancy(item, detail) for item, detail in zip(items, details, strict=False)]

    async def _fetch_detail(self, session: aiohttp.ClientSession, url: str | None) -> dict[str, Any]:
        if not url:
            return {}
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20), proxy=self.proxy) as response:
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.warning("Failed to fetch HH vacancy detail: %s", url)
            return {}

    def _parse_vacancy(self, item: dict[str, Any], detail: dict[str, Any]) -> Vacancy:
        salary_raw = item.get("salary") or {}
        employer = item.get("employer") or {}
        area = item.get("area") or {}
        schedule = item.get("schedule") or {}
        experience = item.get("experience") or {}
        employment = item.get("employment") or {}
        raw = {**item, "detail": detail}

        return Vacancy(
            source="hh",
            external_id=str(item.get("id", "")),
            title=item.get("name") or "Без названия",
            company=employer.get("name") or "Компания не указана",
            url=item.get("alternate_url") or "",
            area=area.get("name"),
            schedule=schedule.get("name") or schedule.get("id"),
            experience=experience.get("id") or experience.get("name"),
            employment=employment.get("name") or employment.get("id"),
            salary=Salary(
                salary_from=salary_raw.get("from"),
                salary_to=salary_raw.get("to"),
                currency=salary_raw.get("currency"),
            ),
            description=clean_html(detail.get("description")),
            raw=raw,
        )
