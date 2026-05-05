from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

import aiohttp

from src.config import Settings
from src.crypto import TokenCipher
from src.database import Database
from src.models import Salary, Vacancy
from src.scoring import SEARCH_QUERIES
from src.utils import clean_html


logger = logging.getLogger(__name__)


class HHApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, error_type: str | None = None, error_value: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.error_value = error_value


class HHAuthRequiredError(HHApiError):
    pass


class HHClient:
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
        self._cipher: TokenCipher | None = None

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers=self._base_headers())

    async def close(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()
        self.session = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            raise RuntimeError("HHClient session is not started")
        return self.session

    def _base_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.settings.hh_user_agent,
            "HH-User-Agent": self.settings.hh_user_agent,
            "Accept": "application/json",
        }

    def _cipher_or_raise(self) -> TokenCipher:
        if self._cipher is None:
            self._cipher = TokenCipher(self.settings.hh_token_encryption_key)
        return self._cipher

    async def ping(self) -> bool:
        try:
            await self.hh_request(
                None,
                "/vacancies",
                params={"host": self.settings.hh_host, "text": "test", "area": self.settings.hh_area, "per_page": 1},
                auth=False,
                timeout=10,
            )
            return True
        except (HHApiError, aiohttp.ClientError, asyncio.TimeoutError):
            logger.warning("HH API ping failed")
            return False

    async def hh_request(
        self,
        telegram_user_id: int | None,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        auth: bool = True,
        timeout: int = 20,
        retry_on_expired: bool = True,
    ) -> dict[str, Any] | str:
        headers = self._base_headers()
        if auth:
            if telegram_user_id is None:
                raise HHAuthRequiredError("Для запроса нужен пользователь HH.", error_type="oauth", error_value="user_auth_expected")
            token = await self.refresh_access_token_if_needed(telegram_user_id)
            headers["Authorization"] = f"Bearer {token}"

        url = path if path.startswith("http") else urljoin(f"{self.settings.hh_api_base}/", path.lstrip("/"))
        request_params = {"host": self.settings.hh_host, **(params or {})}
        return await self._request_json_or_text(
            method=method,
            url=url,
            headers=headers,
            params=request_params,
            data=data,
            timeout=timeout,
            telegram_user_id=telegram_user_id,
            retry_on_expired=retry_on_expired,
            auth=auth,
        )

    async def _request_json_or_text(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any],
        data: dict[str, Any] | None,
        timeout: int,
        telegram_user_id: int | None,
        retry_on_expired: bool,
        auth: bool,
    ) -> dict[str, Any] | str:
        session = self._get_session()
        last_error: Exception | None = None
        for proxy in self._proxy_candidates():
            try:
                async with session.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    proxy=proxy,
                ) as response:
                    if response.status in {204, 201}:
                        self._mark_proxy_active(proxy)
                        return {"ok": True, "location": response.headers.get("Location")}
                    content_type = response.headers.get("Content-Type", "")
                    payload: dict[str, Any] | str
                    if "application/json" in content_type:
                        payload = await response.json()
                    else:
                        payload = await response.text()
                    if response.status >= 400:
                        error = self._build_error(response.status, payload)
                        if (
                            auth
                            and retry_on_expired
                            and telegram_user_id is not None
                            and error.error_value in {"token_expired", "token-expired"}
                        ):
                            await self.refresh_access_token_if_needed(telegram_user_id, force=True)
                            return await self.hh_request(
                                telegram_user_id,
                                url,
                                method=method,
                                params={key: value for key, value in params.items() if key != "host"},
                                data=data,
                                auth=True,
                                timeout=timeout,
                                retry_on_expired=False,
                            )
                        logger.warning(
                            "HH API error: status=%s type=%s value=%s url=%s",
                            error.status,
                            error.error_type,
                            error.error_value,
                            url,
                        )
                        raise error
                    self._mark_proxy_active(proxy)
                    return payload
            except (aiohttp.ClientError, asyncio.TimeoutError, HHApiError) as exc:
                last_error = exc
                if proxy:
                    self._mark_proxy_blocked(proxy)
                if isinstance(exc, HHApiError):
                    raise
        if last_error:
            raise last_error
        raise HHApiError("HH API сейчас недоступен.")

    def _build_error(self, status: int, payload: dict[str, Any] | str) -> HHApiError:
        error_type: str | None = None
        error_value: str | None = None
        if isinstance(payload, dict):
            errors = payload.get("errors") or []
            if errors and isinstance(errors[0], dict):
                error_type = str(errors[0].get("type") or "")
                error_value = str(errors[0].get("value") or "")
            oauth_error = payload.get("oauth_error")
            if oauth_error:
                error_type = "oauth"
                error_value = str(oauth_error).replace("-", "_")
        message = self.explain_error(error_value or error_type or str(status))
        if error_type == "oauth" or error_value in {"token_revoked", "token_expired", "token-revoked", "token-expired"}:
            return HHAuthRequiredError(message, status=status, error_type=error_type, error_value=error_value)
        return HHApiError(message, status=status, error_type=error_type, error_value=error_value)

    async def refresh_access_token_if_needed(self, telegram_user_id: int, force: bool = False) -> str:
        row = self.db.get_hh_token_row(telegram_user_id)
        if not row:
            raise HHAuthRequiredError("HH пока не подключен. Сначала подключи аккаунт в разделе «HH подключение».")
        expires_at = datetime.fromisoformat(row["expires_at"])
        cipher = self._cipher_or_raise()
        if not force and expires_at > datetime.now(timezone.utc) + timedelta(minutes=3):
            return cipher.decrypt(row["access_token_encrypted"])

        refresh_token = cipher.decrypt(row["refresh_token_encrypted"])
        payload = await self.exchange_refresh_token(refresh_token)
        access_token = str(payload["access_token"])
        new_refresh_token = str(payload.get("refresh_token") or refresh_token)
        expires_in = int(payload.get("expires_in") or 3600)
        self.db.save_hh_tokens(
            telegram_user_id=telegram_user_id,
            hh_user_id=row["hh_user_id"],
            hh_user_type=row["hh_user_type"],
            access_token_encrypted=cipher.encrypt(access_token),
            refresh_token_encrypted=cipher.encrypt(new_refresh_token),
            expires_at=(datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat(timespec="seconds"),
        )
        return access_token

    async def exchange_code(self, code: str) -> dict[str, Any]:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": self.settings.hh_client_id,
                "client_secret": self.settings.hh_client_secret,
                "redirect_uri": self.settings.hh_redirect_uri,
                "code": code,
            }
        )

    async def exchange_refresh_token(self, refresh_token: str) -> dict[str, Any]:
        return await self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )

    async def _token_request(self, data: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.hh_client_id or not self.settings.hh_client_secret:
            raise HHApiError("Не настроены идентификатор клиента и секрет клиента приложения HH.")
        session = self._get_session()
        headers = {**self._base_headers(), "Content-Type": "application/x-www-form-urlencoded"}
        async with session.post(
            self.settings.hh_token_url,
            data=data,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            payload = await response.json()
            if response.status >= 400:
                raise self._build_error(response.status, payload)
            return payload

    async def save_oauth_tokens(self, telegram_user_id: int, token_payload: dict[str, Any]) -> dict[str, Any]:
        cipher = self._cipher_or_raise()
        access_token = str(token_payload["access_token"])
        refresh_token = str(token_payload["refresh_token"])
        expires_in = int(token_payload.get("expires_in") or 3600)
        me = await self.get_me_with_token(access_token)
        self.db.save_hh_tokens(
            telegram_user_id=telegram_user_id,
            hh_user_id=str(me.get("id") or ""),
            hh_user_type=str(me.get("user_type") or ""),
            access_token_encrypted=cipher.encrypt(access_token),
            refresh_token_encrypted=cipher.encrypt(refresh_token),
            expires_at=(datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat(timespec="seconds"),
        )
        return me

    async def get_me_with_token(self, access_token: str) -> dict[str, Any]:
        headers = {**self._base_headers(), "Authorization": f"Bearer {access_token}"}
        result = await self._request_json_or_text(
            method="GET",
            url=urljoin(f"{self.settings.hh_api_base}/", "me"),
            headers=headers,
            params={"host": self.settings.hh_host},
            data=None,
            timeout=20,
            telegram_user_id=None,
            retry_on_expired=False,
            auth=False,
        )
        return result if isinstance(result, dict) else {}

    async def get_me(self, telegram_user_id: int) -> dict[str, Any]:
        result = await self.hh_request(telegram_user_id, "/me")
        return result if isinstance(result, dict) else {}

    async def get_my_resumes(self, telegram_user_id: int) -> list[dict[str, Any]]:
        result = await self.hh_request(telegram_user_id, "/resumes/mine")
        if isinstance(result, dict):
            return list(result.get("items") or [])
        return []

    async def get_vacancy(self, telegram_user_id: int | None, vacancy_id: str, resume_id: str | None = None) -> dict[str, Any]:
        params = {"resume_id": resume_id} if resume_id else None
        result = await self.hh_request(telegram_user_id, f"/vacancies/{vacancy_id}", params=params, auth=telegram_user_id is not None)
        return result if isinstance(result, dict) else {}

    async def search_vacancies(self, telegram_user_id: int | None, params: dict[str, Any]) -> dict[str, Any]:
        result = await self.hh_request(telegram_user_id, "/vacancies", params=params, auth=False)
        return result if isinstance(result, dict) else {}

    async def apply_to_vacancy(self, telegram_user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        action_url = payload.pop("action_url", None)
        path = action_url or "/negotiations"
        return await self.hh_request(
            telegram_user_id,
            path,
            method="POST",
            data=payload,
            auth=True,
            timeout=20,
        )

    async def search_all(self, ignored_external_ids: set[str] | None = None, keywords: list[str] | None = None, areas: list[str] | None = None, only_remote: bool = True) -> AsyncGenerator[Vacancy, None]:
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
                detail = await self.get_vacancy(None, external_id)
                yield self._parse_vacancy(item, detail)
                await asyncio.sleep(0.8)

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

    def _proxy_candidates(self) -> list[str | None]:
        if not self.proxies:
            return [None]
        ordered = self.proxies[self._proxy_index :] + self.proxies[: self._proxy_index]
        return [proxy for proxy in ordered if proxy not in self._blocked_proxies]

    def _mark_proxy_active(self, proxy: str | None) -> None:
        if proxy and proxy in self.proxies:
            self._proxy_index = self.proxies.index(proxy)

    def _mark_proxy_blocked(self, proxy: str | None) -> None:
        self._blocked_proxies.add(proxy)

    @staticmethod
    def explain_error(value: str) -> str:
        mapping = {
            "captcha_required": "HH запросил капчу. Автоотклики остановлены, нужно действие вручную.",
            "limit_exceeded": "HH сообщил о превышении лимита. Автоотклики временно остановлены.",
            "already_applied": "На эту вакансию уже был отклик.",
            "resume_not_found": "Резюме не найдено. Выбери резюме заново в разделе «HH подключение».",
            "resume_deleted": "Резюме скрыто или удалено. Выбери другое резюме.",
            "archived": "Вакансия уже недоступна.",
            "invalid_vacancy": "Вакансия недоступна или скрыта.",
            "empty_message": "Сопроводительное письмо пустое. Нужно изменить текст.",
            "message_cannot_be_empty": "Сопроводительное письмо пустое. Нужно изменить текст.",
            "too_long_message": "Сопроводительное письмо слишком длинное. Нужно сократить текст.",
            "token_expired": "Сессия HH устарела, пробую обновить подключение.",
            "token_revoked": "HH-подключение отозвано. Нужно подключить HH заново.",
            "token-expired": "Сессия HH устарела, пробую обновить подключение.",
            "token-revoked": "HH-подключение отозвано. Нужно подключить HH заново.",
            "bad_authorization": "HH не принял авторизацию. Подключи аккаунт заново.",
            "test_required": "Для вакансии нужен тест. Через API такой отклик не отправляю, открой вакансию на HH.",
        }
        return mapping.get(value, "HH вернул ошибку. Я сохранил её в журнал и не стал продолжать автоматически.")
