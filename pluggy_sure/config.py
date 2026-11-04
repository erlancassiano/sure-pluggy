"""Carrega e valida configuração a partir de variáveis de ambiente."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


REQUIRED_ENV = (
    "PLUGGY_CLIENT_ID",
    "PLUGGY_CLIENT_SECRET",
    "SURE_API_KEY",
    "SURE_URL",
)

# Placeholders do .env.example — se ainda estiverem assim, falha com mensagem clara
_PLACEHOLDER_VALUES = {
    "PLUGGY_CLIENT_ID": {"seu-client-id"},
    "PLUGGY_CLIENT_SECRET": {"seu-client-secret"},
    "SURE_API_KEY": {"sua-chave-api-sure"},
}


class ConfigError(Exception):
    """Falha de configuração (CLI traduz para exit code 2)."""

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
    # Conta Azul: crédito pode chegar menor (tarifa). 0 desliga.
    transfer_tariff_max: Decimal = Decimal("25.00")


def load_dotenv(
    path: Path | None = None,
    *,
    override: bool = False,
) -> bool:
    """Carrega pares KEY=VALUE de um arquivo .env para os.environ (stdlib).

    - Não loga nem retorna valores.
    - Por padrão NÃO sobrescreve variáveis já definidas no ambiente.
    - Ignora linhas vazias e comentários (#).
    - Aspas simples/duplas ao redor do valor são removidas.
    Retorna True se o arquivo existia e foi lido.
    """
    env_path = path if path is not None else Path(".env")
    if not env_path.is_file():
        return False
    # utf-8-sig remove BOM do Notepad/Windows que quebraria o nome da 1ª chave
    text = env_path.read_text(encoding="utf-8-sig")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not override and key in os.environ:
            continue
        os.environ[key] = value
    return True


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"Variável de ambiente obrigatória ausente: {name}. "
            "Defina-a no ambiente ou no arquivo .env (não versionado)."
        )
    return value


def load_settings(*, dotenv: bool | Path = True) -> Settings:
    """Valida variáveis obrigatórias e monta Settings. Sem logar segredos.

    Carrega `.env` automaticamente (se existir) antes da validação.
    Variáveis já presentes no ambiente têm precedência.

    Args:
        dotenv: True = carrega Path(".env"); Path = arquivo específico;
            False = não lê arquivo (útil em testes).
    """
    if dotenv is True:
        load_dotenv()
    elif isinstance(dotenv, Path):
        load_dotenv(dotenv)

    missing = [n for n in REQUIRED_ENV if not os.environ.get(n, "").strip()]
    if missing:
        raise ConfigError(
            "Variáveis de ambiente obrigatórias ausentes: "
            + ", ".join(missing)
            + ". Defina-as antes de executar (veja .env.example)."
        )

    placeholders = [
        n
        for n, bad in _PLACEHOLDER_VALUES.items()
        if os.environ.get(n, "").strip() in bad
    ]
    if placeholders:
        raise ConfigError(
            "Variáveis ainda com placeholder do .env.example: "
            + ", ".join(placeholders)
            + ". Preencha com valores reais no arquivo .env."
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
        transfer_tariff_max=_parse_decimal_env(
            "SYNC_TRANSFER_TARIFF_MAX_BRL", Decimal("25.00")
        ),
    )


def _parse_decimal_env(name: str, default: Decimal) -> Decimal:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigError(
            f"{name} inválido (use número decimal, ex.: 25.00)."
        ) from exc
    if value < 0:
        raise ConfigError(f"{name} deve ser ≥ 0.")
    return value


def load_accounts_map(path: Path) -> dict[str, str]:
    """Carrega pluggy_account_id → sure_account_id de accounts_map.json.

    Usa utf-8-sig para tolerar BOM do PowerShell 5.1 (`Set-Content -Encoding utf8`).
    """
    import json

    if not path.exists():
        raise ConfigError(
            f"Arquivo de mapeamento de contas não encontrado: {path}. "
            "Copie accounts_map.example.json para accounts_map.json e preencha."
        )
    try:
        with path.open(encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"accounts_map.json inválido ({path}): {exc.msg} "
            f"(linha {exc.lineno}, coluna {exc.colno}). "
            "Confira vírgulas/aspas; se gravou no PowerShell 5.1, prefira "
            "[IO.File]::WriteAllText com UTF8Encoding($false) para evitar BOM."
        ) from None
    if not isinstance(data, dict):
        raise ConfigError(
            f"accounts_map.json deve ser um objeto JSON {{pluggy_id: sure_id}}, "
            f"recebido: {type(data).__name__}."
        )
    # Ignora chaves de metadados começando com _
    return {k: str(v) for k, v in data.items() if not str(k).startswith("_")}
