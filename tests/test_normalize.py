"""Testes de normalize (seção 10 da spec)."""

from __future__ import annotations

from decimal import Decimal

from pluggy_sure.normalize import (
    collapse_whitespace,
    normalize_transaction,
    to_decimal,
    utc_to_sao_paulo_date,
)


def test_utc_to_sao_paulo_date_crosses_midnight():
    assert utc_to_sao_paulo_date("2026-03-01T02:30:00Z").isoformat() == "2026-02-28"


def test_bank_negative_amount_is_expense():
    raw = {
        "id": "tx-fake-bank-neg",
        "accountId": "acc-fake-bank-001",
        "date": "2026-03-01T12:00:00Z",
        "description": "Saida",
        "amount": -50.00,
        "currencyCode": "BRL",
        "status": "POSTED",
    }
    n = normalize_transaction(raw, "BANK")
    assert n.amount == Decimal("50.00")
    assert n.nature == "expense"


def test_bank_positive_amount_is_income():
    raw = {
        "id": "tx-fake-bank-pos",
        "accountId": "acc-fake-bank-001",
        "date": "2026-03-01T12:00:00Z",
        "description": "Entrada",
        "amount": 50.00,
        "currencyCode": "BRL",
        "status": "POSTED",
    }
    n = normalize_transaction(raw, "BANK")
    assert n.amount == Decimal("50.00")
    assert n.nature == "income"


def test_credit_positive_amount_is_expense():
    raw = {
        "id": "tx-fake-card-pos",
        "accountId": "acc-fake-card-002",
        "date": "2026-03-02T15:00:00Z",
        "description": "Compra",
        "amount": 120.00,
        "currencyCode": "BRL",
        "status": "POSTED",
    }
    n = normalize_transaction(raw, "CREDIT")
    assert n.amount == Decimal("120.00")
    assert n.nature == "expense"


def test_credit_negative_amount_is_income_refund():
    raw = {
        "id": "tx-fake-card-neg",
        "accountId": "acc-fake-card-002",
        "date": "2026-03-02T15:00:00Z",
        "description": "Estorno",
        "amount": -33.00,
        "currencyCode": "BRL",
        "status": "POSTED",
    }
    n = normalize_transaction(raw, "CREDIT")
    assert n.amount == Decimal("33.00")
    assert n.nature == "income"


def test_foreign_currency_uses_brl_bill_amount(fixtures, account_types):
    raw = next(t for t in fixtures["pluggy_transactions"] if t["id"] == "tx-fake-0004")
    n = normalize_transaction(raw, account_types[raw["accountId"]])
    assert n.deferred is False
    assert n.amount == Decimal("110.00")
    assert n.currency == "BRL"
    assert n.nature == "expense"


def test_foreign_currency_without_brl_is_deferred(fixtures, account_types):
    raw = next(t for t in fixtures["pluggy_transactions"] if t["id"] == "tx-fake-0006")
    n = normalize_transaction(raw, account_types[raw["accountId"]])
    assert n.deferred is True
    assert n.defer_reason == "aguardando_valor_brl"


def test_description_whitespace_collapsed():
    assert collapse_whitespace("Pix enviado  CAIXA ECONOMICA") == (
        "Pix enviado CAIXA ECONOMICA"
    )
    raw = {
        "id": "tx-ws",
        "accountId": "a",
        "date": "2026-03-01T12:00:00Z",
        "description": "  foo   bar  ",
        "amount": -1,
        "currencyCode": "BRL",
        "status": "POSTED",
    }
    assert normalize_transaction(raw, "BANK").name == "foo bar"


def test_decimal_no_float_loss():
    assert to_decimal("0.1") + to_decimal("0.2") == to_decimal("0.3")
