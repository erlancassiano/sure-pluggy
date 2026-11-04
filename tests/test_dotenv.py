"""Testes do carregamento automático de .env (stdlib)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from pluggy_sure.config import ConfigError, load_dotenv, load_settings
from pluggy_sure.logging_utils import setup_logging


def test_load_dotenv_fills_missing_env_from_file(tmp_path, monkeypatch):
    for k in (
        "PLUGGY_CLIENT_ID",
        "PLUGGY_CLIENT_SECRET",
        "SURE_API_KEY",
        "SURE_URL",
        "PLUGGY_ITEM_IDS",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(k, raising=False)

    env_file = tmp_path / ".env"
    # Valores fictícios — nunca reais
    env_file.write_text(
        "\n".join(
            [
                "# comentario",
                "PLUGGY_CLIENT_ID=fake-client-from-dotenv",
                "PLUGGY_CLIENT_SECRET=fake-secret-from-dotenv",
                'SURE_API_KEY="fake-sure-key-from-dotenv"',
                "SURE_URL=http://localhost:3001",
                "PLUGGY_ITEM_IDS=item-fake-01",
                "export LOG_LEVEL=DEBUG",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert load_dotenv(env_file) is True
    settings = load_settings(dotenv=env_file)
    assert settings.pluggy_client_id == "fake-client-from-dotenv"
    assert settings.sure_url == "http://localhost:3001"
    assert settings.log_level == "DEBUG"


def test_load_dotenv_does_not_override_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("PLUGGY_CLIENT_ID", "already-set-client")
    monkeypatch.setenv("PLUGGY_CLIENT_SECRET", "already-set-secret")
    monkeypatch.setenv("SURE_API_KEY", "already-set-key")
    monkeypatch.setenv("SURE_URL", "http://already.example:3001")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "PLUGGY_CLIENT_ID=from-file",
                "PLUGGY_CLIENT_SECRET=from-file",
                "SURE_API_KEY=from-file",
                "SURE_URL=http://from-file:3001",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    load_dotenv(env_file, override=False)
    settings = load_settings(dotenv=env_file)
    assert settings.pluggy_client_id == "already-set-client"
    assert settings.sure_url == "http://already.example:3001"


def test_load_dotenv_missing_file_returns_false(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") is False


def test_load_dotenv_does_not_log_secret_values(tmp_path, monkeypatch, caplog):
    for k in (
        "PLUGGY_CLIENT_ID",
        "PLUGGY_CLIENT_SECRET",
        "SURE_API_KEY",
        "SURE_URL",
    ):
        monkeypatch.delenv(k, raising=False)

    secret = "fake-secret-NAO-LOGAR-dotenv"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "PLUGGY_CLIENT_ID=fake-id",
                f"PLUGGY_CLIENT_SECRET={secret}",
                "SURE_API_KEY=fake-key",
                "SURE_URL=http://localhost:3001",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    caplog.set_level(logging.DEBUG)
    setup_logging("DEBUG")
    load_dotenv(env_file)
    load_settings(dotenv=env_file)
    assert secret not in caplog.text


def test_load_settings_still_fails_if_dotenv_incomplete(tmp_path, monkeypatch):
    for k in REQUIRED_CLEAR:
        monkeypatch.delenv(k, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("SURE_URL=http://localhost:3001\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_settings(dotenv=env_file)
    msg = str(exc.value)
    assert "PLUGGY_CLIENT_ID" in msg
    # mensagem lista nomes de vars, não valores
    assert "http://localhost:3001" not in msg


def test_load_dotenv_strips_utf8_bom(tmp_path, monkeypatch):
    for k in REQUIRED_CLEAR:
        monkeypatch.delenv(k, raising=False)
    env_file = tmp_path / ".env"
    # Simula Notepad salvando com BOM
    content = (
        "PLUGGY_CLIENT_ID=fake-client-bom\n"
        "PLUGGY_CLIENT_SECRET=fake-secret-bom\n"
        "SURE_API_KEY=fake-key-bom\n"
        "SURE_URL=http://localhost:3001\n"
    )
    env_file.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
    settings = load_settings(dotenv=env_file)
    assert settings.pluggy_client_id == "fake-client-bom"


def test_placeholder_values_fail_fast(tmp_path, monkeypatch):
    for k in REQUIRED_CLEAR:
        monkeypatch.delenv(k, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "PLUGGY_CLIENT_ID=seu-client-id",
                "PLUGGY_CLIENT_SECRET=seu-client-secret",
                "SURE_API_KEY=sua-chave-api-sure",
                "SURE_URL=http://localhost:3001",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc:
        load_settings(dotenv=env_file)
    msg = str(exc.value)
    assert "placeholder" in msg.casefold()
    assert "PLUGGY_CLIENT_ID" in msg
    assert "seu-client-id" not in msg  # não ecoa o valor


REQUIRED_CLEAR = (
    "PLUGGY_CLIENT_ID",
    "PLUGGY_CLIENT_SECRET",
    "SURE_API_KEY",
    "SURE_URL",
)
