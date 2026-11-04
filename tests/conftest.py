"""Fixtures compartilhadas (dados 100% fictícios)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pluggy_sure.categorize import load_rules
from pluggy_sure.normalize import normalize_transaction
from pluggy_sure.state import StateStore

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "fixtures-exemplo.json"
RULES_EXAMPLE = Path(__file__).resolve().parents[1] / "rules.example.yaml"


@pytest.fixture
def fixtures() -> dict:
    with FIXTURES_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def accounts_map(fixtures) -> dict[str, str]:
    return dict(fixtures["accounts_map"])


@pytest.fixture
def account_types(fixtures) -> dict[str, str]:
    return {a["id"]: a["type"] for a in fixtures["pluggy_accounts"]}


@pytest.fixture
def rules():
    return load_rules(RULES_EXAMPLE)


@pytest.fixture
def env_falso(fixtures, monkeypatch):
    for k, v in fixtures["env_falso"].items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PLUGGY_ITEM_IDS", "item-fake-01")
    return fixtures["env_falso"]


@pytest.fixture
def state(tmp_path) -> StateStore:
    store = StateStore(tmp_path / "state.sqlite")
    yield store
    store.close()


@pytest.fixture
def normalized_all(fixtures, account_types):
    out = []
    for raw in fixtures["pluggy_transactions"]:
        at = account_types[raw["accountId"]]
        out.append(normalize_transaction(raw, at))
    return out


@pytest.fixture
def rules_path(tmp_path):
    dest = tmp_path / "rules.yaml"
    dest.write_text(RULES_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return dest
