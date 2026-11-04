"""Conciliação: pareamento de transferências/faturas e bater saldo.

Referências Conta Azul (desempate, sem conflitar com a spec):
- Transferência entre contas (não classificar como gasto; casar débito/crédito):
  https://ajuda.contaazul.com/hc/pt-br/articles/46020337381133
- Pagamento de fatura = transferência conta corrente → cartão:
  https://ajuda.contaazul.com/hc/pt-br/articles/46019129874957
- Tarifa bancária: crédito pode chegar menor que o débito; a diferença
  é registrada como tarifa (Revisar valores), não impede o pareamento.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from pluggy_sure.normalize import NormalizedTx, to_decimal

# Tolerância provisória alinhada à decisão da equipe (3 dias corridos).
DEFAULT_MATCH_DAYS = 3

# Teto default para diferença débito−crédito tratada como tarifa (Conta Azul).
# 0 desliga o pareamento com tarifa. Configurável via SYNC_TRANSFER_TARIFF_MAX_BRL.
DEFAULT_TARIFF_MAX = Decimal("25.00")

# Diferença absoluta estritamente maior que R$ 1,00 sinaliza revisão.
BALANCE_DIFF_THRESHOLD = Decimal("1.00")


@dataclass(frozen=True)
class PairMatch:
    left_id: str
    right_id: str
    kind: str  # own_transfer | bill_payment
    tariff_amount: Decimal = Decimal("0")


@dataclass(frozen=True)
class BalanceDiff:
    pluggy_account_id: str
    sure_account_id: str
    pluggy_balance: Decimal
    sure_balance: Decimal
    diff_abs: Decimal
    needs_review: bool


def normalize_pluggy_balance(account_type: str, balance: Decimal) -> Decimal:
    """Normaliza saldo Pluggy para comparação.

    BANK: saldo disponível (positivo = crédito).
    CREDIT: fatura em aberto / valor devido → positivo = deve.
    Pluggy costuma reportar saldo de cartão como valor devido positivo.
    """
    bal = to_decimal(balance)
    if account_type.upper() == "CREDIT":
        return abs(bal)
    return bal


def normalize_sure_balance(
    account_type: str,
    balance: Decimal,
    classification: str | None = None,
) -> Decimal:
    """Normaliza saldo Sure para a mesma convenção da Pluggy.

    Sure (liability/credit card): positivo = valor devido
    (docs: https://docs.sure.am/guides/key-concepts/debt-accounts).
    API: GET /api/v1/accounts → balance / balance_cents (signed).
    """
    bal = to_decimal(balance)
    is_credit = account_type.upper() == "CREDIT" or (
        classification and classification.lower() in {"liability", "credit"}
    )
    if is_credit:
        return abs(bal)
    return bal


def reconcile_balance(
    pluggy_account_id: str,
    sure_account_id: str,
    account_type: str,
    pluggy_balance: Decimal,
    sure_balance: Decimal,
    sure_classification: str | None = None,
) -> BalanceDiff:
    """Compara saldos normalizados. needs_review se |diff| > 1.00.

    |diff| == 1.00 → não sinaliza; |diff| == 1.01 → sinaliza.
    Nada é ajustado automaticamente.
    """
    p = normalize_pluggy_balance(account_type, pluggy_balance)
    s = normalize_sure_balance(account_type, sure_balance, sure_classification)
    diff = abs(p - s)
    return BalanceDiff(
        pluggy_account_id=pluggy_account_id,
        sure_account_id=sure_account_id,
        pluggy_balance=p,
        sure_balance=s,
        diff_abs=diff,
        needs_review=diff > BALANCE_DIFF_THRESHOLD,
    )


def _within_days(a: date, b: date, days: int) -> bool:
    return abs((a - b).days) <= days


def _pair_kind(out: NormalizedTx, inn: NormalizedTx) -> str | None:
    if out.account_type == "BANK" and inn.account_type == "CREDIT":
        return "bill_payment"
    if out.account_type == "BANK" and inn.account_type == "BANK":
        return "own_transfer"
    if out.account_type == "CREDIT" and inn.account_type == "BANK":
        return "own_transfer"
    return None


def _amount_ok(
    out: NormalizedTx,
    inn: NormalizedTx,
    *,
    exact: bool,
    tariff_max: Decimal,
) -> Decimal | None:
    """Retorna tarifa (0 se exato) se os valores casam; senão None."""
    if exact:
        return Decimal("0") if out.amount == inn.amount else None
    if tariff_max <= 0:
        return None
    # Conta Azul: débito ≥ crédito; diferença = tarifa bancária
    if inn.amount >= out.amount:
        return None
    tariff = out.amount - inn.amount
    if tariff <= 0 or tariff > tariff_max:
        return None
    return tariff


def _match_pass(
    outs: list[NormalizedTx],
    ins: list[NormalizedTx],
    used: set[str],
    *,
    exact: bool,
    match_days: int,
    tariff_max: Decimal,
) -> list[PairMatch]:
    pairs: list[PairMatch] = []
    for out in outs:
        if out.pluggy_id in used:
            continue
        best: NormalizedTx | None = None
        best_kind: str | None = None
        best_tariff = Decimal("0")
        # Menor (dias, tarifa) vence — data mais próxima, depois menor tarifa
        best_score: tuple[int, Decimal] | None = None
        for inn in ins:
            if inn.pluggy_id in used:
                continue
            if out.account_id == inn.account_id:
                continue
            if not _within_days(out.local_date, inn.local_date, match_days):
                continue
            kind = _pair_kind(out, inn)
            if kind is None:
                continue
            tariff = _amount_ok(out, inn, exact=exact, tariff_max=tariff_max)
            if tariff is None:
                continue
            score = (abs((out.local_date - inn.local_date).days), tariff)
            if best_score is None or score < best_score:
                best = inn
                best_kind = kind
                best_tariff = tariff
                best_score = score
        if best is not None and best_kind is not None:
            used.add(out.pluggy_id)
            used.add(best.pluggy_id)
            pairs.append(
                PairMatch(
                    left_id=out.pluggy_id,
                    right_id=best.pluggy_id,
                    kind=best_kind,
                    tariff_amount=best_tariff,
                )
            )
    return pairs


def match_own_transfers_and_bill_payments(
    txs: Iterable[NormalizedTx],
    mapped_account_ids: set[str],
    match_days: int = DEFAULT_MATCH_DAYS,
    tariff_max: Decimal = DEFAULT_TARIFF_MAX,
) -> tuple[list[PairMatch], list[str]]:
    """Casa pontas com valor Decimal igual (ou débito−crédito ≤ tariff_max) e ≤ match_days.

    - Transferência própria: BANK↔BANK (ou contas mapeadas) sinais opostos.
    - Pagamento de fatura: débito BANK ↔ crédito CREDIT (pagamento no cartão).
    - Tarifa (Conta Azul): 1º passo valor exato; 2º passo crédito menor até tariff_max.

    Retorna (pares, ids sem par que deveriam ter par potencial).
    Conta Azul: transferência não é gasto
    (https://ajuda.contaazul.com/hc/pt-br/articles/46020337381133).
    """
    candidates = [
        t
        for t in txs
        if t.account_id in mapped_account_ids and not t.deferred
    ]
    used: set[str] = set()
    outs = [t for t in candidates if t.nature == "expense"]
    ins = [t for t in candidates if t.nature == "income"]

    pairs: list[PairMatch] = []
    # Passo 1: valor exato (Conta Azul — transferência padrão)
    pairs.extend(
        _match_pass(
            outs, ins, used, exact=True, match_days=match_days, tariff_max=tariff_max
        )
    )
    # Passo 2: crédito menor = tarifa bancária
    pairs.extend(
        _match_pass(
            outs, ins, used, exact=False, match_days=match_days, tariff_max=tariff_max
        )
    )

    # Pontas sem par: saídas que parecem transferência/fatura (categoria/desc)
    # mas não casaram — o planejador marca pendente-revisao.
    unpaired: list[str] = []
    transfer_hints = (
        "mesma titularidade",
        "transferência",
        "transferencia",
        "pagamento de fatura",
        "same person",
        "credit card payment",
    )
    for t in candidates:
        if t.pluggy_id in used:
            continue
        name_l = t.name.casefold()
        cat = (t.pluggy_category or "").casefold()
        looks_transfer = any(h in name_l for h in transfer_hints) or any(
            h in cat for h in ("transfer", "same person", "credit card payment")
        )
        if looks_transfer:
            unpaired.append(t.pluggy_id)

    return pairs, unpaired
