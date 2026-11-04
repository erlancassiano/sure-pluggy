"""Testes do cliente Pluggy (HTTP mockado)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import responses

from pluggy_sure.pluggy_client import (
    PLUGGY_BASE,
    PluggyAuthError,
    PluggyClient,
    PluggyRequestError,
)


@pytest.fixture
def client():
    sleeps: list[float] = []
    c = PluggyClient(
        "fake-client-id",
        "fake-secret-NAO-LOGAR",
        sleep=sleeps.append,
    )
    c._sleeps = sleeps
    return c


def _auth_ok():
    responses.add(
        responses.POST,
        f"{PLUGGY_BASE}/auth",
        json={"apiKey": "fake-api-key-token"},
        status=200,
    )


@responses.activate
def test_auth_uses_env_credentials(client, env_falso):
    _auth_ok()
    key = client.authenticate()
    assert key == "fake-api-key-token"
    import json

    body = json.loads(responses.calls[0].request.body)
    assert body["clientId"] == "fake-client-id"
    assert body["clientSecret"] == "fake-secret-NAO-LOGAR"


@responses.activate
def test_pagination_follows_all_pages(client):
    _auth_ok()
    responses.add(
        responses.GET,
        f"{PLUGGY_BASE}/v2/transactions",
        json={
            "results": [{"id": "tx-page-1"}],
            "next": "?accountId=acc-fake-bank-001&after=CURSOR1",
        },
        status=200,
        match=[
            responses.matchers.query_param_matcher(
                {"accountId": "acc-fake-bank-001"}, strict_match=False
            )
        ],
    )
    # Segunda página: path já com query string do next
    responses.add(
        responses.GET,
        f"{PLUGGY_BASE}/v2/transactions",
        json={"results": [{"id": "tx-page-2"}], "next": None},
        status=200,
    )
    txs = client.list_transactions("acc-fake-bank-001")
    assert [t["id"] for t in txs] == ["tx-page-1", "tx-page-2"]
    assert len(responses.calls) >= 3  # auth + 2 gets


@responses.activate
def test_retries_on_429_with_backoff(client):
    _auth_ok()
    responses.add(
        responses.GET,
        f"{PLUGGY_BASE}/accounts",
        json={"message": "rate limit"},
        status=429,
    )
    responses.add(
        responses.GET,
        f"{PLUGGY_BASE}/accounts",
        json={"message": "rate limit"},
        status=429,
    )
    responses.add(
        responses.GET,
        f"{PLUGGY_BASE}/accounts",
        json={"results": [], "totalPages": 1, "page": 1},
        status=200,
    )
    client.list_accounts("item-fake-01")
    assert client._sleeps == [1, 4]


@responses.activate
def test_stale_item_warns_with_last_update_date(client, caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="pluggy_sure")
    item = {
        "id": "item-fake-02",
        "lastUpdatedAt": "2026-05-01T09:00:00Z",
    }
    now = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    assert client.warn_if_stale(item, now=now) is True
    assert "desatualizado" in caplog.text
    assert "2026-05-01" in caplog.text


def test_never_calls_non_get_methods(client):
    client.assert_read_only_except_auth("GET", "/accounts")
    client.assert_read_only_except_auth("POST", "/auth")
    with pytest.raises(PluggyRequestError):
        client.assert_read_only_except_auth("POST", "/transactions")
    with pytest.raises(PluggyRequestError):
        client.assert_read_only_except_auth("DELETE", "/accounts/x")
    with pytest.raises(PluggyRequestError):
        client.assert_read_only_except_auth("PATCH", "/transactions/x")


@responses.activate
def test_pluggy_401_raises_auth_error(client):
    responses.add(
        responses.POST,
        f"{PLUGGY_BASE}/auth",
        json={"error": "unauthorized"},
        status=401,
    )
    with pytest.raises(PluggyAuthError):
        client.authenticate()
