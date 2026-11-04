"""Teste de integração opcional contra Sure local.

NÃO entra na suíte padrão (marcador `integration` excluído em pyproject.toml).

Como rodar:
  SURE_URL=http://localhost:3001 SURE_API_KEY=... \\
    pytest -m integration tests/integration/test_sure_idempotency.py

Pré-requisito: a imagem Docker do Sure precisa expor external_id/source no POST
(ver seção 2 da spec — recurso recente; confirmar na instância real).
"""

from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
import requests

from pluggy_sure.config import load_dotenv

pytestmark = pytest.mark.integration


@pytest.fixture
def sure_env():
    # Carrega .env local se existir (não sobrescreve env já definido)
    load_dotenv()
    url = os.environ.get("SURE_URL", "").rstrip("/")
    key = os.environ.get("SURE_API_KEY", "")
    account_id = os.environ.get("SURE_TEST_ACCOUNT_ID", "")
    if not url or not key or not account_id:
        pytest.fail(
            "Defina SURE_URL, SURE_API_KEY e SURE_TEST_ACCOUNT_ID "
            "(env ou .env) para o teste de integração"
        )
    return url, key, account_id


def test_sure_idempotency_supported(sure_env):
    """POST duas vezes com o mesmo external_id → 201 e depois 200."""
    base, key, account_id = sure_env
    headers = {"Content-Type": "application/json", "X-Api-Key": key}
    external_id = f"pluggy-itest-{uuid.uuid4()}"
    payload = {
        "transaction": {
            "account_id": account_id,
            "date": date.today().isoformat(),
            "amount": 1.23,
            "name": "teste-idempotencia-pluggy-sure",
            "nature": "expense",
            "currency": "BRL",
            "external_id": external_id,
            "source": "pluggy",
        }
    }
    r1 = requests.post(
        f"{base}/api/v1/transactions", json=payload, headers=headers, timeout=30
    )
    r2 = requests.post(
        f"{base}/api/v1/transactions", json=payload, headers=headers, timeout=30
    )
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["id"] == r2.json()["id"]
