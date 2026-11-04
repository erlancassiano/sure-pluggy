"""Logging seguro: sem valores, descrições, nomes de conta ou headers de auth."""

from __future__ import annotations

import logging
import re
from typing import Any

# Headers sensíveis a mascarar em mensagens de erro HTTP
_SENSITIVE_HEADER_RE = re.compile(
    r"(?i)(authorization|x-api-key)\s*[:=]\s*\S+"
)
_SECRET_LIKE = re.compile(
    r"(?i)(fake-secret|fake-sure-key|client_secret|api[_-]?key)\S*"
)


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        msg = _SENSITIVE_HEADER_RE.sub(r"\1=***", msg)
        return msg


def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("pluggy_sure")
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(
        SafeFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    return logger


def truncate_id(value: str, keep: int = 8) -> str:
    if len(value) <= keep:
        return value
    return value[:keep] + "…"


def redact_http_error(exc: BaseException) -> str:
    """Mensagem de erro HTTP sem headers de autorização."""
    text = str(exc)
    text = _SENSITIVE_HEADER_RE.sub(r"\1=***", text)
    return text


def assert_no_secrets(text: str, secrets: list[str]) -> None:
    """Utilitário de teste: falha se algum segredo aparecer no texto."""
    for secret in secrets:
        if secret and secret in text:
            raise AssertionError("segredo encontrado em saída")


def safe_extra(**kwargs: Any) -> dict[str, Any]:
    """Filtra kwargs para logs — nunca passa amount/description/name."""
    blocked = {
        "amount",
        "description",
        "name",
        "authorization",
        "api_key",
        "secret",
        "client_secret",
    }
    return {k: v for k, v in kwargs.items() if k.lower() not in blocked}
