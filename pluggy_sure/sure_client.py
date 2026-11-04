"""Cliente Sure (we-promise/sure).

Fontes verificadas (commit ~630711e / main):
- Auth X-Api-Key: app/controllers/api/v1/base_controller.rb
- Transações: app/controllers/api/v1/transactions_controller.rb
  docs: https://github.com/we-promise/sure/blob/main/docs/api/transactions.md
- Contas (com balance/balance_cents): app/views/api/v1/accounts/_account.json.jbuilder
- Categorias / tags: categories_controller.rb, tags_controller.rb
- Idempotência: external_id + source no POST (índice account_id, source, external_id)
"""

from __future__ import annotations

import logging
import unicodedata
from decimal import Decimal
from typing import Any

import requests

from pluggy_sure.logging_utils import redact_http_error, truncate_id
from pluggy_sure.normalize import NormalizedTx

logger = logging.getLogger("pluggy_sure")

SOURCE_PLUGGY = "pluggy"


class SureAuthError(SystemExit):
    def __init__(self) -> None:
        super().__init__("credenciais Sure inválidas")
        self.code = 2


class SureConnectionError(SystemExit):
    def __init__(self) -> None:
        super().__init__("Sure fora do ar ou inacessível")
        self.code = 3


class SureValidationError(Exception):
    def __init__(self, pluggy_id: str, message: str) -> None:
        self.pluggy_id = pluggy_id
        self.message = message
        super().__init__(f"pluggy_id={pluggy_id} {message}")


def fold_name(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).casefold().strip()


class SureClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout = timeout
        self._category_cache: dict[str, str] = {}  # fold(name) → id
        self._tag_cache: dict[str, str] = {}

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Api-Key": self._api_key,
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any] | list[Any] | None]:
        url = f"{self._base}{path}"
        try:
            resp = self._session.request(
                method,
                url,
                headers=self._headers(),
                json=json,
                params=params,
                timeout=self._timeout,
            )
        except requests.ConnectionError as exc:
            logger.error("Sure conexão falhou: %s", redact_http_error(exc))
            raise SureConnectionError() from None
        except requests.Timeout as exc:
            logger.error("Sure timeout: %s", redact_http_error(exc))
            raise SureConnectionError() from None
        except requests.RequestException as exc:
            logger.error("Sure erro: %s", redact_http_error(exc))
            raise SureConnectionError() from None

        if resp.status_code == 401:
            raise SureAuthError()

        body: dict[str, Any] | list[Any] | None
        if resp.content:
            try:
                body = resp.json()
            except ValueError:
                body = None
        else:
            body = None
        return resp.status_code, body

    # --- Contas ---
    def list_accounts(self) -> list[dict[str, Any]]:
        status, body = self._request("GET", "/api/v1/accounts", params={"per_page": 100})
        if status >= 400 or not isinstance(body, dict):
            raise SureConnectionError()
        return list(body.get("accounts") or [])

    def get_account_balance(self, account_id: str) -> tuple[Decimal, str | None]:
        """Retorna (balance_decimal, classification). Usa balance_cents se disponível."""
        status, body = self._request("GET", f"/api/v1/accounts/{account_id}")
        if status == 404:
            raise SureValidationError(account_id, "conta Sure não encontrada")
        if status >= 400 or not isinstance(body, dict):
            raise SureConnectionError()
        # Preferir balance_cents (inteiro) → Decimal
        if "balance_cents" in body and body["balance_cents"] is not None:
            cents = Decimal(int(body["balance_cents"]))
            bal = cents / Decimal(100)
        elif "balance" in body:
            # formatado — fallback frágil; preferir cents
            raw = str(body["balance"]).replace("$", "").replace("R$", "").replace(",", "")
            bal = Decimal(raw.strip() or "0")
        else:
            bal = Decimal("0")
        return bal, body.get("classification")

    # --- Categorias ---
    def list_categories(self) -> list[dict[str, Any]]:
        status, body = self._request("GET", "/api/v1/categories", params={"per_page": 100})
        if status >= 400:
            raise SureConnectionError()
        if isinstance(body, dict):
            return list(body.get("categories") or body.get("data") or [])
        if isinstance(body, list):
            return body
        return []

    def ensure_category(self, name: str, parent_id: str | None = None) -> str | None:
        """Cria categoria se não existir (comparação sem acento/caixa). Retorna id."""
        if not name:
            return None
        key = fold_name(name)
        if key in self._category_cache:
            return self._category_cache[key]

        for cat in self.list_categories():
            cid = str(cat["id"])
            self._category_cache[fold_name(str(cat.get("name") or ""))] = cid

        if key in self._category_cache:
            return self._category_cache[key]

        payload: dict[str, Any] = {"category": {"name": name}}
        if parent_id:
            payload["category"]["parent_id"] = parent_id
        status, body = self._request("POST", "/api/v1/categories", json=payload)
        if status in (200, 201) and isinstance(body, dict):
            cid = str(body.get("id") or body.get("category", {}).get("id"))
            self._category_cache[key] = cid
            logger.info("categoria Sure criada id=%s", truncate_id(cid))
            return cid
        # Se 422 por duplicata, relê
        for cat in self.list_categories():
            self._category_cache[fold_name(str(cat.get("name") or ""))] = str(cat["id"])
        return self._category_cache.get(key)

    # --- Tags ---
    def list_tags(self) -> list[dict[str, Any]]:
        status, body = self._request("GET", "/api/v1/tags", params={"per_page": 100})
        if status >= 400:
            return []
        if isinstance(body, dict):
            return list(body.get("tags") or body.get("data") or [])
        if isinstance(body, list):
            return body
        return []

    def ensure_tag(self, name: str) -> str | None:
        key = fold_name(name)
        if key in self._tag_cache:
            return self._tag_cache[key]
        for tag in self.list_tags():
            self._tag_cache[fold_name(str(tag.get("name") or ""))] = str(tag["id"])
        if key in self._tag_cache:
            return self._tag_cache[key]
        status, body = self._request(
            "POST", "/api/v1/tags", json={"tag": {"name": name}}
        )
        if status in (200, 201) and isinstance(body, dict):
            tid = str(body.get("id") or body.get("tag", {}).get("id"))
            self._tag_cache[key] = tid
            return tid
        for tag in self.list_tags():
            self._tag_cache[fold_name(str(tag.get("name") or ""))] = str(tag["id"])
        return self._tag_cache.get(key)

    def resolve_tag_ids(self, names: list[str]) -> list[str]:
        ids: list[str] = []
        for n in names:
            tid = self.ensure_tag(n)
            if tid:
                ids.append(tid)
        return ids

    # --- Transações ---
    def build_create_payload(
        self,
        tx: NormalizedTx,
        sure_account_id: str,
        category_id: str | None,
        tag_ids: list[str],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "account_id": sure_account_id,
            "date": tx.local_date.isoformat(),
            "amount": float(tx.amount),  # API JSON number; origem é Decimal
            "name": tx.name,
            "nature": tx.nature,
            "currency": "BRL",
            "notes": tx.notes,
            "external_id": tx.pluggy_id,
            "source": SOURCE_PLUGGY,
        }
        if category_id:
            body["category_id"] = category_id
        if tag_ids:
            body["tag_ids"] = tag_ids
        return {"transaction": body}

    def create_transaction(
        self,
        tx: NormalizedTx,
        sure_account_id: str,
        category_id: str | None,
        tag_ids: list[str],
    ) -> tuple[int, dict[str, Any]]:
        payload = self.build_create_payload(tx, sure_account_id, category_id, tag_ids)
        status, body = self._request("POST", "/api/v1/transactions", json=payload)
        if status == 422:
            msg = "validation_failed"
            if isinstance(body, dict):
                msg = str(body.get("message") or msg)
            raise SureValidationError(tx.pluggy_id, msg)
        if status not in (200, 201) or not isinstance(body, dict):
            raise SureConnectionError()
        logger.info(
            "Sure POST transaction status=%s pluggy_id=%s sure_id=%s",
            status,
            truncate_id(tx.pluggy_id),
            truncate_id(str(body.get("id", ""))),
        )
        return status, body

    def update_transaction(
        self,
        sure_id: str,
        tx: NormalizedTx,
        category_id: str | None,
        tag_ids: list[str],
        *,
        skip_category: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "date": tx.local_date.isoformat(),
            "amount": float(tx.amount),
            "name": tx.name,
            "nature": tx.nature,
            "notes": tx.notes,
            "tag_ids": tag_ids,
        }
        if not skip_category and category_id:
            body["category_id"] = category_id
        status, resp = self._request(
            "PATCH", f"/api/v1/transactions/{sure_id}", json={"transaction": body}
        )
        if status == 422:
            msg = "validation_failed"
            if isinstance(resp, dict):
                msg = str(resp.get("message") or msg)
            raise SureValidationError(tx.pluggy_id, msg)
        if status >= 400 or not isinstance(resp, dict):
            raise SureConnectionError()
        logger.info(
            "Sure PATCH transaction sure_id=%s pluggy_id=%s",
            truncate_id(sure_id),
            truncate_id(tx.pluggy_id),
        )
        return resp

    def add_tags_to_transaction(self, sure_id: str, tag_names: list[str]) -> None:
        tag_ids = self.resolve_tag_ids(tag_names)
        if not tag_ids:
            return
        # PATCH só com tag_ids — merge não é nativo; caller deve passar lista completa.
        status, _ = self._request(
            "PATCH",
            f"/api/v1/transactions/{sure_id}",
            json={"transaction": {"tag_ids": tag_ids}},
        )
        if status >= 400:
            logger.warning(
                "falha ao taguear sure_id=%s status=%s",
                truncate_id(sure_id),
                status,
            )
