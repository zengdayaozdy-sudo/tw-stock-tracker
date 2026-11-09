from datetime import date

from engine.config import load_settings
from engine.joblog import job_run
from engine.pipeline import fetch_daily
from engine.repository import Repository
from engine.sources.quota import QuotaTracker

D = date(2026, 11, 9)  # 星期一


def _stocks(repo):
    repo.upsert_stocks([
        {"stock_id": "2330", "name": "台積電", "market": "twse"},
        {"stock_id": "2317", "name": "鴻海", "market": "twse"},
    ])


def test_migration_is_idempotent(conn):
    from engine.db import migrate

    assert migrate() == []


def test_no_lookahead_financials(conn):
    repo = Repository(conn)
    _stocks(repo)
    repo.upsert("financials", [
        {"stock_id": "2330", "period_type": "month", "period": "2026-10",
         "published_date": date(2026, 11, 10), "metrics": {"revenue": 1}},
        {"stock_id": "2330", "period_type": "month", "period": "2026-09",
         "published_date": date(2026, 10, 10), "metrics": {"revenue": 2}},
    ])
    conn.commit()
    before = repo.financials_as_of("2330", date(2026, 11, 9), "month")
    assert [r["period"] for r in before] == ["2026-09"]
    after = repo.financials_as_of("2330", date(2026, 11, 10), "month")
    assert [r["period"] for r in after] == ["2026-09", "2026-10"]


def test_upsert_is_repeatable(conn):
    repo = Repository(conn)
    _stocks(repo)
    row = {"stock_id": "2330", "date": D, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10, "turnover": 20}
    repo.upsert("daily_prices", [row])
    repo.upsert("daily_prices", [{**row, "close": 3}])
    assert repo.count_rows("daily_prices", D) == 1


def test_chips_merge_partial_columns(conn):
    repo = Repository(conn)
    _stocks(repo)
    repo.upsert_chips([{"stock_id": "2330", "date": D, "foreign_net": 100, "trust_net": 5, "dealer_net": 0}])
    repo.upsert_chips([{"stock_id": "2330", "date": D, "margin_balance": 999}])
    with conn.cursor() as cur:
        cur.execute("SELECT foreign_net, margin_balance FROM chips WHERE stock_id='2330'")
        assert cur.fetchone() == (100, 999)


def test_job_log_failure_recorded(conn):
    try:
        with job_run(conn, "fetch", D):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with conn.cursor() as cur:
        cur.execute("SELECT status, detail->>'error' FROM job_logs")
        assert cur.fetchone() == ("failed", "boom")


class FakeExchange:
    def __init__(self, latest=D, tpex_latest=None):
        self.latest = latest
        self.tpex_latest = tpex_latest or latest

    def daily_prices(self, d):
        return [
            {"stock_id": "2330", "name": "台積電", "market": "twse", "date": self.latest,
             "open": 1000, "high": 1010, "low": 990, "close": 1005, "volume": 10**7, "turnover": 10**10},
            {"stock_id": "2317", "name": "鴻海", "market": "twse", "date": self.latest,
             "open": 200, "high": 205, "low": 198, "close": 204, "volume": 10**7, "turnover": 10**9},
            {"stock_id": "6488", "name": "環球晶", "market": "tpex", "date": self.tpex_latest,
             "open": 400, "high": 405, "low": 397, "close": 401, "volume": 10**6, "turnover": 4 * 10**8},
        ]

    def valuations(self, d):
        return [{"stock_id": "2330", "date": d, "pe": 25, "pb": 7, "dividend_yield": 1.5}]

    def institutional(self, d):
        return [{"stock_id": s, "date": d, "foreign_net": 1, "trust_net": 2, "dealer_net": 3} for s in ("2330", "2317")]

    def margin(self, d):
        return [{"stock_id": s, "date": d, "margin_balance": 10, "short_balance": 1} for s in ("2330", "2317")]

    last_errors: list = []

    def status_flags(self, d=None):
        return {"2317": {"attention"}}


class FakeFinMind:
    def __init__(self):
        self.quota = QuotaTracker("finmind", 600)
        self.calls = []

    def fetch(self, key, sid, start, end=None):
        self.quota.acquire()
        self.calls.append((key, sid))
        if key == "month_revenue":
            return [{"stock_id": sid, "date": "2026-11-05", "revenue": "100",
                     "revenue_year": 2026, "revenue_month": 10}]
        return []


def test_fetch_daily_free_mode(conn):
    settings = load_settings("free")
    repo = Repository(conn)
    finmind = FakeFinMind()
    # 第一次：建立股票清單後加入自選股
    fetch_daily(settings, repo, FakeExchange(), finmind, D)
    conn.cursor().execute("INSERT INTO watchlist (stock_id) VALUES ('2330')")
    result = fetch_daily(settings, repo, FakeExchange(), finmind, D)
    conn.commit()
    assert result.market_open
    assert result.counts["prices"] == 3
    # FinMind 只查自選股，且月營收已有資料後不會重複抓
    assert {sid for _, sid in finmind.calls} == {"2330"}
    assert ("month_revenue", "2330") in finmind.calls
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM stocks WHERE stock_id='2317'")
        assert cur.fetchone()[0] == ["attention"]


def test_fetch_waits_until_both_markets_updated(conn):
    # 2026-11-09 實際情況：櫃買已更新、證交所還是前一交易日
    settings = load_settings("free")
    ex = FakeExchange(latest=date(2026, 11, 6), tpex_latest=D)
    result = fetch_daily(settings, Repository(conn), ex, FakeFinMind(), D)
    assert not result.market_open
    assert result.counts["latest_available"] == {"twse": "2026-11-06", "tpex": "2026-11-09"}


def test_fetch_skips_holiday(conn):
    settings = load_settings("free")
    result = fetch_daily(settings, Repository(conn), FakeExchange(latest=date(2026, 11, 6)), FakeFinMind(), D)
    assert not result.market_open


def test_fetch_skips_weekend(conn):
    settings = load_settings("free")
    result = fetch_daily(settings, Repository(conn), FakeExchange(), FakeFinMind(), date(2026, 11, 8))
    assert not result.market_open
