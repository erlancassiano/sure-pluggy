"""Carrega e valida configuração a partir de variáveis de ambiente."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


REQUIRED_ENV = (
    "PLUGGY_CLIENT_ID",
    "PLUGGY_CLIENT_SECRET",
    "SURE_API_KEY",
    "SURE_URL",
)


class ConfigError(SystemExit):
    """Falha rápida de configuração (exit code 2 por padrão via SystemExit)."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Settings:
    pluggy_client_id: str
    pluggy_client_secret: str
    sure_api_key: str
    sure_url: str
    pluggy_item_ids: list[str]
    lookback_days: int
    state_path: Path
    rules_path: Path
    accounts_map_path: Path
    log_level: str
    log_amounts: bool
    transfer_match_days: int = 3  # TODO(spec): confirmar tolerância definitiva


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"Variável de ambiente obrigatória ausente: {name}. "
            "Defina-a no ambiente ou no arquivo .env (não versionado)."
        )
    return value


def load_settings() -> Settings:
    """Valida variáveis obrigatórias e monta Settings. Sem logar segredos."""
    missing = [n for n in REQUIRED_ENV if not os.environ.get(n, "").strip()]
    if missing:
        raise ConfigError(
            "Variáveis de ambiente obrigatórias ausentes: "
            + ", ".join(missing)
            + ". Defina-as antes de executar (veja .env.example)."
        )

    item_ids_raw = os.environ.get("PLUGGY_ITEM_IDS", "").strip()
    item_ids = [i.strip() for i in item_ids_raw.split(",") if i.strip()]

    return Settings(
        pluggy_client_id=_require("PLUGGY_CLIENT_ID"),
        pluggy_client_secret=_require("PLUGGY_CLIENT_SECRET"),
        sure_api_key=_require("SURE_API_KEY"),
        sure_url=_require("SURE_URL").rstrip("/"),
        pluggy_item_ids=item_ids,
        lookback_days=int(os.environ.get("SYNC_LOOKBACK_DAYS", "15")),
        state_path=Path(os.environ.get("SYNC_STATE_PATH", "./state.sqlite")),
        rules_path=Path(os.environ.get("SYNC_RULES_PATH", "./rules.yaml")),
        accounts_map_path=Path(
            os.environ.get("SYNC_ACCOUNTS_MAP_PATH", "./accounts_map.json")
        ),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        log_amounts=os.environ.get("LOG_AMOUNTS", "false").lower()
        in {"1", "true", "yes"},
        transfer_match_days=int(os.environ.get("SYNC_TRANSFER_MATCH_DAYS", "3")),
    )


def load_accounts_map(path: Path) -> dict[str, str]:
    """Carrega pluggy_account_id → sure_account_id de accounts_map.json."""
    import json

    if not path.exists():
        raise ConfigError(
            f"Arquivo de mapeamento de contas não encontrado: {path}. "
            "Copie accounts_map.example.json para accounts_map.json e preencha."
        )
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    # Ignora chaves de metadados começando com _
    return {k: str(v) for k, v in data.items() if not str(k).startswith("_")}
