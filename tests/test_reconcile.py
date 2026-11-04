"""Testes de conciliação de saldo e pareamento."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pluggy_sure.normalize import NormalizedTx
from pluggy_sure.reconcile import (
    match_own_transfers_and_bill_payments,
    reconcile_balance,
)


def test_reconcile_balance_flags_diff_over_1_real():
    # |diff| == 1.00 → não sinaliza
    ok = reconcile_balance(
        "acc-fake-bank-001",
        "sure-acc-fake-aaa",
        "BANK",
        Decimal("100.00"),
        Decimal("99.00"),
    )
    assert ok.diff_abs == Decimal("1.00")
    assert ok.needs_review is False

    # |diff| == 1.01 → sinaliza
    bad = reconcile_balance(
        "acc-fake-bank-001",
        "sure-acc-fake-aaa",
        "BANK",
        Decimal("100.00"),
        Decimal("98.99"),
    )
    assert bad.diff_abs == Decimal("1.01")
    assert bad.needs_review is True

    # Igual → não sinaliza
    eq = reconcile_balance(
        "acc-fake-bank-001",
        "sure-acc-fake-aaa",
        "BANK",
        Decimal("50.00"),
        Decimal("50.00"),
    )
    assert eq.needs_review is False


def test_reconcile_credit_card_normalizes_open_balance():
    """Cartão: ambos lados normalizados para fatura em aberto (positivo = deve)."""
    # Pluggy +500 devido; Sure liability +500
    d = reconcile_balance(
        "acc-fake-card-002",
        "sure-acc-fake-bbb",
        "CREDIT",
        Decimal("500.00"),
        Decimal("500.00"),
        sure_classification="liability",
    )
    assert d.needs_review is False
    # Sure com sinal invertido na API ainda compara após abs
    d2 = reconcile_balance(
        "acc-fake-card-002",
        "sure-acc-fake-bbb",
        "CREDIT",
        Decimal("500.00"),
        Decimal("-500.00"),
        sure_classification="liability",
    )
    assert d2.needs_review is False


def _tx(**kwargs) -> NormalizedTx:
    defaults = dict(
        pluggy_id="x",
        account_id="acc-fake-bank-001",
        account_type="BANK",
        local_date=date(2026, 3, 6),
        amount=Decimal("300.00"),
        nature="expense",
        name="Transferência mesma titularidade",
        currency="BRL",
        status="POSTED",
        pluggy_category="Same person transfer",
        notes="",
    )
    defaults.update(kwargs)
    return NormalizedTx(**defaults)


def test_match_own_transfer_exact_decimal_within_3_days():
    mapped = {"acc-fake-bank-001", "acc-fake-bank-other"}
    out = _tx(
        pluggy_id="out-1",
        account_id="acc-fake-bank-001",
        nature="expense",
        amount=Decimal("300.00"),
        local_date=date(2026, 3, 6),
    )
    inn = _tx(
        pluggy_id="in-1",
        account_id="acc-fake-bank-other",
        nature="income",
        amount=Decimal("300.00"),
        local_date=date(2026, 3, 8),  # +2 dias
        name="Transferência recebida",
    )
    pairs, unpaired = match_own_transfers_and_bill_payments(
        [out, inn], mapped, match_days=3
    )
    assert len(pairs) == 1
    assert pairs[0].kind == "own_transfer"
    assert not unpaired


def test_match_bill_payment_bank_to_card():
    # Conta Azul: pagamento de fatura = transferência CC → cartão
    # https://ajuda.contaazul.com/hc/pt-br/articles/46019129874957
    mapped = {"acc-fake-bank-001", "acc-fake-card-002"}
    out = _tx(
        pluggy_id="bill-out",
        account_id="acc-fake-bank-001",
        account_type="BANK",
        nature="expense",
        amount=Decimal("500.00"),
        local_date=date(2026, 3, 6),
        name="Pagamento de fatura CARTAO EXEMPLO",
        pluggy_category="Credit card payment",
    )
    inn = _tx(
        pluggy_id="bill-in",
        account_id="acc-fake-card-002",
        account_type="CREDIT",
        nature="income",
        amount=Decimal("500.00"),
        local_date=date(2026, 3, 7),
        name="Pagamento recebido",
        pluggy_category=None,
    )
    pairs, unpaired = match_own_transfers_and_bill_payments(
        [out, inn], mapped, match_days=3
    )
    assert len(pairs) == 1
    assert pairs[0].kind == "bill_payment"
    assert not unpaired


def test_unpaired_transfer_listed():
    mapped = {"acc-fake-bank-001", "acc-fake-card-002"}
    out = _tx(pluggy_id="lonely", amount=Decimal("77.00"))
    pairs, unpaired = match_own_transfers_and_bill_payments([out], mapped)
    assert pairs == []
    assert "lonely" in unpaired
