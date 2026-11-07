"""資料庫連線與 migration。"""

from __future__ import annotations

import os
from pathlib import Path

from engine.config import ROOT

MIGRATIONS_DIR = ROOT / "db" / "migrations"


def connect():
    import psycopg

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("未設定 DATABASE_URL")
    return psycopg.connect(url)


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def migrate() -> list[str]:
    """依序執行尚未套用的 migration，回傳本次套用的檔名。"""
    applied_now: list[str] = []
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        cur.execute("SELECT filename FROM schema_migrations")
        done = {row[0] for row in cur.fetchall()}
        for path in migration_files():
            if path.name in done:
                continue
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            applied_now.append(path.name)
        conn.commit()
    return applied_now
