"""Motor de regras ordenado: primeira regra que casa vence."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pluggy_sure.normalize import NormalizedTx

# Fallback por categoria Pluggy (seção 6.1 item 7 da spec)
PLUGGY_CATEGORY_FALLBACK: dict[str, str] = {
    "Groceries": "Alimentação casa",
    "Eating out": "Restaurantes e delivery",
    "Food delivery": "Restaurantes e delivery",
    "Pharmacy": "Saúde",
    "Gas stations": "Transporte",
    "Taxi and ride-hailing": "Transporte",
    "Public transportation": "Transporte",
    "Pet supplies and vet": "Pet",
    "Leisure": "Lazer",
    "Digital services": "Assinaturas",
    "Online shopping": "Compras",
    "Shopping": "Compras",
    "Electronics": "Compras",
    "Clothing": "Compras",
    "Gambling": "Loterias",
    "Lottery": "Loterias",
}

ENVELOPE_CATEGORIES = (
    "Alimentação casa",
    "Restaurantes e delivery",
    "Compras",
    "Transporte",
    "Carro",
    "Saúde",
    "Pet",
    "Lazer",
    "Loterias",
    "Pix a pessoas",
    "Outros",
)

OUTSIDE_ENVELOPE = (
    "Parcelas/Dívidas",
    "Assinaturas",
    "Transferências entre contas próprias",
    "Investimentos",
    "Pagamento de fatura",
)

PARENT_FORA = "Fora do orçamento"


@dataclass(frozen=True)
class CategoryResult:
    category: str
    subcategory: str | None = None
    parent: str | None = None
    fora_envelope: bool = False


@dataclass
class Rule:
    match: re.Pattern[str] | None = None
    account_type: str | None = None
    pluggy_category: str | None = None
    sign: str | None = None  # in | out
    category: str = "Outros"
    subcategory: str | None = None
    parent: str | None = None
    fora_envelope: bool = False


def fold(text: str) -> str:
    """Remove acentos e lower-case para comparação de nomes de categoria."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).casefold()


def _sign_of(tx: NormalizedTx) -> str:
    return "out" if tx.nature == "expense" else "in"


def _load_rules_from_dict(data: dict[str, Any]) -> list[Rule]:
    rules: list[Rule] = []
    for item in data.get("rules") or []:
        pattern = None
        if item.get("match"):
            pattern = re.compile(str(item["match"]), re.IGNORECASE)
        rules.append(
            Rule(
                match=pattern,
                account_type=(item.get("account_type") or None),
                pluggy_category=(item.get("pluggy_category") or None),
                sign=(item.get("sign") or None),
                category=str(item.get("category") or "Outros"),
                subcategory=item.get("subcategory"),
                parent=item.get("parent"),
                fora_envelope=bool(item.get("fora_envelope", False)),
            )
        )
    return rules


def load_rules(path: Path) -> list[Rule]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return _load_rules_from_dict(data)


def _rule_matches(rule: Rule, tx: NormalizedTx) -> bool:
    if rule.account_type and rule.account_type.upper() != tx.account_type.upper():
        return False
    if rule.sign and rule.sign != _sign_of(tx):
        return False
    if rule.pluggy_category:
        if not tx.pluggy_category or fold(tx.pluggy_category) != fold(
            rule.pluggy_category
        ):
            return False
    if rule.match is not None:
        return bool(rule.match.search(tx.name))
    # Só pluggy_category (já validado acima)
    return rule.pluggy_category is not None


def _is_international_iof(tx: NormalizedTx) -> bool:
    return bool(re.search(r"IOF de compra internacional", tx.name, re.IGNORECASE))


def _looks_digital_service(name: str) -> bool:
    return bool(
        re.search(
            r"(SERVICO DIGITAL|Cursor|Apple|OpenAI|Google|Netflix|Spotify|Microsoft)",
            name,
            re.IGNORECASE,
        )
    )


def categorize(tx: NormalizedTx, rules: list[Rule]) -> CategoryResult:
    """Aplica regras ordenadas; trata IOF internacional antes do match genérico de IOF."""

    # IOF de compra internacional segue a categoria da compra (spec 6.1 item 9)
    if _is_international_iof(tx):
        if _looks_digital_service(tx.name):
            return CategoryResult(
                category="Assinaturas", fora_envelope=True, parent=PARENT_FORA
            )
        return CategoryResult(category="Compras")

    for rule in rules:
        # Regra genérica de IOF não deve pegar IOF internacional (já tratado)
        if rule.match and "IOF" in rule.match.pattern and _is_international_iof(tx):
            continue
        if _rule_matches(rule, tx):
            fora = rule.fora_envelope or rule.category in OUTSIDE_ENVELOPE
            parent = rule.parent
            if fora and parent is None and rule.category not in ENVELOPE_CATEGORIES:
                if rule.category in (
                    "Transferências entre contas próprias",
                    "Investimentos",
                    "Parcelas/Dívidas",
                    "Assinaturas",
                    "Pagamento de fatura",
                ):
                    parent = PARENT_FORA
            return CategoryResult(
                category=rule.category,
                subcategory=rule.subcategory,
                parent=parent,
                fora_envelope=fora,
            )

    # Fallback por categoria Pluggy
    if tx.pluggy_category:
        mapped = PLUGGY_CATEGORY_FALLBACK.get(tx.pluggy_category)
        if mapped:
            fora = mapped in OUTSIDE_ENVELOPE
            return CategoryResult(
                category=mapped,
                fora_envelope=fora,
                parent=PARENT_FORA if fora else None,
            )

    # Pix/transferência enviada sem regra → Pix a pessoas
    if tx.nature == "expense" and tx.pluggy_category and "PIX" in tx.pluggy_category.upper():
        return CategoryResult(category="Pix a pessoas")
    if tx.nature == "expense" and re.search(r"\bPix\b", tx.name, re.IGNORECASE):
        return CategoryResult(category="Pix a pessoas")

    # Entrada sem regra → sem categoria (receita)
    if tx.nature == "income":
        return CategoryResult(category="")  # receita sem envelope

    # Resto: conta → Outros; cartão → Compras
    if tx.account_type == "CREDIT":
        return CategoryResult(category="Compras")
    return CategoryResult(category="Outros")
