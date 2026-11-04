"""Testes do categorizador (seção 10)."""

from __future__ import annotations

import os
from pathlib import Path

from pluggy_sure.categorize import categorize, load_rules
from pluggy_sure.normalize import normalize_transaction


def _by_id(fixtures, account_types, tx_id):
    raw = next(t for t in fixtures["pluggy_transactions"] if t["id"] == tx_id)
    return normalize_transaction(raw, account_types[raw["accountId"]])


def test_rules_first_match_wins(tmp_path):
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(
        """
rules:
  - match: "IFOOD"
    category: Primeira
  - match: "IFOOD"
    category: Segunda
""",
        encoding="utf-8",
    )
    rules = load_rules(rules_file)
    raw = {
        "id": "x",
        "accountId": "a",
        "date": "2026-03-02T15:00:00Z",
        "description": "IFOOD *TESTE",
        "amount": 10,
        "currencyCode": "BRL",
        "status": "POSTED",
    }
    n = normalize_transaction(raw, "CREDIT")
    assert categorize(n, rules).category == "Primeira"


def test_pix_to_caixa_is_loterias_not_pix_a_pessoas(fixtures, account_types, rules):
    n = _by_id(fixtures, account_types, "tx-fake-0001")
    assert categorize(n, rules).category == "Loterias"


def test_progames_is_lazer(fixtures, account_types, rules):
    n = _by_id(fixtures, account_types, "tx-fake-0003")
    assert categorize(n, rules).category == "Lazer"


def test_ifood_is_restaurantes(fixtures, account_types, rules):
    n = _by_id(fixtures, account_types, "tx-fake-0002")
    assert categorize(n, rules).category == "Restaurantes e delivery"


def test_oxxo_is_alimentacao_casa(fixtures, account_types, rules):
    n = _by_id(fixtures, account_types, "tx-fake-0010")
    assert categorize(n, rules).category == "Alimentação casa"


def test_daiso_is_compras(fixtures, account_types, rules):
    n = _by_id(fixtures, account_types, "tx-fake-0011")
    assert categorize(n, rules).category == "Compras"


def test_own_transfer_is_transferencias_proprias(fixtures, account_types, rules):
    n = _by_id(fixtures, account_types, "tx-fake-0008")
    r = categorize(n, rules)
    assert r.category == "Transferências entre contas próprias"
    assert r.fora_envelope is True


def test_card_bill_payment_on_bank_is_outside_envelopes(fixtures, account_types, rules):
    n = _by_id(fixtures, account_types, "tx-fake-0007")
    r = categorize(n, rules)
    assert r.fora_envelope is True
    assert "fatura" in r.category.casefold() or r.category == "Pagamento de fatura"


def test_rdb_cdb_is_investimentos(fixtures, account_types, rules):
    n = _by_id(fixtures, account_types, "tx-fake-0009")
    r = categorize(n, rules)
    assert r.category == "Investimentos"
    assert r.fora_envelope is True


def test_interest_and_iof_is_dividas(rules):
    raw = {
        "id": "tx-juros",
        "accountId": "acc-fake-bank-001",
        "date": "2026-03-01T12:00:00Z",
        "description": "Juros cheque especial",
        "amount": -15.00,
        "currencyCode": "BRL",
        "status": "POSTED",
    }
    n = normalize_transaction(raw, "BANK")
    r = categorize(n, rules)
    assert r.category == "Parcelas/Dívidas"
    assert r.fora_envelope is True


def test_international_iof_follows_digital_service(fixtures, account_types, rules):
    n = _by_id(fixtures, account_types, "tx-fake-0005")
    r = categorize(n, rules)
    assert r.category == "Assinaturas"


def test_unmatched_pix_out_is_pix_a_pessoas(fixtures, account_types, rules):
    n = _by_id(fixtures, account_types, "tx-fake-0012")
    assert categorize(n, rules).category == "Pix a pessoas"


def test_unmatched_bank_out_is_outros_and_card_is_compras(rules):
    bank = normalize_transaction(
        {
            "id": "b1",
            "accountId": "a",
            "date": "2026-03-01T12:00:00Z",
            "description": "XYZ SEM REGRA BANCO",
            "amount": -10,
            "currencyCode": "BRL",
            "status": "POSTED",
        },
        "BANK",
    )
    card = normalize_transaction(
        {
            "id": "c1",
            "accountId": "a",
            "date": "2026-03-01T12:00:00Z",
            "description": "XYZ SEM REGRA CARTAO",
            "amount": 10,
            "currencyCode": "BRL",
            "status": "POSTED",
        },
        "CREDIT",
    )
    assert categorize(bank, rules).category == "Outros"
    assert categorize(card, rules).category == "Compras"


def test_pluggy_category_fallback_groceries(rules):
    raw = {
        "id": "g1",
        "accountId": "a",
        "date": "2026-03-01T12:00:00Z",
        "description": "COMPRA QUALQUER SEM MATCH",
        "amount": -20,
        "currencyCode": "BRL",
        "category": "Groceries",
        "status": "POSTED",
    }
    n = normalize_transaction(raw, "BANK")
    assert categorize(n, rules).category == "Alimentação casa"


def test_rules_loaded_from_yaml_path_env(monkeypatch, rules_path):
    monkeypatch.setenv("SYNC_RULES_PATH", str(rules_path))
    loaded = load_rules(Path(os.environ["SYNC_RULES_PATH"]))
    assert len(loaded) > 0
