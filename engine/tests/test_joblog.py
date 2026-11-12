"""job_run 在連線被切斷時仍要把執行記成失敗（2026-11-12 Neon idle-in-transaction 逾時事件）。"""

import pytest

import engine.db
from engine.joblog import job_run


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self.conn.lost:
            raise RuntimeError("the connection is lost")
        self.conn.statements.append((sql, params))

    def fetchone(self):
        return (1,)


class FakeConn:
    def __init__(self):
        self.lost = False
        self.statements = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        if self.lost:
            raise RuntimeError("the connection is lost")

    def rollback(self):
        if self.lost:
            raise RuntimeError("the connection is lost")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _finished_status(conn):
    updates = [p for sql, p in conn.statements if sql.startswith("UPDATE job_logs SET status = %s")]
    return updates[-1][0] if updates else None


def test_failure_recorded_on_same_connection():
    conn = FakeConn()
    with pytest.raises(ValueError):
        with job_run(conn, "fetch"):
            raise ValueError("boom")
    assert _finished_status(conn) == "failed"


def test_failure_recorded_on_new_connection_when_lost(monkeypatch):
    conn, fresh = FakeConn(), FakeConn()
    monkeypatch.setattr(engine.db, "connect", lambda: fresh)
    with pytest.raises(ValueError, match="idle-in-transaction"):
        with job_run(conn, "fetch"):
            conn.lost = True
            raise ValueError("idle-in-transaction timeout")
    assert _finished_status(fresh) == "failed"


def test_original_error_kept_when_reconnect_fails(monkeypatch):
    conn = FakeConn()

    def broken():
        raise RuntimeError("db down")

    monkeypatch.setattr(engine.db, "connect", broken)
    with pytest.raises(ValueError, match="boom"):
        with job_run(conn, "fetch"):
            conn.lost = True
            raise ValueError("boom")
