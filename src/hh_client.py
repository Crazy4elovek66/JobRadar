from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator, Iterable
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import aiohttp
from bs4 import BeautifulSoup

from src.config import Settings
from src.database import Database
from src.models import Salary, Vacancy
from src.scoring import SEARCH_QUERIES
from src.utils import clean_html


logger = logging.getLogger(__name__)


class HHApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        status: int | None = None,
        error_type: str | None = None,
        error_value: str | None = None,
        *,
        raw_payload: Any = None,
        url: str | None = None,
        params: dict[str, Any] | None = None,
        method: str | None = None,
        proxy: str | None = None,
        response_headers: dict[str, str] | None = None,
        content_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.error_value = error_value
        self.raw_payload = raw_payload
        self.url = url
        self.params = params or {}
        self.method = method
        self.proxy = proxy
        self.response_headers = response_headers or {}
        self.content_type = content_type


class HHAuthRequiredError(HHApiError):
    pass


class HHClient:
    site_base = "https://hh.ru"

    def __init__(
        self,
        settings: Settings,
        db: Database,
        proxies: Iterable[str] = (),
    ) -> None:
        self.settings = settings
        self.db = db
        self.proxies = tuple(proxies)
        self._proxy_index = 0
        self._blocked_proxies: set[str | None] = set()
        self.session: aiohttp.ClientSession | None = None
        self.last_request_proxy: str | None = None

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(ssl=False) if self.settings.disable_hh_ssl_verification else None
            self.session = aiohttp.ClientSession(headers=self._base_headers(), connector=connector)

    async def close(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()
        self.session = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            raise RuntimeError("Сессия HHClient ещё не запущена.")
        return self.session

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": self.settings.hh_user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.6,en;q=0.4",
            "Accept-Encoding": "gzip, deflate",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        }
        if self.settings.hh_session_cookie:
            headers["Cookie"] = self.settings.hh_session_cookie
        return headers

    async def ping(self) -> bool:
        try:
            await self.search_vacancies(None, {"text": "python", "area": self.settings.hh_area, "per_page": 1})
            return True
        except HHApiError as exc:
            logger.warning(
                "HH HTML ping failed: status=%s type=%s value=%s route=%s recommendation=%s",
                exc.status,
                exc.error_type,
                exc.error_value,
                self._proxy_label(exc.proxy),
                self.explain_error(exc.error_value or exc.error_type or str(exc.status or "")),
            )
            return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("HH HTML ping failed: route=%s network_error=%s", self._proxy_label(self.last_request_proxy), exc)
            return False

    async def _request_html(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        auth: bool = False,
        timeout: int = 25,
        referer: str | None = None,
    ) -> str:
        if auth and not self.settings.hh_session_cookie:
            raise HHAuthRequiredError(
                "В .env не задан HH_SESSION_COOKIE. JobRadar не сможет открыть личные страницы HH без браузерной сессии.",
                error_type="auth",
                error_value="session_cookie_required",
                url=url,
                params=params,
                method="GET",
            )

        headers = self._base_headers()
        if referer:
            headers["Referer"] = referer

        session = self._get_session()
        last_error: Exception | None = None
        for proxy in self._proxy_candidates():
            self.last_request_proxy = proxy
            try:
                async with session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    proxy=proxy,
                    allow_redirects=True,
                ) as response:
                    content_type = response.headers.get("Content-Type", "")
                    text = await response.text()
                    if response.status >= 400:
                        error = self._build_error(
                            response.status,
                            text,
                            method="GET",
                            url=str(response.url),
                            params=params,
                            proxy=proxy,
                            response_headers=dict(response.headers),
                            content_type=content_type,
                        )
                        self._log_hh_error(error)
                        if self._should_try_next_route(error):
                            last_error = error
                            if proxy:
                                self._mark_proxy_blocked(proxy)
                            continue
                        raise error
                    if auth and self._looks_like_login_page(text, str(response.url)):
                        raise HHAuthRequiredError(
                            "HH не принял сессионные cookie. Обнови HH_SESSION_COOKIE из браузера и попробуй снова.",
                            status=response.status,
                            error_type="auth",
                            error_value="session_cookie_expired",
                            raw_payload=text[:1000],
                            url=str(response.url),
                            params=params,
                            method="GET",
                            proxy=proxy,
                            response_headers=self._useful_headers(dict(response.headers)),
                            content_type=content_type,
                        )
                    self._mark_proxy_active(proxy)
                    self.last_request_proxy = proxy
                    return text
            except (aiohttp.ClientError, asyncio.TimeoutError, HHApiError) as exc:
                last_error = exc
                if proxy:
                    self._mark_proxy_blocked(proxy)
                if isinstance(exc, HHApiError):
                    raise
        if last_error:
            raise last_error
        raise HHApiError("HH сейчас недоступен: не удалось выполнить HTML-запрос.")

    def _build_error(
        self,
        status: int,
        payload: dict[str, Any] | str,
        *,
        method: str | None = None,
        url: str | None = None,
        params: dict[str, Any] | None = None,
        proxy: str | None = None,
        response_headers: dict[str, str] | None = None,
        content_type: str | None = None,
    ) -> HHApiError:
        error_type = "http"
        error_value = str(status)
        if status == 403:
            error_type = "forbidden"
            error_value = "forbidden"
        elif status == 404:
            error_value = "not_found"
        elif status in {401, 302}:
            error_type = "auth"
            error_value = "session_cookie_expired"
        message = self.explain_error(error_value)
        error_cls = HHAuthRequiredError if error_type == "auth" else HHApiError
        return error_cls(
            message,
            status=status,
            error_type=error_type,
            error_value=error_value,
            raw_payload=payload,
            url=url,
            params=self._safe_params(params or {}),
            method=method,
            proxy=proxy,
            response_headers=self._useful_headers(response_headers or {}),
            content_type=content_type,
        )

    async def get_me(self, telegram_user_id: int) -> dict[str, Any]:
        html = await self._request_html(f"{self.site_base}/applicant/resumes", auth=True)
        return {"authorized": not self._looks_like_login_page(html, f"{self.site_base}/applicant/resumes")}

    async def get_my_resumes(self, telegram_user_id: int) -> list[dict[str, Any]]:
        html = await self._request_html(f"{self.site_base}/applicant/resumes", auth=True)
        resumes = self._parse_resumes(html)
        if not resumes:
            logger.warning("HH resumes page parsed without resumes; layout may have changed")
        return resumes

    async def get_vacancy(self, telegram_user_id: int | None, vacancy_id: str, resume_id: str | None = None) -> dict[str, Any]:
        url = f"{self.site_base}/vacancy/{vacancy_id}"
        use_auth = bool(telegram_user_id and self.settings.hh_session_cookie)
        try:
            html = await self._request_html(url, auth=use_auth, referer=f"{self.site_base}/search/vacancy")
        except HHAuthRequiredError:
            logger.warning("HH auth failed while opening vacancy %s; falling back to public vacancy page", vacancy_id)
            html = await self._request_html(url, auth=False, referer=f"{self.site_base}/search/vacancy")
        return self._parse_vacancy_detail(html, vacancy_id, url)

    async def search_vacancies(self, telegram_user_id: int | None, params: dict[str, Any]) -> dict[str, Any]:
        search_params = self._search_params(params)
        html = await self._request_html(f"{self.site_base}/search/vacancy", params=search_params, auth=False)
        items = self._parse_search_results(html)
        return {"items": items, "found": len(items), "page": int(search_params.get("page") or 0)}

    async def apply_to_vacancy(self, telegram_user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        vacancy_id = str(payload.get("vacancy_id") or "")
        url = f"{self.site_base}/vacancy/{vacancy_id}" if vacancy_id else self.site_base
        raise HHApiError(
            "Автоотклики через HTML-версию пока отключены. Открой вакансию на HH и отправь отклик вручную.",
            status=None,
            error_type="html_apply",
            error_value="html_apply_disabled",
            url=url,
            method="POST",
        )

    async def search_all(
        self,
        ignored_external_ids: set[str] | None = None,
        keywords: list[str] | None = None,
        areas: list[str] | None = None,
        only_remote: bool = True,
        telegram_user_id: int | None = None,
    ) -> AsyncGenerator[Vacancy, None]:
        ignored_external_ids = ignored_external_ids or set()
        seen_external_ids: set[str] = set()
        for query in (keywords or SEARCH_QUERIES):
            params: dict[str, Any] = {
                "text": query,
                "area": areas or [self.settings.hh_area],
                "experience": "noExperience",
                "per_page": 50,
                "order_by": "publication_time",
            }
            if only_remote:
                params["schedule"] = "remote"
            payload = await self.search_vacancies(None, params)
            for item in payload.get("items", []):
                external_id = str(item.get("id", ""))
                if not external_id or external_id in ignored_external_ids or external_id in seen_external_ids:
                    continue
                seen_external_ids.add(external_id)
                detail = await self.get_vacancy(telegram_user_id, external_id)
                yield self._parse_vacancy(item, detail)
                await asyncio.sleep(0.8)

    def _parse_search_results(self, html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select('[data-qa="vacancy-serp__vacancy"]')
        if not cards:
            cards = soup.select("[data-vacancy-id]")

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for card in cards:
            title_link = (
                card.select_one('[data-qa="vacancy-serp__vacancy-title"]')
                or card.select_one('a[data-qa="serp-item__title"]')
                or card.select_one('a[href*="/vacancy/"]')
            )
            if not title_link:
                continue
            href = title_link.get("href") or ""
            vacancy_id = self._vacancy_id_from_url(href) or card.get("data-vacancy-id")
            if not vacancy_id or vacancy_id in seen:
                continue
            seen.add(vacancy_id)

            company_node = (
                card.select_one('[data-qa="vacancy-serp__vacancy-employer"]')
                or card.select_one('[data-qa="vacancy-serp__vacancy-employer-text"]')
                or card.select_one('[data-qa="serp-item__meta-info-company"]')
            )
            salary_node = card.select_one('[data-qa="vacancy-serp__vacancy-compensation"]') or card.select_one('[data-qa="vacancy-serp__vacancy-salary"]')
            area_node = card.select_one('[data-qa="vacancy-serp__vacancy-address"]') or card.select_one('[data-qa="vacancy-serp__vacancy-work-address"]')

            items.append(
                {
                    "id": vacancy_id,
                    "name": self._text(title_link) or "Без названия",
                    "alternate_url": self._absolute_hh_url(href),
                    "employer": {"name": self._text(company_node) or "Компания не указана"},
                    "salary": self._parse_salary(self._text(salary_node)),
                    "area": {"name": self._text(area_node) or None},
                }
            )
        return items

    def _parse_vacancy_detail(self, html: str, vacancy_id: str, url: str) -> dict[str, Any]:
        soup = BeautifulSoup(html, "lxml")
        title = self._text(soup.select_one('[data-qa="vacancy-title"]') or soup.select_one("h1")) or "Без названия"
        company_node = (
            soup.select_one('[data-qa="vacancy-company-name"]')
            or soup.select_one('[data-qa="vacancy-company-name"] span')
            or soup.select_one('[data-qa="bloko-header-2"]')
        )
        salary_node = soup.select_one('[data-qa="vacancy-salary"]') or soup.select_one('[data-qa="vacancy-compensation"]')
        description_node = soup.select_one('[data-qa="vacancy-description"]') or soup.select_one(".vacancy-description")
        area_node = soup.select_one('[data-qa="vacancy-view-raw-address"]') or soup.select_one('[data-qa="vacancy-view-location"]')
        schedule_node = soup.select_one('[data-qa="vacancy-view-employment-mode"]')
        experience_node = soup.select_one('[data-qa="vacancy-experience"]')
        page_text = soup.get_text(" ", strip=True).lower()

        employer_id = None
        employer_link = soup.select_one('a[href*="/employer/"]')
        if employer_link and employer_link.get("href"):
            match = re.search(r"/employer/(\d+)", employer_link["href"])
            employer_id = match.group(1) if match else None

        return {
            "id": vacancy_id,
            "name": title,
            "alternate_url": url,
            "employer": {"id": employer_id, "name": self._text(company_node) or "Компания не указана"},
            "salary": self._parse_salary(self._text(salary_node)),
            "area": {"name": self._text(area_node) or None},
            "schedule": {"name": self._text(schedule_node) or None},
            "experience": {"name": self._text(experience_node) or None},
            "employment": {"name": None},
            "description": str(description_node) if description_node else "",
            "archived": "вакансия в архиве" in page_text or "вакансия недоступна" in page_text,
            "has_test": "тестовое задание" in page_text or "пройти тест" in page_text,
            "already_applied": self._looks_already_applied(page_text),
            "response_url": None,
            "response_letter_required": "сопроводительное письмо обязательно" in page_text,
        }

    def _parse_resumes(self, html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        resumes: dict[str, dict[str, Any]] = {}

        for link in soup.select('a[href*="/resume/"]'):
            href = link.get("href") or ""
            resume_id = self._resume_id_from_url(href)
            title_node = (
                link.select_one('[data-qa="resume-title"] [data-qa="title"]')
                or link.select_one('[data-qa="resume-title"]')
                or link.select_one('[data-qa="title"]')
            )
            title = self._text(title_node) or self._clean_resume_title(self._text(link))
            if resume_id and title and len(title) <= 160:
                resumes[resume_id] = {"id": resume_id, "title": title}

        for script in soup.find_all("script"):
            text = script.string or script.get_text() or ""
            if "resume" not in text.lower():
                continue
            for match in re.finditer(r'"(?:id|resumeHash|hash)"\s*:\s*"([0-9a-f]{8,}|[A-Za-z0-9_-]{8,})"', text):
                resume_id = match.group(1)
                window = text[max(0, match.start() - 800) : match.end() + 1200]
                title_match = re.search(r'"(?:title|name)"\s*:\s*"([^"]{2,160})"', window)
                if title_match:
                    title = self._decode_json_string(title_match.group(1))
                    if title:
                        resumes.setdefault(resume_id, {"id": resume_id, "title": title})

        return list(resumes.values())

    def _parse_vacancy(self, item: dict[str, Any], detail: dict[str, Any]) -> Vacancy:
        salary_raw = item.get("salary") or detail.get("salary") or {}
        employer = item.get("employer") or detail.get("employer") or {}
        area = item.get("area") or detail.get("area") or {}
        schedule = item.get("schedule") or detail.get("schedule") or {}
        experience = item.get("experience") or detail.get("experience") or {}
        employment = item.get("employment") or detail.get("employment") or {}
        raw = {**item, "detail": detail}
        return Vacancy(
            source="hh",
            external_id=str(item.get("id") or detail.get("id") or ""),
            title=item.get("name") or detail.get("name") or "Без названия",
            company=employer.get("name") or "Компания не указана",
            url=item.get("alternate_url") or detail.get("alternate_url") or "",
            area=area.get("name"),
            schedule=schedule.get("name") or schedule.get("id"),
            experience=experience.get("id") or experience.get("name"),
            employment=employment.get("name") or employment.get("id"),
            salary=Salary(salary_from=salary_raw.get("from"), salary_to=salary_raw.get("to"), currency=salary_raw.get("currency")),
            description=clean_html(detail.get("description")),
            raw=raw,
        )

    def _search_params(self, params: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in params.items():
            if key == "host" or value in (None, "", []):
                continue
            result[key] = value
        result.setdefault("area", self.settings.hh_area)
        return result

    @staticmethod
    def _parse_salary(text: str) -> dict[str, Any]:
        if not text:
            return {}
        normalized = text.replace("\u202f", " ").replace("\xa0", " ")
        numbers = [int(value.replace(" ", "")) for value in re.findall(r"\d[\d ]*", normalized)]
        currency = None
        lowered = normalized.lower()
        if "руб" in lowered or "₽" in normalized:
            currency = "RUR"
        elif "$" in normalized or "usd" in lowered:
            currency = "USD"
        elif "€" in normalized or "eur" in lowered:
            currency = "EUR"
        salary_from = salary_to = None
        if numbers:
            if any(word in lowered for word in ("от", "from")):
                salary_from = numbers[0]
            if any(word in lowered for word in ("до", "up to")):
                salary_to = numbers[-1]
            if salary_from is None and salary_to is None:
                salary_from = numbers[0]
                salary_to = numbers[1] if len(numbers) > 1 else None
        return {"from": salary_from, "to": salary_to, "currency": currency}

    @staticmethod
    def _vacancy_id_from_url(url: str) -> str | None:
        parsed = urlsplit(url)
        match = re.search(r"/vacancy/(\d+)", parsed.path)
        if match:
            return match.group(1)
        query = parse_qs(parsed.query)
        values = query.get("vacancyId") or query.get("vacancy_id")
        return values[0] if values else None

    @staticmethod
    def _resume_id_from_url(url: str) -> str | None:
        parsed = urlsplit(url)
        match = re.search(r"/resume/([A-Za-z0-9_-]+)", parsed.path)
        return match.group(1) if match else None

    def _absolute_hh_url(self, url: str) -> str:
        absolute = urljoin(f"{self.site_base}/", url)
        parts = urlsplit(absolute)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @staticmethod
    def _text(node: Any) -> str:
        if not node:
            return ""
        return " ".join(node.get_text(" ", strip=True).split())

    @staticmethod
    def _decode_json_string(value: str) -> str:
        try:
            return json.loads(f'"{value}"')
        except json.JSONDecodeError:
            return value

    @staticmethod
    def _clean_resume_title(value: str) -> str:
        stop_phrases = (
            "Поднять вручную можно",
            "Поднять вручную",
            "Можно поднять",
        )
        for phrase in stop_phrases:
            value = value.replace(phrase, "")
        return " ".join(value.split())

    @staticmethod
    def _looks_like_login_page(html: str, url: str) -> bool:
        lowered_url = url.lower()
        lowered_html = html.lower()
        return (
            "/account/login" in lowered_url
            or "/account/signup" in lowered_url
            or 'data-qa="account-login"' in lowered_html
            or "войти в личный кабинет" in lowered_html
        )

    @staticmethod
    def _looks_already_applied(page_text: str) -> bool:
        phrases = (
            "вы откликнулись",
            "вы уже откликнулись",
            "вы уже откликались",
            "вы уже отправили отклик",
            "ваш отклик отправлен",
            "отклик уже отправлен",
            "отклик отправлен",
            "резюме отправлено",
        )
        return any(phrase in page_text for phrase in phrases)

    def _proxy_candidates(self) -> list[str | None]:
        mode = self.settings.hh_proxy_mode
        if mode == "direct_only" or not self.proxies:
            return [None]
        ordered = self.proxies[self._proxy_index :] + self.proxies[: self._proxy_index]
        proxies = [proxy for proxy in ordered if proxy not in self._blocked_proxies]
        if mode == "proxy_only":
            return proxies
        if mode == "proxy_then_direct":
            return [*proxies, None]
        return [None, *proxies]

    def _mark_proxy_active(self, proxy: str | None) -> None:
        if proxy and proxy in self.proxies:
            self._proxy_index = self.proxies.index(proxy)

    def _mark_proxy_blocked(self, proxy: str | None) -> None:
        self._blocked_proxies.add(proxy)

    def _should_try_next_route(self, error: HHApiError) -> bool:
        return error.status == 403 and len(self._proxy_candidates()) > 1

    def _log_hh_error(self, error: HHApiError) -> None:
        logger.warning(
            "HH HTML error: status=%s type=%s value=%s method=%s url=%s params=%s route=%s content_type=%s headers=%s",
            error.status,
            error.error_type,
            error.error_value,
            error.method,
            error.url,
            error.params,
            self._proxy_label(error.proxy),
            error.content_type,
            error.response_headers,
        )

    @staticmethod
    def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
        secret_keys = {"cookie", "authorization", "hh_session_cookie"}
        return {key: "***" if key.lower() in secret_keys else value for key, value in params.items()}

    @staticmethod
    def _useful_headers(headers: dict[str, str]) -> dict[str, str]:
        useful_names = ("x-request", "request-id", "trace", "x-trace", "x-hh", "content-type", "location")
        return {key: value for key, value in headers.items() if any(part in key.lower() for part in useful_names)}

    @staticmethod
    def _proxy_label(proxy: str | None) -> str:
        if not proxy:
            return "direct"
        parts = urlsplit(proxy)
        if not parts.hostname:
            return "proxy"
        netloc = parts.hostname
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        return urlunsplit((parts.scheme, netloc, "", "", ""))

    @staticmethod
    def explain_error(value: str) -> str:
        mapping = {
            "forbidden": "HH запретил доступ к HTML-странице с текущего сетевого маршрута. Обнови cookie, проверь VPN/прокси и попробуй открыть hh.ru из того же окружения.",
            "session_cookie_required": "Для личных страниц HH нужен HH_SESSION_COOKIE из авторизованного браузера.",
            "session_cookie_expired": "HH не принял cookie. Зайди на hh.ru в браузере, скопируй свежий Cookie и обнови .env.",
            "not_found": "HH не нашёл страницу вакансии. Возможно, вакансия удалена или скрыта.",
            "html_apply_disabled": "Автоотклики через HTML-версию пока отключены. Открой вакансию на HH и отправь отклик вручную.",
            "captcha_required": "HH запросил капчу. Автоматические действия остановлены, нужно действие вручную.",
            "limit_exceeded": "HH сообщил о превышении лимита. Автоотклики временно остановлены.",
            "already_applied": "На эту вакансию уже был отклик.",
            "resume_not_found": "Резюме не найдено. Обнови cookie и выбери резюме заново.",
            "archived": "Вакансия уже недоступна.",
            "invalid_vacancy": "Вакансия недоступна или скрыта.",
            "empty_message": "Сопроводительное письмо пустое. Нужно изменить текст.",
            "message_cannot_be_empty": "Сопроводительное письмо пустое. Нужно изменить текст.",
            "too_long_message": "Сопроводительное письмо слишком длинное. Нужно сократить текст.",
            "test_required": "Для вакансии нужен тест. Открой вакансию на HH и пройди его вручную.",
        }
        return mapping.get(value, "HH вернул ошибку. JobRadar сохранил её в журнал и не продолжил действие автоматически.")
