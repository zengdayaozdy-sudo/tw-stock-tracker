"""資料庫整合測試：有 TEST_DATABASE_URL 時連到該資料庫，否則嘗試啟動本機臨時 PostgreSQL，都沒有就略過。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest


def _local_pg_bin() -> Path | None:
    for base in sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True):
        if (base / "initdb").exists():
            return base
    found = shutil.which("initdb")
    return Path(found).parent if found else None


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    pg = _local_pg_bin()
    if pg is None:
        pytest.skip("沒有可用的 PostgreSQL")
    tmp = tempfile.mkdtemp(prefix="pgtest-")
    data = Path(tmp) / "data"
    run_as = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
    if run_as:
        shutil.chown(tmp, "postgres")
    subprocess.run([*run_as, str(pg / "initdb"), "-D", str(data), "-A", "trust", "-U", "postgres"],
                   check=True, capture_output=True)
    port = "54329"
    proc = subprocess.Popen([*run_as, str(pg / "postgres"), "-D", str(data), "-p", port, "-k", tmp,
                             "-c", "listen_addresses=127.0.0.1"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"postgresql://postgres@127.0.0.1:{port}/postgres"
    import psycopg

    for _ in range(50):
        try:
            psycopg.connect(url).close()
            break
        except psycopg.OperationalError:
            time.sleep(0.2)
    yield url
    proc.terminate()
    proc.wait()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture()
def conn(database_url, monkeypatch):
    import psycopg

    from engine import db

    monkeypatch.setenv("DATABASE_URL", database_url)
    with psycopg.connect(database_url) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        c.commit()
    db.migrate()
    c = psycopg.connect(database_url)
    yield c
    c.close()
