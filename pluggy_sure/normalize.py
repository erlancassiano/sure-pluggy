"""Normalização pura: datas UTC→SP, sinal por tipo de conta, Decimal, descrição."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class NormalizedTx:
    pluggy_id: str
    account_id: str
    account_type: str  # BANK | CREDIT
    local_date: date
    amount: Decimal  # sempre positivo (absoluto a enviar ao Sure)
    nature: str  # expense | income
    name: str
    currency: str
    status: str  # PENDING | POSTED
    pluggy_category: str | None
    notes: str
    tags: tuple[str, ...] = ()
    deferred: bool = False
    defer_reason: str | None = None
    installment_number: int | None = None
    total_installments: int | None = None
    raw_signed_amount: Decimal = Decimal("0")  # sinal Pluggy original (p/ pareamento)


def to_decimal(value: Any) -> Decimal:
    """Converte valor numérico para Decimal sem passar por float."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # Evita artefatos de float: usa repr curto via str do valor formatado
        return Decimal(str(value))
    if value is None:
        raise InvalidOperation("valor nulo")
    return Decimal(str(value))


def utc_to_sao_paulo_date(iso_utc: str) -> date:
    """Converte ISO UTC para data civil em America/Sao_Paulo.

    Ex.: 2026-03-01T02:30:00Z → 2026-02-28 (cruza meia-noite).
    """
    text = iso_utc.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(SAO_PAULO).date()


def collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def nature_from_account(account_type: str, amount: Decimal) -> tuple[Decimal, str]:
    """BANK: negativo=expense, positivo=income.
    CREDIT: positivo=expense (gasto), negativo=income (estorno/pagamento).
    Retorna (amount_abs, nature).
    """
    abs_amount = abs(amount)
    at = account_type.upper()
    if at == "CREDIT":
        nature = "expense" if amount >= 0 else "income"
    else:  # BANK e demais
        nature = "expense" if amount < 0 else "income"
    return abs_amount, nature


def normalize_transaction(
    raw: dict[str, Any],
    account_type: str,
) -> NormalizedTx:
    """Normaliza uma transação Pluggy para o payload Sure.

    Compra em moeda estrangeira: usa amountInAccountCurrency (BRL da fatura).
    Sem valor BRL → deferred (aguardando_valor_brl), fora do plano de envio.
    """
    pluggy_id = str(raw["id"])
    account_id = str(raw["accountId"])
    local_date = utc_to_sao_paulo_date(str(raw["date"]))
    name = collapse_whitespace(str(raw.get("description") or ""))
    status = str(raw.get("status") or "POSTED").upper()
    currency_code = str(raw.get("currencyCode") or "BRL").upper()
    pluggy_category = raw.get("category")
    if pluggy_category is not None:
        pluggy_category = str(pluggy_category)

    amount_raw = to_decimal(raw["amount"])
    amount_in_account = raw.get("amountInAccountCurrency")

    deferred = False
    defer_reason = None
    signed_for_nature = amount_raw

    if currency_code != "BRL":
        if amount_in_account is None:
            deferred = True
            defer_reason = "aguardando_valor_brl"
            # Mantém amount absoluto do estrangeiro só para hash interno; não envia
            abs_amount = abs(amount_raw)
            nature = "expense"
        else:
            # Valor BRL da fatura (com IOF embutido pela instituição).
            # Mantém o sinal da operação original (Pluggy) para nature.
            brl = to_decimal(amount_in_account)
            # amountInAccountCurrency costuma vir positivo; aplica sinal do amount
            if amount_raw < 0:
                signed_for_nature = -abs(brl)
            else:
                signed_for_nature = abs(brl)
            abs_amount, nature = nature_from_account(account_type, signed_for_nature)
            currency_code = "BRL"
    else:
        abs_amount, nature = nature_from_account(account_type, amount_raw)

    tags: list[str] = []
    if status == "PENDING":
        tags.append("pendente")

    meta = raw.get("creditCardMetadata") or {}
    inst_n = meta.get("installmentNumber")
    inst_t = meta.get("totalInstallments")
    notes_parts: list[str] = []
    if inst_n is not None and inst_t is not None:
        tags.append("parcelado")
        notes_parts.append(f"parcela {inst_n}/{inst_t}")
    notes_parts.append(f"pluggy:{pluggy_id}")

    return NormalizedTx(
        pluggy_id=pluggy_id,
        account_id=account_id,
        account_type=account_type.upper(),
        local_date=local_date,
        amount=abs_amount if not deferred else abs(amount_raw),
        nature=nature,
        name=name,
        currency=currency_code if not deferred else str(raw.get("currencyCode") or "USD"),
        status=status,
        pluggy_category=pluggy_category,
        notes=" · ".join(notes_parts),
        tags=tuple(tags),
        deferred=deferred,
        defer_reason=defer_reason,
        installment_number=int(inst_n) if inst_n is not None else None,
        total_installments=int(inst_t) if inst_t is not None else None,
        raw_signed_amount=signed_for_nature if not deferred else amount_raw,
    )


def payload_hash(tx: NormalizedTx, category: str | None) -> str:
    """Hash estável dos campos enviados (para detectar update)."""
    import hashlib

    parts = [
        tx.pluggy_id,
        tx.local_date.isoformat(),
        format(tx.amount, "f"),
        tx.nature,
        tx.name,
        tx.status,
        category or "",
        tx.notes,
        ",".join(tx.tags),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest
