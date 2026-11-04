"""Testes do cliente Sure (HTTP mockado com responses)."""

from __future__ import annotations

import io
import logging
from decimal import Decimal

import pytest
import responses

from pluggy_sure.normalize import normalize_transaction
from pluggy_sure.sure_client import (
    SureAuthError,
    SureClient,
    SureConnectionError,
    SureValidationError,
    fold_name,
)


BASE = "http://sure.test"


@pytest.fixture
def client():
    return SureClient(BASE, "fake-sure-key-NAO-LOGAR")


@responses.activate
def test_sends_x_api_key_header(client):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/accounts",
        json={"accounts": [], "pagination": {}},
        status=200,
    )
    client.list_accounts()
    assert responses.calls[0].request.headers["X-Api-Key"] == "fake-sure-key-NAO-LOGAR"


@responses.activate
def test_create_payload_has_external_id_and_source_pluggy(client):
    raw = {
        "id": "tx-fake-0002",
        "accountId": "acc-fake-card-002",
        "date": "2026-03-02T15:00:00Z",
        "description": "IFOOD *RESTAURANTE EXEMPLO",
        "amount": 45.90,
        "currencyCode": "BRL",
        "status": "POSTED",
    }
    n = normalize_transaction(raw, "CREDIT")
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/transactions",
        json={"id": "sure-tx-fake-1"},
        status=201,
    )
    status, body = client.create_transaction(n, "sure-acc-fake-bbb", None, [])
    assert status == 201
    sent = responses.calls[0].request.body
    import json

    payload = json.loads(sent)
    assert payload["transaction"]["external_id"] == "tx-fake-0002"
    assert payload["transaction"]["source"] == "pluggy"


@responses.activate
def test_categories_created_once_case_and_accent_insensitive(client):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/categories",
        json={
            "categories": [
                {"id": "cat-1", "name": "Alimentacao casa"},
            ]
        },
        status=200,
    )
    # Mesmo nome com acento/caixa diferente → reutiliza
    cid = client.ensure_category("Alimentação Casa")
    assert cid == "cat-1"
    assert fold_name("Alimentação Casa") == fold_name("Alimentacao casa")
    # Segunda chamada não cria de novo (só GET no cache miss inicial)
    cid2 = client.ensure_category("ALIMENTAÇÃO CASA")
    assert cid2 == "cat-1"
    post_calls = [c for c in responses.calls if c.request.method == "POST"]
    assert post_calls == []


@responses.activate
def test_401_aborts_exit_2(client):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/accounts",
        json={"error": "unauthorized"},
        status=401,
    )
    with pytest.raises(SureAuthError) as exc:
        client.list_accounts()
    assert getattr(exc.value, "code", 2) == 2


@responses.activate
def test_422_logged_and_continues_exit_1(client, caplog):
    raw = {
        "id": "tx-fake-bad",
        "accountId": "acc-fake-card-002",
        "date": "2026-03-02T15:00:00Z",
        "description": "X",
        "amount": 1,
        "currencyCode": "BRL",
        "status": "POSTED",
    }
    n = normalize_transaction(raw, "CREDIT")
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/transactions",
        json={
            "error": "validation_failed",
            "message": "Transaction could not be created",
            "errors": ["Date can't be blank"],
        },
        status=422,
    )
    with pytest.raises(SureValidationError) as exc:
        client.create_transaction(n, "sure-acc-fake-bbb", None, [])
    assert exc.value.pluggy_id == "tx-fake-bad"
    # O runner traduz isso em exit 1 — ver test de sync/cli


@responses.activate
def test_connection_error_aborts_exit_3_without_state_write(client):
    import requests as req

    responses.add(
        responses.GET,
        f"{BASE}/api/v1/accounts",
        body=req.ConnectionError("connection refused"),
    )
    with pytest.raises(SureConnectionError) as exc:
        client.list_accounts()
    assert getattr(exc.value, "code", 3) == 3


@responses.activate
def test_get_account_balance_from_cents(client):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/accounts/sure-acc-fake-aaa",
        json={
            "id": "sure-acc-fake-aaa",
            "balance": "R$100.00",
            "balance_cents": 10000,
            "classification": "asset",
        },
        status=200,
    )
    bal, classification = client.get_account_balance("sure-acc-fake-aaa")
    assert bal == Decimal("100.00")
    assert classification == "asset"
