"""Spool local SQLite : file FIFO bornée de lots à envoyer. Aucune donnée personnelle."""
from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from typing import List, Optional

MAX_BATCHES = 5000
MAX_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class SpooledBatch:
    batch_id: str
    created_at: float
    payload: str
    attempts: int


class Spool:
    def __init__(self, path: str, max_batches: int = MAX_BATCHES, max_bytes: int = MAX_BYTES) -> None:
        self.path = path
        self.max_batches, self.max_bytes = max_batches, max_bytes
        self.dropped_batches = 0
        self.recovered_corruption = False
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, mode=0o700, exist_ok=True)
        self._db = self._open()

    # ------------------------------------------------------------ ouverture
    def _open(self) -> sqlite3.Connection:
        try:
            return self._connect()
        except sqlite3.DatabaseError:
            self._quarantine_file()
            self.recovered_corruption = True
            return self._connect()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS batches ("
                " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                " batch_id TEXT NOT NULL UNIQUE,"
                " created_at REAL NOT NULL,"
                " payload TEXT NOT NULL,"
                " size INTEGER NOT NULL,"
                " attempts INTEGER NOT NULL DEFAULT 0)"
            )
            db.execute("SELECT COUNT(*) FROM batches").fetchone()
        except sqlite3.DatabaseError:
            db.close()
            raise
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return db

    def _quarantine_file(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            src = self.path + suffix
            if os.path.exists(src):
                try:
                    os.replace(src, "%s.corrupt-%d%s" % (self.path, int(os.path.getmtime(src)), suffix))
                except OSError:
                    try:
                        os.remove(src)
                    except OSError:
                        pass

    # -------------------------------------------------------------- écriture
    def enqueue(self, payload: str, created_at: float, batch_id: Optional[str] = None) -> str:
        bid = batch_id or str(uuid.uuid4())
        size = len(payload.encode("utf-8"))
        self._db.execute(
            "INSERT OR IGNORE INTO batches (batch_id, created_at, payload, size) VALUES (?, ?, ?, ?)",
            (bid, created_at, payload, size),
        )
        self._enforce_bounds()
        return bid

    def _enforce_bounds(self) -> None:
        count, total = self._db.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM batches").fetchone()
        while count > 1 and (count > self.max_batches or total > self.max_bytes):
            row = self._db.execute("SELECT seq, size FROM batches ORDER BY seq LIMIT 1").fetchone()
            self._db.execute("DELETE FROM batches WHERE seq = ?", (row[0],))
            count -= 1
            total -= row[1]
            self.dropped_batches += 1

    # --------------------------------------------------------------- lecture
    def peek(self, limit: int = 1) -> List[SpooledBatch]:
        rows = self._db.execute(
            "SELECT batch_id, created_at, payload, attempts FROM batches ORDER BY seq LIMIT ?", (limit,)
        ).fetchall()
        return [SpooledBatch(*r) for r in rows]

    def mark_attempt(self, batch_id: str) -> None:
        self._db.execute("UPDATE batches SET attempts = attempts + 1 WHERE batch_id = ?", (batch_id,))

    def ack(self, batch_id: str) -> bool:
        cur = self._db.execute("DELETE FROM batches WHERE batch_id = ?", (batch_id,))
        return cur.rowcount > 0

    def pending(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM batches").fetchone()[0]

    def oldest_age(self, now: float) -> Optional[float]:
        row = self._db.execute("SELECT MIN(created_at) FROM batches").fetchone()
        return None if row[0] is None else max(0.0, now - row[0])

    def close(self) -> None:
        self._db.close()
