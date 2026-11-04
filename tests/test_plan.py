"""Testes de plan / idempotência (seção 10)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from pluggy_sure.normalize import normalize_transaction, payload_hash
from pluggy_sure.plan import TAG_PENDING_REVIEW, TAG_REMOVED, build_plan
from pluggy_sure.categorize import categorize


def _norm(raw, atype):
    return normalize_transaction(raw, atype)


def test_new_transaction_planned_as_create(
    fixtures, accounts_map, account_types, state, rules
):
    raw = next(t for t in fixtures["pluggy_transactions"] if t["id"] == "tx-fake-0002")
    n = _norm(raw, account_types[raw["accountId"]])
    plan = build_plan([n], accounts_map, account_types, state, rules)
    assert plan.actions[0].action == "create"


def test_same_transaction_twice_is_noop(
    fixtures, accounts_map, account_types, state, rules
):
    raw = next(t for t in fixtures["pluggy_transactions"] if t["id"] == "tx-fake-0002")
    n = _norm(raw, account_types[raw["accountId"]])
    cat = categorize(n, rules)
    state.upsert(n.pluggy_id, "sure-tx-fake-1", payload_hash(n, cat.category), n.status)
    plan = build_plan([n], accounts_map, account_types, state, rules)
    assert plan.actions[0].action == "noop"
    assert plan.actions[0].sure_id == "sure-tx-fake-1"


def test_pending_to_posted_with_amount_change_is_update(
    fixtures, accounts_map, account_types, state, rules
):
    raw = next(t for t in fixtures["pluggy_transactions"] if t["id"] == "tx-fake-0003")
    n_before = _norm(raw, "CREDIT")
    cat = categorize(n_before, rules)
    state.upsert(
        n_before.pluggy_id,
        "sure-tx-fake-3",
        payload_hash(n_before, cat.category),
        "PENDING",
    )
    raw_after = dict(raw)
    raw_after["amount"] = 82.50
    raw_after["status"] = "POSTED"
    n_after = _norm(raw_after, "CREDIT")
    plan = build_plan([n_after], accounts_map, account_types, state, rules)
    assert plan.actions[0].action == "update"
    assert plan.actions[0].sure_id == "sure-tx-fake-3"
    assert "pendente" not in plan.actions[0].tx.tags


def test_late_card_transaction_inside_lookback_is_created(
    accounts_map, account_types, state, rules
):
    # Lançamento que "chega" 10 dias depois — ainda na janela de 15 dias
    today = date(2026, 3, 20)
    tx_date = today - timedelta(days=10)
    raw = {
        "id": "tx-late-card",
        "accountId": "acc-fake-card-002",
        "date": f"{tx_date.isoformat()}T12:00:00Z",
        "description": "LOJA TARDIA EXEMPLO",
        "amount": 40.00,
        "currencyCode": "BRL",
        "status": "POSTED",
    }
    n = _norm(raw, "CREDIT")
    plan = build_plan([n], accounts_map, account_types, state, rules)
    assert plan.actions[0].action == "create"


def test_missing_from_source_is_tagged_not_deleted(
    fixtures, accounts_map, account_types, state, rules
):
    raw = next(t for t in fixtures["pluggy_transactions"] if t["id"] == "tx-fake-0002")
    n = _norm(raw, "CREDIT")
    cat = categorize(n, rules)
    state.upsert(n.pluggy_id, "sure-tx-fake-1", payload_hash(n, cat.category), n.status)
    # Plano sem a transação, mas com previously_seen
    plan = build_plan(
        [],
        accounts_map,
        account_types,
        state,
        rules,
        previously_seen_ids_in_window={n.pluggy_id},
    )
    missing = [a for a in plan.actions if a.action == "tag_missing"]
    assert len(missing) == 1
    assert missing[0].sure_id == "sure-tx-fake-1"
    assert TAG_REMOVED in missing[0].tags_extra


def test_user_edited_category_not_overwritten(
    fixtures, accounts_map, account_types, state, rules
):
    raw = next(t for t in fixtures["pluggy_transactions"] if t["id"] == "tx-fake-0002")
    n = _norm(raw, "CREDIT")
    cat = categorize(n, rules)
    # Simula mudança de descrição para forçar update
    state.upsert(n.pluggy_id, "sure-tx-fake-1", "hash-antigo", n.status)
    plan = build_plan(
        [n],
        accounts_map,
        account_types,
        state,
        rules,
        sure_user_modified_ids={n.pluggy_id},
    )
    assert plan.actions[0].action == "update"
    assert plan.actions[0].skip_category_update is True


def test_unmapped_account_skipped_with_warning(
    fixtures, accounts_map, account_types, state, rules
):
    raw = next(t for t in fixtures["pluggy_transactions"] if t["id"] == "tx-fake-0014")
    n = _norm(raw, "BANK")
    plan = build_plan([n], accounts_map, account_types, state, rules)
    assert plan.actions[0].action == "skip"
    assert any("não mapeada" in w for w in plan.warnings)


def test_create_returning_200_records_mapping(state, tmp_path):
    """Dado POST 200 (já existia), estado grava sure_id — exercitado via upsert."""
    state.upsert("tx-fake-0002", "sure-tx-fake-1", "abc", "POSTED")
    st = state.get("tx-fake-0002")
    assert st is not None
    assert st.sure_id == "sure-tx-fake-1"


def test_amount_change_marks_pending_review(
    fixtures, accounts_map, account_types, state, rules
):
    """Lançamento já vinculado que mudou de valor → pendente-revisao."""
    raw = next(t for t in fixtures["pluggy_transactions"] if t["id"] == "tx-fake-0002")
    n = _norm(raw, "CREDIT")
    cat = categorize(n, rules)
    state.upsert(n.pluggy_id, "sure-tx-fake-1", payload_hash(n, cat.category), n.status)
    raw2 = dict(raw)
    raw2["amount"] = 99.99
    n2 = _norm(raw2, "CREDIT")
    plan = build_plan([n2], accounts_map, account_types, state, rules)
    assert plan.actions[0].action == "update"
    assert plan.actions[0].pending_review is True
    assert TAG_PENDING_REVIEW in plan.actions[0].tags_extra


def test_unpaired_transfer_marks_pending_review(
    accounts_map, account_types, state, rules
):
    """Ponta de transferência sem par → pendente-revisao, fora do envelope."""
    raw = {
        "id": "tx-unpaired-transfer",
        "accountId": "acc-fake-bank-001",
        "date": "2026-03-06T14:00:00Z",
        "description": "Transferência mesma titularidade",
        "amount": -300.00,
        "currencyCode": "BRL",
        "category": "Same person transfer",
        "status": "POSTED",
    }
    n = _norm(raw, "BANK")
    plan = build_plan([n], accounts_map, account_types, state, rules)
    assert n.pluggy_id in plan.unpaired_transfer_ids
    assert plan.actions[0].pending_review is True
    assert TAG_PENDING_REVIEW in plan.actions[0].tags_extra
    assert plan.actions[0].category.fora_envelope is True


def test_card_bill_payment_never_counts_as_envelope_expense(
    fixtures, accounts_map, account_types, state, rules
):
    """Competência: pagamento de fatura é caixa/transferência, nunca gasto de envelope."""
    raw = next(t for t in fixtures["pluggy_transactions"] if t["id"] == "tx-fake-0007")
    n = _norm(raw, "BANK")
    plan = build_plan([n], accounts_map, account_types, state, rules)
    action = plan.actions[0]
    assert action.category.fora_envelope is True
    assert action.tx.local_date  # data da saída na conta (caixa)
