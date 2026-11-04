"""Cliente Pluggy (somente leitura, exceto POST /auth).

Docs:
- Auth: https://docs.pluggy.ai/en/docs/authentication
- Contas: https://docs.pluggy.ai/en/reference/account/accounts-list
- Transações (cursor): https://docs.pluggy.ai/en/reference/transaction/transactions-list-by-cursor
- Conceitos/paginação: https://docs.pluggy.ai/en/reference/basic-concepts
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from pluggy_sure.logging_utils import redact_http_error, truncate_id

logger = logging.getLogger("pluggy_sure")

PLUGGY_BASE = "https://api.pluggy.ai"
BACKOFF_SECONDS = (1, 4, 16)
STALE_HOURS = 48


class PluggyAuthError(SystemExit):
    def __init__(self) -> None:
        super().__init__("credenciais Pluggy inválidas")


class PluggyRequestError(RuntimeError):
    pass


class PluggyClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        base_url: str = PLUGGY_BASE,
        timeout: float = 30.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._session = session or requests.Session()
        self._sleep = sleep
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._api_key: str | None = None

    def authenticate(self) -> str:
        """POST /auth → apiKey (válida ~2h)."""
        url = f"{self._base_url}/auth"
        try:
            resp = self._session.post(
                url,
                json={
                    "clientId": self._client_id,
                    "clientSecret": self._client_secret,
                },
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise PluggyRequestError(redact_http_error(exc)) from None

        if resp.status_code in (401, 403):
            raise PluggyAuthError()
        if resp.status_code >= 400:
            raise PluggyRequestError(
                f"auth falhou status={resp.status_code}"
            )
        data = resp.json()
        self._api_key = data["apiKey"]
        return self._api_key

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            self.authenticate()
        assert self._api_key
        return {
            "Content-Type": "application/json",
            "X-API-KEY": self._api_key,
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None
        for attempt, delay in enumerate((*BACKOFF_SECONDS, None)):
            try:
                resp = self._session.get(
                    url,
                    headers=self._headers(),
                    params=params,
                    timeout=self._timeout,
                )
            except requests.Timeout as exc:
                last_exc = exc
                if delay is None:
                    break
                logger.warning(
                    "timeout Pluggy path=%s attempt=%s", path, attempt + 1
                )
                self._sleep(delay)
                continue
            except requests.RequestException as exc:
                raise PluggyRequestError(redact_http_error(exc)) from None

            if resp.status_code in (401, 403):
                raise PluggyAuthError()
            if resp.status_code == 429 or resp.status_code >= 500:
                if delay is None:
                    raise PluggyRequestError(
                        f"Pluggy status={resp.status_code} path={path} após retries"
                    )
                logger.warning(
                    "retry Pluggy status=%s path=%s attempt=%s",
                    resp.status_code,
                    path,
                    attempt + 1,
                )
                self._sleep(delay)
                continue
            if resp.status_code >= 400:
                raise PluggyRequestError(
                    f"Pluggy status={resp.status_code} path={path}"
                )
            return resp.json()

        raise PluggyRequestError(
            f"Pluggy falhou após retries: {redact_http_error(last_exc or RuntimeError('unknown'))}"
        )

    def list_accounts(self, item_id: str) -> list[dict[str, Any]]:
        """GET /accounts?itemId=… (paginação por página)."""
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._get(
                "/accounts",
                params={"itemId": item_id, "page": page, "pageSize": 500},
            )
            batch = data.get("results") or []
            results.extend(batch)
            total_pages = int(data.get("totalPages") or 1)
            if page >= total_pages:
                break
            page += 1
        logger.info(
            "contas Pluggy item=%s count=%s",
            truncate_id(item_id),
            len(results),
        )
        return results

    def list_transactions(
        self,
        account_id: str,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """GET /v2/transactions com paginação por cursor (`next`)."""
        results: list[dict[str, Any]] = []
        params: dict[str, Any] = {"accountId": account_id}
        if date_from:
            params["dateFrom"] = date_from
        if date_to:
            params["dateTo"] = date_to

        data = self._get("/v2/transactions", params=params)
        results.extend(data.get("results") or [])
        nxt = data.get("next")

        while nxt:
            # Docs: append `next` as-is to the path
            # https://docs.pluggy.ai/en/reference/basic-concepts
            path = f"/v2/transactions{nxt}"
            data = self._get(path)
            results.extend(data.get("results") or [])
            nxt = data.get("next")

        logger.info(
            "transações Pluggy account=%s count=%s",
            truncate_id(account_id),
            len(results),
        )
        return results

    def get_item(self, item_id: str) -> dict[str, Any]:
        return self._get(f"/items/{item_id}")

    def warn_if_stale(self, item: dict[str, Any], now: datetime | None = None) -> bool:
        """Avisa se lastUpdatedAt > 48h. Retorna True se stale."""
        now = now or datetime.now(timezone.utc)
        raw = item.get("lastUpdatedAt")
        if not raw:
            return False
        text = str(raw)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        last = datetime.fromisoformat(text)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_h = (now - last).total_seconds() / 3600
        if age_h > STALE_HOURS:
            logger.warning(
                "item Pluggy desatualizado id=%s lastUpdatedAt=%s",
                truncate_id(str(item.get("id", ""))),
                raw,
            )
            return True
        return False

    def assert_read_only_except_auth(self, method: str, path: str) -> None:
        """Garante política de somente leitura (exceto POST /auth)."""
        m = method.upper()
        if m == "GET":
            return
        if m == "POST" and path.rstrip("/").endswith("/auth"):
            return
        raise PluggyRequestError(
            f"método não permitido na Pluggy: {m} {path}"
        )
