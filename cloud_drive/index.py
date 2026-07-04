"""Local SQLite index tracking what has been synced to S3."""

import hashlib
import sqlite3
from pathlib import Path


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS files (
            local_path  TEXT PRIMARY KEY,
            s3_key      TEXT NOT NULL,
            size        INTEGER NOT NULL,
            mtime       REAL NOT NULL,
            sha256      TEXT NOT NULL,
            etag        TEXT,
            storage_class TEXT NOT NULL,
            synced_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Index:
    def __init__(self, db_path: str):
        self._conn = _connect(db_path)

    def get(self, local_path: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM files WHERE local_path = ?", (local_path,)
        ).fetchone()

    def needs_upload(self, local_path: Path) -> bool:
        try:
            stat = local_path.stat()
        except OSError:
            return False  # unreadable file — skip it
        row = self.get(str(local_path))
        if row is None:
            return True
        if row["size"] != stat.st_size or abs(row["mtime"] - stat.st_mtime) > 1:
            return True
        return False

    def upsert(
        self,
        local_path: Path,
        s3_key: str,
        checksum: str,
        etag: str,
        storage_class: str,
    ) -> None:
        stat = local_path.stat()
        self._conn.execute(
            """
            INSERT INTO files (local_path, s3_key, size, mtime, sha256, etag, storage_class, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(local_path) DO UPDATE SET
                s3_key=excluded.s3_key, size=excluded.size, mtime=excluded.mtime,
                sha256=excluded.sha256, etag=excluded.etag,
                storage_class=excluded.storage_class, synced_at=excluded.synced_at
            """,
            (str(local_path), s3_key, stat.st_size, stat.st_mtime,
             checksum, etag, storage_class),
        )
        self._conn.commit()

    def remove(self, local_path: str) -> None:
        self._conn.execute("DELETE FROM files WHERE local_path = ?", (local_path,))
        self._conn.commit()

    def all_synced(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM files ORDER BY synced_at DESC"
        ).fetchall()

    def stats(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*) as count, SUM(size) as total_bytes FROM files"
        ).fetchone()
        return {"count": row["count"], "total_bytes": row["total_bytes"] or 0}

    def close(self) -> None:
        self._conn.close()
