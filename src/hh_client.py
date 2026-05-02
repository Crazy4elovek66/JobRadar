from __future__ import annotations

import asyncio
import html
import logging
import random
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urljoin

import aiohttp

from src.models import Salary, Vacancy
from src.scoring import SEARCH_QUERIES
from src.utils import clean_html, normalize_text


logger = logging.getLogger(__name__)


class HHAccessBlockedError(RuntimeError):
    pass


class HHClient:
    API_URL = "https://api.hh.ru/vacancies"
    HTML_SEARCH_URL = "https://hh.ru/search/vacancy"
    HTML_VACANCY_URL = "https://hh.ru/vacancy/{vacancy_id}"
    HTML_ITEMS_PER_QUERY = 10
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
    HTML_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://hh.ru/",
    }

    def __init__(
        self,
        area: str,
        host: str = "hh.ru",
        proxies: Iterable[str] = (),
        user_agent: str | None = None,
        access_token: str | None = None,
    ) -> None:
        self.area = area
        self.host = host
        self.proxies = tuple(proxies)
        self.user_agent = user_agent or "JobRadar/1.0 (email@example.com)"
        self.access_token = access_token
        self._proxy_index = 0
        self._blocked_proxies: set[str | None] = set()
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers=self._headers())

    async def close(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()
        self.session = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            raise RuntimeError("HHClient session is not started")
        return self.session

    def _headers(self) -> dict[str, str]:
        headers = {**self.HEADERS, "User-Agent": self.user_agent}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    async def ping(self) -> bool:
        try:
            await self._request_json(
                self.API_URL,
                params={"host": self.host, "text": "test", "area": self.area, "per_page": 1},
                timeout=10,
            )
            return True
        except (HHAccessBlockedError, aiohttp.ClientError, asyncio.TimeoutError):
            logger.warning("HH API ping failed")
            return False

    async def search_all(self, ignored_external_ids: set[str] | None = None) -> list[Vacancy]:
        self._get_session()
        ignored_external_ids = ignored_external_ids or set()
        vacancies: dict[str, Vacancy] = {}
        queries = list(SEARCH_QUERIES)
        for index, query in enumerate(queries):
            found: list[Vacancy] = []
            try:
                found = await self.search(query, ignored_external_ids=ignored_external_ids | set(vacancies))
            except HHAccessBlockedError:
                logger.warning("HH API access blocked for all configured routes")
                raise
            except aiohttp.ClientError:
                logger.exception("HH API request failed for query=%s", query)
            except asyncio.TimeoutError:
                logger.warning("HH API timeout for query=%s", query)
            for vacancy in found:
                vacancies[vacancy.external_id] = vacancy
            if index < len(queries) - 1:
                await asyncio.sleep(random.uniform(5.0, 10.0))
        return list(vacancies.values())

    async def search(self, query: str, ignored_external_ids: set[str] | None = None) -> list[Vacancy]:
        self._get_session()
        ignored_external_ids = ignored_external_ids or set()
        params = {
            "host": self.host,
            "text": query,
            "area": self.area,
            "experience": "noExperience",
            "schedule": "remote",
            "per_page": 50,
            "order_by": "publication_time",
        }
        try:
            payload = await self._request_json(
                self.API_URL,
                params=params,
                timeout=20,
            )
        except HHAccessBlockedError:
            logger.warning("HH API blocked search query=%s, switching to HTML fallback", query)
            return await self._search_html(query, ignored_external_ids=ignored_external_ids)

        items = payload.get("items", [])
        vacancies: list[Vacancy] = []
        for index, item in enumerate(items):
            external_id = str(item.get("id", ""))
            if external_id and external_id in ignored_external_ids:
                continue
            detail = await self._fetch_detail(item.get("url"))
            vacancies.append(self._parse_vacancy(item, detail))
            if index < len(items) - 1:
                await asyncio.sleep(random.uniform(1.5, 3.5))
        return vacancies

    async def _fetch_detail(self, url: str | None) -> dict[str, Any]:
        if not url:
            return {}
        try:
            return await self._request_json(url, timeout=20)
        except (HHAccessBlockedError, aiohttp.ClientError, asyncio.TimeoutError):
            logger.warning("Failed to fetch HH vacancy detail: %s", url)
            return {}

    def _proxy_candidates(self) -> list[str | None]:
        if not self.proxies:
            return [None]

        ordered = self.proxies[self._proxy_index :] + self.proxies[: self._proxy_index]
        return [proxy for proxy in ordered if proxy not in self._blocked_proxies]

    async def _request_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        timeout: int = 20,
    ) -> dict[str, Any]:
        session = self._get_session()
        candidates = self._proxy_candidates()
        if not candidates:
            raise HHAccessBlockedError("Все настроенные маршруты к HH API получили 403.")

        last_error: Exception | None = None
        for proxy in candidates:
            try:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    proxy=proxy,
                ) as response:
                    if response.status == 403:
                        body = await response.text()
                        logger.warning(
                            "HH API returned 403 via %s: %s",
                            proxy or "direct connection",
                            body[:300],
                        )
                        self._mark_proxy_blocked(proxy)
                        last_error = HHAccessBlockedError("HH API вернул 403.")
                        continue

                    response.raise_for_status()
                    self._mark_proxy_active(proxy)
                    return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning("HH API request failed via %s: %s", proxy or "direct connection", exc)
                last_error = exc
                if proxy:
                    self._mark_proxy_blocked(proxy)

        if isinstance(last_error, HHAccessBlockedError):
            raise last_error
        if last_error:
            raise last_error
        raise HHAccessBlockedError("Нет доступных маршрутов к HH API.")

    def _mark_proxy_active(self, proxy: str | None) -> None:
        if proxy and proxy in self.proxies:
            self._proxy_index = self.proxies.index(proxy)

    def _mark_proxy_blocked(self, proxy: str | None) -> None:
        self._blocked_proxies.add(proxy)

    async def _search_html(self, query: str, ignored_external_ids: set[str]) -> list[Vacancy]:
        params = {
            "text": query,
            "area": self.area,
            "experience": "noExperience",
            "schedule": "remote",
            "items_on_page": self.HTML_ITEMS_PER_QUERY,
            "hhtmFrom": "vacancy_search_list",
        }
        search_html = await self._request_text(self.HTML_SEARCH_URL, params=params, timeout=20)
        vacancy_ids = [
            vacancy_id
            for vacancy_id in self._extract_html_vacancy_ids(search_html)
            if vacancy_id not in ignored_external_ids
        ]

        vacancies: list[Vacancy] = []
        for index, vacancy_id in enumerate(vacancy_ids):
            vacancy = await self._fetch_html_vacancy(vacancy_id)
            if vacancy:
                vacancies.append(vacancy)
            if index < len(vacancy_ids) - 1:
                await asyncio.sleep(random.uniform(1.0, 2.0))
        return vacancies

    async def _fetch_html_vacancy(self, vacancy_id: str) -> Vacancy | None:
        url = self.HTML_VACANCY_URL.format(vacancy_id=vacancy_id)
        try:
            page_html = await self._request_text(url, timeout=20)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("Failed to fetch HH vacancy HTML %s: %s", vacancy_id, exc)
            return None
        return self._parse_html_vacancy(vacancy_id, url, page_html)

    async def _request_text(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        timeout: int = 20,
    ) -> str:
        session = self._get_session()
        candidates = self._proxy_candidates()
        if not candidates:
            raise HHAccessBlockedError("Нет доступных маршрутов к HTML-страницам HH.")

        last_error: Exception | None = None
        for proxy in candidates:
            try:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    proxy=proxy,
                    headers=self.HTML_HEADERS,
                ) as response:
                    if response.status == 403:
                        body = await response.text()
                        logger.warning(
                            "HH HTML returned 403 via %s: %s",
                            proxy or "direct connection",
                            body[:300],
                        )
                        self._mark_proxy_blocked(proxy)
                        last_error = HHAccessBlockedError("HTML-страница HH вернула 403.")
                        continue

                    response.raise_for_status()
                    self._mark_proxy_active(proxy)
                    return await response.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning("HH HTML request failed via %s: %s", proxy or "direct connection", exc)
                last_error = exc
                if proxy:
                    self._mark_proxy_blocked(proxy)

        if last_error:
            raise last_error
        raise HHAccessBlockedError("Нет доступных маршрутов к HTML-страницам HH.")

    def _extract_html_vacancy_ids(self, page_html: str) -> list[str]:
        ids = re.findall(r'<div id="(\d+)" class="vacancy-card', page_html)
        if not ids:
            ids = re.findall(r"/vacancy/(\d+)", page_html)

        result: list[str] = []
        seen: set[str] = set()
        for vacancy_id in ids:
            if vacancy_id not in seen:
                result.append(vacancy_id)
                seen.add(vacancy_id)
        return result[: self.HTML_ITEMS_PER_QUERY]

    def _parse_html_vacancy(self, vacancy_id: str, fallback_url: str, page_html: str) -> Vacancy:
        meta_description = self._extract_meta_description(page_html)
        meta = self._parse_meta_description(meta_description)
        description_html = self._extract_vacancy_description_html(page_html)
        salary_from, salary_to, currency = self._parse_salary(meta.get("salary") or self._extract_data_qa(page_html, "vacancy-salary"))

        return Vacancy(
            source="hh",
            external_id=vacancy_id,
            title=self._extract_data_qa(page_html, "vacancy-title") or meta.get("title") or "Без названия",
            company=self._extract_data_qa(page_html, "vacancy-company-name") or meta.get("company") or "Компания не указана",
            url=self._extract_canonical_url(page_html) or fallback_url,
            area=meta.get("area"),
            schedule=self._extract_schedule(page_html),
            experience=self._extract_data_qa(page_html, "vacancy-experience") or meta.get("experience"),
            employment=meta.get("employment"),
            salary=Salary(salary_from=salary_from, salary_to=salary_to, currency=currency),
            description=clean_html(description_html),
            raw={
                "html_fallback": True,
                "alternate_url": self._extract_canonical_url(page_html) or fallback_url,
                "published_at": self._extract_published_at(page_html),
                "meta_description": meta_description,
            },
        )

    def _extract_data_qa(self, page_html: str, data_qa: str) -> str:
        match = re.search(
            rf'<(?P<tag>[a-z0-9]+)[^>]*data-qa="{re.escape(data_qa)}"[^>]*>(?P<body>.*?)</(?P=tag)>',
            page_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return ""
        return clean_html(match.group("body"))

    def _extract_vacancy_description_html(self, page_html: str) -> str:
        marker = 'data-qa="vacancy-description"'
        marker_index = page_html.find(marker)
        if marker_index == -1:
            return ""

        start = page_html.rfind("<div", 0, marker_index)
        if start == -1:
            return ""

        end_markers = [
            '</div></div><div class="vacancy-section',
            '</div></div></div><div class="vacancy-section',
        ]
        end_candidates = [page_html.find(end_marker, marker_index) for end_marker in end_markers]
        end_candidates = [index for index in end_candidates if index != -1]
        if not end_candidates:
            return page_html[start : marker_index + 5000]
        return page_html[start : min(end_candidates) + len("</div>")]

    def _extract_meta_description(self, page_html: str) -> str:
        match = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', page_html, flags=re.IGNORECASE)
        if not match:
            return ""
        return normalize_text(html.unescape(match.group(1)))

    def _parse_meta_description(self, description: str) -> dict[str, str]:
        meta: dict[str, str] = {}
        if not description:
            return meta

        title_match = re.search(r"Вакансия (?P<title>.*?) в компании (?P<company>.*?)\.", description)
        if title_match:
            meta["title"] = normalize_text(title_match.group("title"))
            meta["company"] = normalize_text(title_match.group("company"))

        details_match = re.search(
            r"Зарплата: (?P<salary>.*?)\. (?P<area>.*?)\. Требуемый опыт: (?P<experience>.*?)\. Занятость: (?P<employment>.*?)\.",
            description,
        )
        if details_match:
            meta.update({key: normalize_text(value) for key, value in details_match.groupdict().items()})
        return meta

    def _parse_salary(self, salary_text: str) -> tuple[int | None, int | None, str | None]:
        text = normalize_text(salary_text.replace("\xa0", " "))
        if not text or "не указ" in text.lower():
            return None, None, None

        numbers = [int(value.replace(" ", "")) for value in re.findall(r"\d[\d ]*", text)]
        salary_from: int | None = None
        salary_to: int | None = None
        if "от" in text.lower() and numbers:
            salary_from = numbers[0]
        if "до" in text.lower() and numbers:
            salary_to = numbers[-1]
        if salary_from is None and salary_to is None and numbers:
            salary_from = numbers[0]

        currency = "RUR" if "₽" in text or "руб" in text.lower() else None
        return salary_from, salary_to, currency

    def _extract_schedule(self, page_html: str) -> str | None:
        text = clean_html(page_html)
        for value in ("Удалённо", "Удаленно", "Гибрид", "Полный день", "Сменный график"):
            if value.lower().replace("ё", "е") in text.lower().replace("ё", "е"):
                return value
        return None

    def _extract_canonical_url(self, page_html: str) -> str:
        match = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', page_html, flags=re.IGNORECASE)
        if not match:
            return ""
        return urljoin("https://hh.ru", html.unescape(match.group(1)))

    def _extract_published_at(self, page_html: str) -> str | None:
        match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', page_html)
        if match:
            return match.group(1)
        return None

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
