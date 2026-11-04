"""Testes de carga de accounts_map.json (BOM / JSON inválido)."""

from __future__ import annotations

import json

import pytest

from pluggy_sure.config import ConfigError, load_accounts_map


def test_load_accounts_map_accepts_utf8_bom(tmp_path):
    path = tmp_path / "accounts_map.json"
    payload = {
        "aaaaaaaa-bbbb-cccc-dddd-111111111111": "eeeeeeee-ffff-0000-1111-222222222222"
    }
    raw = json.dumps(payload).encode("utf-8")
    path.write_bytes(b"\xef\xbb\xbf" + raw)
    loaded = load_accounts_map(path)
    assert loaded == payload


def test_load_accounts_map_invalid_json_clear_message(tmp_path):
    path = tmp_path / "accounts_map.json"
    path.write_text("{nao-json", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_accounts_map(path)
    msg = str(exc.value)
    assert "accounts_map.json inválido" in msg
    assert "linha" in msg


def test_load_accounts_map_missing_file(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_accounts_map(tmp_path / "nope.json")
    assert "não encontrado" in str(exc.value)


def test_load_accounts_map_ignores_underscore_keys(tmp_path):
    path = tmp_path / "accounts_map.json"
    path.write_text(
        json.dumps(
            {
                "_aviso": "ignorar",
                "aaaaaaaa-bbbb-cccc-dddd-111111111111": "eeeeeeee-ffff-0000-1111-222222222222",
            }
        ),
        encoding="utf-8",
    )
    loaded = load_accounts_map(path)
    assert "_aviso" not in loaded
    assert len(loaded) == 1
