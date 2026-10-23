"""SQLite storage for links and API tokens."""
from __future__ import annotations

import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


class Store:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        schema = Path(__file__).resolve().parent.parent / "schema.sql"
        self._db.executescript(schema.read_text())

    def owner_for_token(self, token: str):
        row = self._db.execute("SELECT owner FROM tokens WHERE token = ?", (token,)).fetchone()
        return row[0] if row else None

    def create(self, url: str, owner: str) -> str:
        with self._lock:
            while True:
                code = "".join(secrets.choice(ALPHABET) for _ in range(7))
                try:
                    self._db.execute("INSERT INTO links VALUES (?, ?, ?, ?, 0)",
                                     (code, url, owner, datetime.now(timezone.utc).isoformat()))
                    self._db.commit()
                    return code
                except sqlite3.IntegrityError:
                    continue

    def resolve(self, code: str):
        with self._lock:
            row = self._db.execute("SELECT url FROM links WHERE code = ?", (code,)).fetchone()
            if row:
                self._db.execute("UPDATE links SET hits = hits + 1 WHERE code = ?", (code,))
                self._db.commit()
            return row[0] if row else None

    def stats(self, code: str, owner: str):
        row = self._db.execute("SELECT hits FROM links WHERE code = ? AND owner = ?", (code, owner)).fetchone()
        return row[0] if row else None
