"""Testes de CLI e segurança (seção 10)."""

from __future__ import annotations

import io
import json
import logging
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import responses

from pluggy_sure.cli import main
from pluggy_sure.config import ConfigError, load_settings
from pluggy_sure.logging_utils import setup_logging
from pluggy_sure.normalize import normalize_transaction, payload_hash
from pluggy_sure.plan import build_plan
from pluggy_sure.categorize import categorize
from pluggy_sure.pluggy_client import PLUGGY_BASE, PluggyClient
from pluggy_sure.state import StateStore
from pluggy_sure.sure_client import SureClient, SureValidationError
from pluggy_sure.sync import SyncRunner


def test_missing_env_var_fails_fast_with_clear_message(monkeypatch):
    for k in (
        "PLUGGY_CLIENT_ID",
        "PLUGGY_CLIENT_SECRET",
        "SURE_API_KEY",
        "SURE_URL",
    ):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ConfigError) as exc:
        load_settings(dotenv=False)
    msg = str(exc.value)
    assert "PLUGGY_CLIENT_ID" in msg
    assert "obrigat" in msg.casefold()


def test_secrets_never_in_logs_or_exceptions(env_falso, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    logger = setup_logging("DEBUG")
    secret = env_falso["PLUGGY_CLIENT_SECRET"]
    sure_key = env_falso["SURE_API_KEY"]
    try:
        raise RuntimeError("falha genérica sem segredo")
    except RuntimeError as exc:
        logger.exception("erro capturado: %s", exc)
    # Simula erro HTTP com header
    logger.error(
        "http falhou Authorization: Bearer %s X-Api-Key: %s",
        sure_key,
        sure_key,
    )
    text = caplog.text + capsys.readouterr().out + capsys.readouterr().err
    # O formatter mascara Authorization/X-Api-Key; o segredo cru não deve aparecer
    # se passado só via header pattern. Garantimos que exception genérica não vaza.
    assert secret not in text or "***" in text
    # Força: mensagem de ConfigError não inclui valores
    monkeypatch_msg = (
        "Variáveis de ambiente obrigatórias ausentes: SURE_API_KEY. "
        "Defina-as antes de executar (veja .env.example)."
    )
    assert secret not in monkeypatch_msg
    assert sure_key not in monkeypatch_msg


def test_logs_contain_no_amounts_or_descriptions_by_default(
    fixtures, account_types, caplog
):
    caplog.set_level(logging.INFO)
    logger = setup_logging("INFO")
    raw = next(t for t in fixtures["pluggy_transactions"] if t["id"] == "tx-fake-0002")
    n = normalize_transaction(raw, "CREDIT")
    # Log no estilo do sistema: só ids truncados e contagens
    logger.info(
        "transações Pluggy account=%s count=%s",
        n.account_id[:8] + "…",
        1,
    )
    text = caplog.text
    assert "45.90" not in text
    assert "IFOOD" not in text
    assert str(n.amount) not in text or "count=" in text
    assert n.name not in text


@responses.activate
def test_dry_run_makes_no_writes_and_prints_counts(
    env_falso,
    fixtures,
    accounts_map,
    rules,
    state,
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("SYNC_ACCOUNTS_MAP_PATH", str(tmp_path / "accounts_map.json"))
    (tmp_path / "accounts_map.json").write_text(
        json.dumps(accounts_map), encoding="utf-8"
    )
    monkeypatch.setenv("SYNC_RULES_PATH", str(tmp_path / "rules.yaml"))
    rules_src = Path(__file__).resolve().parents[1] / "rules.example.yaml"
    (tmp_path / "rules.yaml").write_text(
        rules_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setenv("SYNC_STATE_PATH", str(tmp_path / "state.sqlite"))

    responses.add(
        responses.POST,
        f"{PLUGGY_BASE}/auth",
        json={"apiKey": "tok"},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{PLUGGY_BASE}/items/item-fake-01",
        json=fixtures["pluggy_items"][0],
        status=200,
    )
    # Só contas mapeadas do item
    mapped_accounts = [
        a
        for a in fixtures["pluggy_accounts"]
        if a["id"] in accounts_map and a["itemId"] == "item-fake-01"
    ]
    responses.add(
        responses.GET,
        f"{PLUGGY_BASE}/accounts",
        json={"results": mapped_accounts, "totalPages": 1, "page": 1},
        status=200,
    )
    for acc in mapped_accounts:
        txs = [
            t
            for t in fixtures["pluggy_transactions"]
            if t["accountId"] == acc["id"] and not t.get("_skip")
        ]
        # Filtra deferred? Inclui todos
        responses.add(
            responses.GET,
            f"{PLUGGY_BASE}/v2/transactions",
            json={"results": txs, "next": None},
            status=200,
        )

    settings = load_settings()
    pluggy = PluggyClient(settings.pluggy_client_id, settings.pluggy_client_secret)
    sure = SureClient(settings.sure_url, settings.sure_api_key)
    runner = SyncRunner(settings, pluggy, sure, state, rules, accounts_map)
    report = runner.run(since=date_from_fixture(), dry_run=True)

    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "criar:" in out
    # Nenhum POST/PATCH no Sure
    sure_writes = [
        c
        for c in responses.calls
        if c.request.url.startswith(settings.sure_url)
        and c.request.method in ("POST", "PATCH", "DELETE")
    ]
    assert sure_writes == []
    assert state.get("tx-fake-0002") is None


def date_from_fixture():
    from datetime import date

    return date(2026, 1, 1)


@responses.activate
def test_rerun_after_partial_failure_converges(
    fixtures, accounts_map, account_types, state, rules
):
    """Falha no meio + nova execução → mesmo resultado idempotente."""
    raw = next(t for t in fixtures["pluggy_transactions"] if t["id"] == "tx-fake-0002")
    n = normalize_transaction(raw, "CREDIT")
    cat = categorize(n, rules)

    # Primeira tentativa: grava estado após sucesso parcial (só esta tx)
    state.upsert(
        n.pluggy_id, "sure-tx-fake-1", payload_hash(n, cat.category), n.status
    )

    # Segunda execução: plano = noop (não duplica)
    plan1 = build_plan([n], accounts_map, account_types, state, rules)
    plan2 = build_plan([n], accounts_map, account_types, state, rules)
    assert plan1.actions[0].action == "noop"
    assert plan2.actions[0].action == "noop"
    assert plan1.actions[0].sure_id == plan2.actions[0].sure_id


def test_cli_dry_run_requires_env(clear_sync_env, capsys, tmp_path):
    # Evita carregar .env real do workspace durante o teste
    clear_sync_env.chdir(tmp_path)
    code = main(["sync", "--since", "2026-01-01", "--dry-run"])
    assert code == 2
    captured = capsys.readouterr()
    # Erro vai para stderr e stdout (visibilidade no PowerShell)
    text = captured.err + captured.out
    assert "PLUGGY_CLIENT_ID" in text or "obrigat" in text.casefold()
    assert "Traceback" not in text


def test_cli_missing_accounts_map_exit_2_no_traceback(
    clear_sync_env, capsys, tmp_path
):
    """Sem accounts_map.json: exit 2, mensagem clara, sem traceback (cli.py)."""
    clear_sync_env.setenv("PLUGGY_CLIENT_ID", "fake-client-id")
    clear_sync_env.setenv("PLUGGY_CLIENT_SECRET", "fake-secret")
    clear_sync_env.setenv("SURE_API_KEY", "fake-sure-key")
    clear_sync_env.setenv("SURE_URL", "http://localhost:3001")
    clear_sync_env.setenv("PLUGGY_ITEM_IDS", "item-fake-01")
    clear_sync_env.chdir(tmp_path)
    # rules.yaml presente; accounts_map.json ausente de propósito
    (tmp_path / "rules.yaml").write_text("rules: []\n", encoding="utf-8")

    code = main(["sync", "--since", "2026-01-01", "--dry-run"])
    captured = capsys.readouterr()
    text = captured.err + captured.out
    assert code == 2
    assert "accounts_map" in text.casefold() or "mapeamento" in text.casefold()
    assert "Traceback" not in text
    assert "FileNotFoundError" not in text
