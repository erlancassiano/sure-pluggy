"""Carga de rules.yaml."""

from __future__ import annotations

import pytest

from pluggy_sure.categorize import load_rules
from pluggy_sure.config import ConfigError


def test_load_rules_missing_file_clear_message(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_rules(tmp_path / "rules.yaml")
    assert "não encontrado" in str(exc.value)


def test_load_rules_accepts_utf8_bom(tmp_path):
    path = tmp_path / "rules.yaml"
    body = b"rules:\n  - match: \"IFOOD\"\n    category: Teste\n"
    path.write_bytes(b"\xef\xbb\xbf" + body)
    rules = load_rules(path)
    assert len(rules) == 1
    assert rules[0].category == "Teste"
