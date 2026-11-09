"""回測與資料庫整合：執行、保留區間只能驗證一次、指紋可重現。"""

import dataclasses
from datetime import date

import pytest

from engine.backtest.runner import BacktestError, run_backtest
from engine.config import load_settings
from engine.repository import Repository
from engine.tests.test_scoring import AS_OF
from engine.tests.test_scoring_db import _insert


@pytest.fixture()
def repo(conn):
    r = Repository(conn)
    for i, (sid, drift, growth) in enumerate([("1111", 0.002, 0.02), ("2222", -0.001, 0.0), ("3333", 0.001, 0.01)]):
        _insert(r, sid, drift, i + 1, growth)
    prices = r.closes(["1111"], date(2000, 1, 1), AS_OF)
    r.upsert("market_series", [{"series": "taiex", "date": d, "value": 20000 * 1.0003 ** i}
                               for i, (_s, d, _c) in enumerate(prices)])
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO watchlist (stock_id) VALUES (%s)", [("1111",), ("2222",), ("3333",)])
        cur.execute("INSERT INTO groups (name, kind) VALUES ('測試族群', 'custom') RETURNING group_id")
        gid = cur.fetchone()[0]
        cur.executemany("INSERT INTO group_members VALUES (%s, %s)", [(gid, s) for s in ("1111", "2222", "3333")])
    conn.commit()
    return r


def _settings():
    s = load_settings("free")
    return dataclasses.replace(s, backtest={**s.backtest, "start": "2025-11-03", "holdout_months": 3})


def test_tune_run_is_reproducible(repo):
    a = run_backtest(_settings(), repo, "tune", end=AS_OF)
    b = run_backtest(_settings(), repo, "tune", end=AS_OF)
    assert a["fingerprint"] == b["fingerprint"]
    assert a["metrics"] == b["metrics"] and a["trades"] == b["trades"]
    assert a["metrics"]["end"] < "2026-08-07"                 # 調參區間不含最後 3 個月
    assert any("選擇偏差" in w for w in a["warnings"])
    with repo.conn.cursor() as cur:
        cur.execute("SELECT count(*), count(DISTINCT fingerprint) FROM backtest_runs WHERE period = 'tune'")
        assert cur.fetchone() == (2, 1)
    # 參數不同 → 指紋不同
    c = run_backtest(_settings(), repo, "tune", end=AS_OF, overrides={"risk.stop_loss.percent": "0.05"}, save=False)
    assert c["fingerprint"] != a["fingerprint"]


def test_holdout_only_once(repo):
    first = run_backtest(_settings(), repo, "holdout", end=AS_OF)
    assert first["metrics"]["start"] >= "2026-08-07"
    with pytest.raises(BacktestError, match="只能驗證一次"):
        run_backtest(_settings(), repo, "holdout", end=AS_OF)
    assert run_backtest(_settings(), repo, "holdout", end=AS_OF, force=True, save=False)["metrics"]
