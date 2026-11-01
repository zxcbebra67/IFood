import os
import sqlite3
from pathlib import Path

MIGRATIONS: list[tuple[str, ...]] = [
    ("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",),
]

def init_db(path: str | None = None) -> sqlite3.Connection:
    path = path or os.environ.get("DB_PATH", "./data/app.db")
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")

    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for v in range(current, len(MIGRATIONS)):
        conn.execute("BEGIN")
        try:
            for stmt in MIGRATIONS[v]:
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {v + 1}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise