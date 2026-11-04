"""Estado local SQLite: pluggy_id → sure_id + hash + status + pendências."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
@dataclass
class TxState:
    pluggy_id: str
    sure_id: str
    payload_hash: str
    status: str
    last_seen_at: str
    pending_review: bool = False
    pending_reason: str | None = None


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tx_links (
                pluggy_id TEXT PRIMARY KEY,
                sure_id TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                pending_review INTEGER NOT NULL DEFAULT 0,
                pending_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS account_checkpoints (
                pluggy_account_id TEXT PRIMARY KEY,
                last_synced_to TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS balance_reviews (
                pluggy_account_id TEXT PRIMARY KEY,
                sure_account_id TEXT NOT NULL,
                flagged_at TEXT NOT NULL,
                diff_abs TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get(self, pluggy_id: str) -> TxState | None:
        row = self._conn.execute(
            "SELECT * FROM tx_links WHERE pluggy_id = ?", (pluggy_id,)
        ).fetchone()
        if not row:
            return None
        return TxState(
            pluggy_id=row["pluggy_id"],
            sure_id=row["sure_id"],
            payload_hash=row["payload_hash"],
            status=row["status"],
            last_seen_at=row["last_seen_at"],
            pending_review=bool(row["pending_review"]),
            pending_reason=row["pending_reason"],
        )

    def upsert(
        self,
        pluggy_id: str,
        sure_id: str,
        payload_hash: str,
        status: str,
        *,
        pending_review: bool = False,
        pending_reason: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO tx_links (
                pluggy_id, sure_id, payload_hash, status, last_seen_at,
                pending_review, pending_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pluggy_id) DO UPDATE SET
                sure_id=excluded.sure_id,
                payload_hash=excluded.payload_hash,
                status=excluded.status,
                last_seen_at=excluded.last_seen_at,
                pending_review=excluded.pending_review,
                pending_reason=excluded.pending_reason
            """,
            (
                pluggy_id,
                sure_id,
                payload_hash,
                status,
                now,
                1 if pending_review else 0,
                pending_reason,
            ),
        )
        self._conn.commit()

    def mark_pending_review(self, pluggy_id: str, reason: str) -> None:
        self._conn.execute(
            """
            UPDATE tx_links
            SET pending_review = 1, pending_reason = ?
            WHERE pluggy_id = ?
            """,
            (reason, pluggy_id),
        )
        self._conn.commit()

    def touch_seen(self, pluggy_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE tx_links SET last_seen_at = ? WHERE pluggy_id = ?",
            (now, pluggy_id),
        )
        self._conn.commit()

    def all_for_accounts(self, pluggy_account_ids: list[str] | None = None) -> list[TxState]:
        rows = self._conn.execute("SELECT * FROM tx_links").fetchall()
        return [
            TxState(
                pluggy_id=r["pluggy_id"],
                sure_id=r["sure_id"],
                payload_hash=r["payload_hash"],
                status=r["status"],
                last_seen_at=r["last_seen_at"],
                pending_review=bool(r["pending_review"]),
                pending_reason=r["pending_reason"],
            )
            for r in rows
        ]

    def set_checkpoint(self, pluggy_account_id: str, last_synced_to: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO account_checkpoints (pluggy_account_id, last_synced_to, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(pluggy_account_id) DO UPDATE SET
                last_synced_to=excluded.last_synced_to,
                updated_at=excluded.updated_at
            """,
            (pluggy_account_id, last_synced_to, now),
        )
        self._conn.commit()

    def flag_balance_review(
        self, pluggy_account_id: str, sure_account_id: str, diff_abs: str
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO balance_reviews (
                pluggy_account_id, sure_account_id, flagged_at, diff_abs
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(pluggy_account_id) DO UPDATE SET
                sure_account_id=excluded.sure_account_id,
                flagged_at=excluded.flagged_at,
                diff_abs=excluded.diff_abs
            """,
            (pluggy_account_id, sure_account_id, now, diff_abs),
        )
        self._conn.commit()

    def clear_balance_review(self, pluggy_account_id: str) -> None:
        self._conn.execute(
            "DELETE FROM balance_reviews WHERE pluggy_account_id = ?",
            (pluggy_account_id,),
        )
        self._conn.commit()
