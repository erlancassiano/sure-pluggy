"""CLI: python -m pluggy_sure sync --since 2026-01-01 [--dry-run] | --days 15"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from pluggy_sure.config import ConfigError, load_settings
from pluggy_sure.logging_utils import setup_logging
from pluggy_sure.sync import build_runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pluggy_sure",
        description="Sincronização unidirecional Pluggy → Sure",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="Sincroniza transações")
    sync.add_argument(
        "--since",
        type=date.fromisoformat,
        help="Backfill a partir desta data (YYYY-MM-DD)",
    )
    sync.add_argument(
        "--days",
        type=int,
        help="Janela incremental em dias (padrão: SYNC_LOOKBACK_DAYS)",
    )
    sync.add_argument(
        "--dry-run",
        action="store_true",
        help="Calcula o plano sem POST/PATCH nem gravar estado",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return getattr(exc, "code", 2)

    setup_logging(settings.log_level)

    if args.command == "sync":
        if args.since is None and args.days is None:
            # Padrão: incremental com lookback configurado
            args.days = settings.lookback_days
        runner = build_runner(settings)
        try:
            report = runner.run(
                since=args.since,
                days=args.days,
                dry_run=args.dry_run,
            )
        except SystemExit as exc:
            # PluggyAuthError / SureAuthError / SureConnectionError
            msg = str(exc)
            print(msg, file=sys.stderr)
            code = getattr(exc, "code", None)
            if code is not None:
                return int(code)
            if "Pluggy" in msg:
                return 2
            if "Sure fora" in msg:
                return 3
            return 2
        return report.exit_code

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
