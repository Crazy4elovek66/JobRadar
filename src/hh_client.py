from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import aiohttp

from src.models import Salary, Vacancy
from src.scoring import SEARCH_QUERIES
from src.utils import clean_html


logger = logging.getLogger(__name__)


class HHClient:
    API_URL = "https://api.hh.ru/vacancies"
    DICTIONARIES_URL = "https://api.hh.ru/dictionaries"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/147.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    }

    def __init__(self, area: str, proxy: str | None = None) -> None:
        self.area = area
        self.proxy = proxy
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers=self.HEADERS)

    async def close(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()
        self.session = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            raise RuntimeError("HHClient session is not started")
        return self.session

    async def ping(self) -> bool:
        session = self._get_session()
        try:
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
        self._get_session()
        vacancies: dict[str, Vacancy] = {}
        queries = list(SEARCH_QUERIES)
        for index, query in enumerate(queries):
            found: list[Vacancy] = []
            try:
                found = await self.search(query)
            except aiohttp.ClientError:
                logger.exception("HH API request failed for query=%s", query)
            except asyncio.TimeoutError:
                logger.warning("HH API timeout for query=%s", query)
            for vacancy in found:
                vacancies[vacancy.external_id] = vacancy
            if index < len(queries) - 1:
                await asyncio.sleep(random.uniform(5.0, 10.0))
        return list(vacancies.values())

    async def search(self, query: str) -> list[Vacancy]:
        session = self._get_session()
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
        vacancies: list[Vacancy] = []
        for index, item in enumerate(items):
            detail = await self._fetch_detail(session, item.get("url"))
            vacancies.append(self._parse_vacancy(item, detail))
            if index < len(items) - 1:
                await asyncio.sleep(random.uniform(1.5, 3.5))
        return vacancies

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
