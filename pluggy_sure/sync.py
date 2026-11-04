"""Orquestração da sincronização Pluggy → Sure."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from pluggy_sure.categorize import PARENT_FORA, Rule, load_rules
from pluggy_sure.config import Settings, load_accounts_map
from pluggy_sure.logging_utils import truncate_id
from pluggy_sure.normalize import NormalizedTx, normalize_transaction, payload_hash, to_decimal
from pluggy_sure.plan import PlannedAction, TAG_REMOVED, build_plan, counts_for_report
from pluggy_sure.pluggy_client import PluggyClient
from pluggy_sure.reconcile import BalanceDiff, reconcile_balance
from pluggy_sure.state import StateStore
from pluggy_sure.sure_client import SureClient, SureValidationError

logger = logging.getLogger("pluggy_sure")


@dataclass
class SyncReport:
    counts: dict[str, int] = field(default_factory=dict)
    balance_reviews: list[BalanceDiff] = field(default_factory=list)
    unpaired_transfers: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exit_code: int = 0

    def print_summary(self) -> None:
        parts = [f"{k}: {v}" for k, v in self.counts.items()]
        print("Relatório: " + ", ".join(parts))
        if self.unpaired_transfers:
            print(
                f"transferências sem par: {len(self.unpaired_transfers)} "
                f"ids={','.join(truncate_id(i) for i in self.unpaired_transfers)}"
            )
        flagged = [b for b in self.balance_reviews if b.needs_review]
        if flagged:
            print(f"saldos para revisão: {len(flagged)}")
            for b in flagged:
                print(
                    f"  conta pluggy={truncate_id(b.pluggy_account_id)} "
                    f"sure={truncate_id(b.sure_account_id)}"
                )


class SyncRunner:
    def __init__(
        self,
        settings: Settings,
        pluggy: PluggyClient,
        sure: SureClient,
        state: StateStore,
        rules: list[Rule],
        accounts_map: dict[str, str],
    ) -> None:
        self.settings = settings
        self.pluggy = pluggy
        self.sure = sure
        self.state = state
        self.rules = rules
        self.accounts_map = accounts_map

    def run(
        self,
        *,
        since: date | None = None,
        days: int | None = None,
        dry_run: bool = False,
        now: date | None = None,
    ) -> SyncReport:
        today = now or date.today()
        if since is not None:
            date_from = since
        else:
            lookback = days if days is not None else self.settings.lookback_days
            date_from = today - timedelta(days=lookback)
        date_to = today

        report = SyncReport()
        self.pluggy.authenticate()

        account_types: dict[str, str] = {}
        pluggy_balances: dict[str, Decimal] = {}
        all_normalized: list[NormalizedTx] = []
        unmapped_account_warnings: list[str] = []

        item_ids = self.settings.pluggy_item_ids
        if not item_ids:
            logger.warning("PLUGGY_ITEM_IDS vazio — nenhuma conta será lida")

        for item_id in item_ids:
            try:
                item = self.pluggy.get_item(item_id)
                self.pluggy.warn_if_stale(item)
            except Exception:
                logger.warning("não foi possível ler item=%s", truncate_id(item_id))

            for acc in self.pluggy.list_accounts(item_id):
                aid = str(acc["id"])
                atype = str(acc.get("type") or "BANK")
                account_types[aid] = atype
                if acc.get("balance") is not None:
                    pluggy_balances[aid] = to_decimal(acc["balance"])

                if aid not in self.accounts_map:
                    # Spec: ignorar + aviso (só id truncado; sem nome/saldo).
                    # Não listamos txs da conta para reduzir I/O; o aviso cobre a omissão.
                    msg = (
                        f"conta não mapeada ignorada: "
                        f"pluggy_account_id={truncate_id(aid)}"
                    )
                    logger.warning("%s", msg)
                    unmapped_account_warnings.append(msg)
                    continue

                for raw in self.pluggy.list_transactions(
                    aid,
                    date_from=date_from.isoformat(),
                    date_to=date_to.isoformat(),
                ):
                    all_normalized.append(normalize_transaction(raw, atype))

        previously = {s.pluggy_id for s in self.state.all_for_accounts()}
        plan = build_plan(
            all_normalized,
            self.accounts_map,
            account_types,
            self.state,
            self.rules,
            previously_seen_ids_in_window=previously,
            match_days=self.settings.transfer_match_days,
            tariff_max=self.settings.transfer_tariff_max,
        )

        report.unpaired_transfers = list(plan.unpaired_transfer_ids)
        # Contas omitidas no fetch + avisos do plano (dedupe preservando ordem)
        report.warnings = list(
            dict.fromkeys([*unmapped_account_warnings, *plan.warnings])
        )
        report.counts = counts_for_report(plan)

        if dry_run:
            print(
                f"dry-run criar: {plan.counts.get('create', 0)}, "
                f"atualizar: {plan.counts.get('update', 0)}, "
                f"sem mudança: {plan.counts.get('noop', 0)}"
            )
            report.print_summary()
            return report

        fora_id = self.sure.ensure_category(PARENT_FORA)
        created = updated = noop = 0

        for action in plan.actions:
            try:
                applied = self._apply(action, fora_id)
                if applied == "create":
                    created += 1
                elif applied == "update":
                    updated += 1
                elif applied == "noop":
                    noop += 1
            except SureValidationError as exc:
                logger.error(
                    "Sure 422 pluggy_id=%s msg=%s",
                    truncate_id(exc.pluggy_id),
                    exc.message,
                )
                report.validation_errors.append(exc.pluggy_id)

        report.counts["criar"] = created
        report.counts["atualizar"] = updated
        report.counts["sem mudança"] = noop

        report.balance_reviews = self._reconcile_balances(
            account_types, pluggy_balances
        )
        for b in report.balance_reviews:
            if b.needs_review:
                self.state.flag_balance_review(
                    b.pluggy_account_id,
                    b.sure_account_id,
                    format(b.diff_abs, "f"),
                )
            else:
                self.state.clear_balance_review(b.pluggy_account_id)

        if report.validation_errors:
            report.exit_code = 1
        report.print_summary()
        return report

    def _apply(self, action: PlannedAction, fora_parent_id: str | None) -> str | None:
        if action.action == "noop" and action.tx:
            self.state.touch_seen(action.tx.pluggy_id)
            if action.pending_review:
                self.state.mark_pending_review(action.tx.pluggy_id, "pendente_revisao")
            return "noop"

        if action.action == "tag_missing" and action.sure_id:
            self.sure.add_tags_to_transaction(
                action.sure_id, list(action.tags_extra) or [TAG_REMOVED]
            )
            logger.info(
                "transação sumida na origem sure_id=%s (não apagada)",
                truncate_id(action.sure_id),
            )
            return None

        if action.action in ("skip", "defer") or action.tx is None:
            return None

        tx = action.tx
        sure_account_id = self.accounts_map[tx.account_id]
        cat = action.category
        category_id = None
        if cat and cat.category:
            parent_id = None
            if cat.parent or cat.fora_envelope:
                parent_id = fora_parent_id or self.sure.ensure_category(PARENT_FORA)
            category_id = self.sure.ensure_category(cat.category, parent_id=parent_id)

        tag_names = list(tx.tags) + list(action.tags_extra)
        if action.pending_review and "pendente-revisao" not in tag_names:
            tag_names.append("pendente-revisao")
        tag_ids = self.sure.resolve_tag_ids(tag_names)
        h = payload_hash(tx, cat.category if cat else None)

        if action.action == "create":
            _status, body = self.sure.create_transaction(
                tx, sure_account_id, category_id, tag_ids
            )
            sure_id = str(body["id"])
            self.state.upsert(
                tx.pluggy_id,
                sure_id,
                h,
                tx.status,
                pending_review=action.pending_review,
                pending_reason="pendente_revisao" if action.pending_review else None,
            )
            return "create"

        if action.action == "update" and action.sure_id:
            self.sure.update_transaction(
                action.sure_id,
                tx,
                category_id,
                tag_ids,
                skip_category=action.skip_category_update,
            )
            self.state.upsert(
                tx.pluggy_id,
                action.sure_id,
                h,
                tx.status,
                pending_review=action.pending_review,
                pending_reason="valor_alterado" if action.pending_review else None,
            )
            return "update"
        return None

    def _reconcile_balances(
        self,
        account_types: dict[str, str],
        pluggy_balances: dict[str, Decimal],
    ) -> list[BalanceDiff]:
        diffs: list[BalanceDiff] = []
        for pluggy_id, sure_id in self.accounts_map.items():
            if pluggy_id not in pluggy_balances:
                continue
            try:
                sure_bal, classification = self.sure.get_account_balance(sure_id)
            except Exception:
                logger.warning(
                    "não foi possível ler saldo Sure account=%s",
                    truncate_id(sure_id),
                )
                continue
            diff = reconcile_balance(
                pluggy_id,
                sure_id,
                account_types.get(pluggy_id, "BANK"),
                pluggy_balances[pluggy_id],
                sure_bal,
                sure_classification=classification,
            )
            if diff.needs_review:
                logger.warning(
                    "saldo diverge account_pluggy=%s (revisão)",
                    truncate_id(pluggy_id),
                )
            diffs.append(diff)
        return diffs


def build_runner(settings: Settings) -> SyncRunner:
    accounts_map = load_accounts_map(settings.accounts_map_path)
    rules = load_rules(settings.rules_path)
    state = StateStore(settings.state_path)
    pluggy = PluggyClient(settings.pluggy_client_id, settings.pluggy_client_secret)
    sure = SureClient(settings.sure_url, settings.sure_api_key)
    return SyncRunner(settings, pluggy, sure, state, rules, accounts_map)
