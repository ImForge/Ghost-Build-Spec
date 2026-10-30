"""SQLite persistence: alerts, evidence, shares, approvals, audit, baselines.

One connection guarded by one lock. The brain has six threads touching the
database and none of them hold it for long, so a connection pool would be more
machinery than this earns. WAL keeps readers from blocking the writer.
"""
from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

log = logging.getLogger("brain.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts(
  id TEXT PRIMARY KEY, ts REAL, last_seen REAL, cam TEXT, type TEXT, severity TEXT,
  zone TEXT, line TEXT, track_ids TEXT, message TEXT, narration TEXT, status TEXT,
  count INTEGER, evidence_id TEXT, features TEXT, dedupe_key TEXT);
CREATE INDEX IF NOT EXISTS alerts_ts ON alerts(ts DESC);
CREATE INDEX IF NOT EXISTS alerts_dedupe ON alerts(dedupe_key, last_seen);

CREATE TABLE IF NOT EXISTS evidence(
  id TEXT PRIMARY KEY, alert_id TEXT, cam TEXT, status TEXT, created_at REAL,
  unsealed_at REAL, nonce_hex TEXT, tag_hex TEXT, meta_json TEXT, size INTEGER,
  flagged INTEGER DEFAULT 0, path TEXT);

CREATE TABLE IF NOT EXISTS shares(
  evidence_id TEXT, keyholder_role TEXT, idx INTEGER, share_hex TEXT,
  PRIMARY KEY(evidence_id, keyholder_role));

CREATE TABLE IF NOT EXISTS approvals(
  evidence_id TEXT, keyholder_role TEXT, ts REAL,
  PRIMARY KEY(evidence_id, keyholder_role));

CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, action TEXT,
  target TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS audit_ts ON audit(ts DESC);

CREATE TABLE IF NOT EXISTS baseline(
  id INTEGER PRIMARY KEY AUTOINCREMENT, cam TEXT, ts REAL, features TEXT);
CREATE INDEX IF NOT EXISTS baseline_cam ON baseline(cam);
"""


def new_id(prefix: str) -> str:
    """`a_7c1e9b2d4f3a` style identifiers: prefix plus 12 hex characters."""
    return f"{prefix}_{secrets.token_hex(6)}"


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        log.info("sqlite ready at %s", path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- plumbing ---------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # --- alerts -----------------------------------------------------------
    def insert_alert(self, alert: dict[str, Any]) -> None:
        self.execute(
            """INSERT INTO alerts(id, ts, last_seen, cam, type, severity, zone, line,
                                  track_ids, message, narration, status, count,
                                  evidence_id, features, dedupe_key)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                alert["id"], alert["ts"], alert["last_seen"], alert["cam"], alert["type"],
                alert["severity"], alert.get("zone"), alert.get("line"),
                json.dumps(alert.get("track_ids") or []), alert["message"],
                alert.get("narration"), alert["status"], alert.get("count", 1),
                alert.get("evidence_id"), json.dumps(alert.get("features") or {}),
                alert["dedupe_key"],
            ),
        )

    def update_alert(self, alert_id: str, **fields: Any) -> None:
        if not fields:
            return
        for key in ("track_ids", "features"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE alerts SET {sets} WHERE id=?", (*fields.values(), alert_id))

    def get_alert(self, alert_id: str) -> dict[str, Any] | None:
        row = self.query_one("SELECT * FROM alerts WHERE id=?", (alert_id,))
        return _alert_from_row(row) if row else None

    def find_open_by_dedupe(self, dedupe_key: str, since: float) -> dict[str, Any] | None:
        row = self.query_one(
            """SELECT * FROM alerts
               WHERE dedupe_key=? AND status IN ('open','acked') AND last_seen >= ?
               ORDER BY last_seen DESC LIMIT 1""",
            (dedupe_key, since),
        )
        return _alert_from_row(row) if row else None

    def last_fired_at(self, dedupe_key: str) -> float:
        row = self.query_one(
            "SELECT MAX(ts) AS ts FROM alerts WHERE dedupe_key=?", (dedupe_key,)
        )
        return float(row["ts"]) if row and row["ts"] is not None else 0.0

    def list_alerts(self, *, status: str | None = None, type_: str | None = None,
                    cam: str | None = None, since: float | None = None,
                    limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM alerts WHERE 1=1"
        params: list[Any] = []
        for column, value in (("status", status), ("type", type_), ("cam", cam)):
            if value:
                sql += f" AND {column}=?"
                params.append(value)
        if since is not None:
            sql += " AND ts >= ?"
            params.append(since)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        return [_alert_from_row(r) for r in self.query(sql, params)]

    def count_open_alerts(self) -> int:
        row = self.query_one("SELECT COUNT(*) AS n FROM alerts WHERE status='open'")
        return int(row["n"]) if row else 0

    # --- evidence ---------------------------------------------------------
    def insert_evidence(self, evidence_id: str, alert_id: str | None, cam: str,
                        status: str = "requested") -> None:
        self.execute(
            """INSERT OR REPLACE INTO evidence(id, alert_id, cam, status, created_at)
               VALUES(?,?,?,?,?)""",
            (evidence_id, alert_id, cam, status, time.time()),
        )

    def update_evidence(self, evidence_id: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE evidence SET {sets} WHERE id=?", (*fields.values(), evidence_id))

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM evidence WHERE id=?", (evidence_id,))

    def insert_shares(self, evidence_id: str,
                      shares: Iterable[tuple[str, int, str]]) -> None:
        for role, idx, share_hex in shares:
            self.execute(
                """INSERT OR REPLACE INTO shares(evidence_id, keyholder_role, idx, share_hex)
                   VALUES(?,?,?,?)""",
                (evidence_id, role, idx, share_hex),
            )

    def get_shares(self, evidence_id: str) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM shares WHERE evidence_id=? ORDER BY idx", (evidence_id,)
        )

    def add_approval(self, evidence_id: str, role: str) -> bool:
        """Record one role's approval. False if that role had already approved."""
        try:
            self.execute(
                "INSERT INTO approvals(evidence_id, keyholder_role, ts) VALUES(?,?,?)",
                (evidence_id, role, time.time()),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def get_approvals(self, evidence_id: str) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM approvals WHERE evidence_id=? ORDER BY ts", (evidence_id,)
        )

    def clear_approvals(self, evidence_id: str) -> None:
        self.execute("DELETE FROM approvals WHERE evidence_id=?", (evidence_id,))

    def delete_evidence(self, evidence_id: str) -> None:
        for table in ("shares", "approvals"):
            self.execute(f"DELETE FROM {table} WHERE evidence_id=?", (evidence_id,))
        self.execute("DELETE FROM evidence WHERE id=?", (evidence_id,))

    def list_evidence(self, *, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return self.query("SELECT * FROM evidence WHERE status=?", (status,))
        return self.query("SELECT * FROM evidence ORDER BY created_at DESC")

    # --- audit ------------------------------------------------------------
    def audit(self, actor: str, action: str, target: str, detail: Any = None) -> None:
        self.execute(
            "INSERT INTO audit(ts, actor, action, target, detail) VALUES(?,?,?,?,?)",
            (time.time(), actor, action, target,
             detail if isinstance(detail, str) or detail is None else json.dumps(detail)),
        )

    def list_audit(self, limit: int = 200, target: str | None = None) -> list[dict[str, Any]]:
        if target:
            return self.query(
                "SELECT * FROM audit WHERE target=? ORDER BY ts DESC LIMIT ?",
                (target, max(1, min(int(limit), 2000))),
            )
        return self.query(
            "SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (max(1, min(int(limit), 2000)),)
        )

    # --- anomaly baselines ------------------------------------------------
    def insert_baseline(self, cam: str, features: Sequence[float]) -> None:
        self.execute(
            "INSERT INTO baseline(cam, ts, features) VALUES(?,?,?)",
            (cam, time.time(), json.dumps([float(f) for f in features])),
        )

    def get_baseline(self, cam: str) -> list[list[float]]:
        rows = self.query("SELECT features FROM baseline WHERE cam=? ORDER BY ts", (cam,))
        return [json.loads(r["features"]) for r in rows]

    def count_baseline(self, cam: str | None = None) -> dict[str, int]:
        if cam:
            row = self.query_one("SELECT COUNT(*) AS n FROM baseline WHERE cam=?", (cam,))
            return {cam: int(row["n"]) if row else 0}
        return {
            r["cam"]: int(r["n"])
            for r in self.query("SELECT cam, COUNT(*) AS n FROM baseline GROUP BY cam")
        }

    def clear_baseline(self, cam: str) -> None:
        self.execute("DELETE FROM baseline WHERE cam=?", (cam,))


def _alert_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Rehydrate the JSON columns so callers always see the §3.7 shape."""
    out = dict(row)
    out["track_ids"] = json.loads(row["track_ids"] or "[]")
    out["features"] = json.loads(row["features"] or "{}")
    return out
