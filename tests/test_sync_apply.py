"""Testes de aplicação do plano (create 200, tags, etc.)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pluggy_sure.categorize import categorize, load_rules
from pluggy_sure.config import Settings
from pluggy_sure.normalize import normalize_transaction, payload_hash
from pluggy_sure.plan import PlannedAction
from pluggy_sure.sync import SyncRunner


RULES = Path(__file__).resolve().parents[1] / "rules.example.yaml"


@pytest.fixture
def runner(accounts_map, state, tmp_path):
    settings = Settings(
        pluggy_client_id="fake-client-id",
        pluggy_client_secret="fake-secret",
        sure_api_key="fake-key",
        sure_url="http://sure.test",
        pluggy_item_ids=["item-fake-01"],
        lookback_days=15,
        state_path=tmp_path / "state.sqlite",
        rules_path=RULES,
        accounts_map_path=tmp_path / "accounts_map.json",
        log_level="INFO",
        log_amounts=False,
    )
    pluggy = MagicMock()
    sure = MagicMock()
    sure.ensure_category.return_value = "cat-fora"
    sure.resolve_tag_ids.side_effect = lambda names: [f"tag-{n}" for n in names]
    rules = load_rules(RULES)
    return SyncRunner(settings, pluggy, sure, state, rules, accounts_map)


def test_unmapped_pluggy_account_warns_without_fetching_txs(runner, caplog):
    """Conta Pluggy fora do mapa: aviso + sem list_transactions da conta."""
    runner.pluggy.authenticate = MagicMock()
    runner.pluggy.get_item = MagicMock(return_value={"id": "item-fake-01"})
    runner.pluggy.warn_if_stale = MagicMock()
    runner.pluggy.list_accounts = MagicMock(
        return_value=[
            {
                "id": "acc-fake-unmapped-003",
                "type": "BANK",
                "balance": 10.0,
                "name": "NaoDeveAparecerNoAviso",
            },
            {
                "id": "acc-fake-bank-001",
                "type": "BANK",
                "balance": 100.0,
            },
        ]
    )
    runner.pluggy.list_transactions = MagicMock(return_value=[])
    runner.sure.list_accounts = MagicMock(return_value=[])

    from datetime import date

    report = runner.run(since=date(2026, 1, 1), dry_run=True, now=date(2026, 3, 15))
    assert any("não mapeada" in w for w in report.warnings)
    assert any("acc-fake" in w for w in report.warnings)
    # Sem nome de conta no aviso
    assert all("NaoDeveAparecerNoAviso" not in w for w in report.warnings)
    called_ids = [
        c.args[0] for c in runner.pluggy.list_transactions.call_args_list
    ]
    assert "acc-fake-unmapped-003" not in called_ids
    assert "acc-fake-bank-001" in called_ids


def test_create_returning_200_records_mapping(runner, fixtures, account_types, state):
    raw = next(t for t in fixtures["pluggy_transactions"] if t["id"] == "tx-fake-0002")
    n = normalize_transaction(raw, "CREDIT")
    cat = categorize(n, runner.rules)
    runner.sure.create_transaction.return_value = (
        200,
        {"id": "sure-tx-fake-1"},
    )
    action = PlannedAction(action="create", tx=n, category=cat)
    runner._apply(action, "cat-fora")
    st = state.get("tx-fake-0002")
    assert st is not None
    assert st.sure_id == "sure-tx-fake-1"
    # Não deve chamar create de novo numa segunda aplicação com estado
    runner.sure.create_transaction.reset_mock()
    from pluggy_sure.plan import build_plan

    plan = build_plan([n], runner.accounts_map, account_types, state, runner.rules)
    assert plan.actions[0].action == "noop"
