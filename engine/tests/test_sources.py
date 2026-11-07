from datetime import date, datetime, timedelta

import httpx
import pytest

from engine.config import TZ
from engine.sources import finmind as fm
from engine.sources.common import HttpClient, SourceError, parse_tw_date, to_number
from engine.sources.exchanges import _is_common_stock, _table
from engine.sources.quota import QuotaTracker


def test_to_number():
    assert to_number("1,234.5") == 1234.5
    assert to_number("+3.2") == 3.2
    assert to_number("--") is None
    assert to_number("") is None
    assert to_number(5) == 5.0


def test_parse_tw_date():
    assert parse_tw_date("1141107") == date(2025, 11, 7)
    assert parse_tw_date("114/11/07") == date(2025, 11, 7)
    assert parse_tw_date("2025-11-07") == date(2025, 11, 7)
    assert parse_tw_date("abc") is None


def test_common_stock_filter():
    assert _is_common_stock("2330")
    assert not _is_common_stock("0050")
    assert not _is_common_stock("2330A")
    assert not _is_common_stock("03001P")


def test_table_conversion():
    payload = {"fields": ["證券代號", "投信買賣超股數"], "data": [["2330", "1,000"]]}
    assert _table(payload) == [{"證券代號": "2330", "投信買賣超股數": "1,000"}]


class FakeClock:
    def __init__(self, start):
        self.now = start
        self.slept = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept += seconds
        self.now += timedelta(seconds=seconds)


def test_quota_waits_for_next_hour():
    clock = FakeClock(datetime(2026, 11, 9, 15, 59, 0, tzinfo=TZ))
    q = QuotaTracker("finmind", hourly_limit=10, safety_margin=0.5, clock=clock, sleep=clock.sleep)
    for _ in range(5):
        q.acquire()
    assert clock.slept == 0
    q.acquire()  # 第 6 次超過預算，等到下一個整點
    assert 60 <= clock.slept <= 70
    assert q.calls_this_hour == 1


def test_quota_preload_from_db():
    clock = FakeClock(datetime(2026, 11, 9, 15, 0, tzinfo=TZ))
    q = QuotaTracker("finmind", hourly_limit=10, safety_margin=1.0, clock=clock, sleep=clock.sleep)
    q.preload(10)
    q.acquire()
    assert clock.slept > 0


def test_http_retries_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    slept = []
    client = HttpClient(transport=httpx.MockTransport(handler), sleep=slept.append, backoff_base=1)
    assert client.get_json("https://example.test/x") == {"ok": True}
    assert slept == [1, 2]


def test_http_gives_up():
    client = HttpClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500)), sleep=lambda s: None, max_retries=2
    )
    with pytest.raises(SourceError):
        client.get_json("https://example.test/x")


def test_revenue_published_date_never_before_deadline():
    rows = [{"stock_id": "2330", "date": "2025-11-01", "revenue": "1000", "revenue_year": 2025, "revenue_month": 10}]
    parsed = fm.parse_month_revenue(rows)[0]
    assert parsed["period"] == "2025-10"
    assert parsed["published_date"] == date(2025, 11, 10)  # 法定期限晚於資料日期，取較晚者


def test_december_revenue_rolls_year():
    assert fm.revenue_published_date(2025, 12) == date(2026, 1, 10)


def test_statements_grouped_by_quarter():
    rows = [
        {"stock_id": "2330", "date": "2025-09-30", "type": "EPS", "value": "17.4"},
        {"stock_id": "2330", "date": "2025-09-30", "type": "Revenue", "value": "900000"},
        {"stock_id": "2330", "date": "2025-06-30", "type": "EPS", "value": "15.4"},
    ]
    parsed = fm.parse_statements(rows)
    assert [p["period"] for p in parsed] == ["2025Q2", "2025Q3"]
    assert parsed[1]["metrics"] == {"EPS": 17.4, "Revenue": 900000.0}
    assert parsed[1]["published_date"] == date(2025, 11, 14)
    assert fm.quarter_published_date(date(2025, 12, 31)) == date(2026, 3, 31)


def test_institutional_aggregation():
    rows = [
        {"stock_id": "2330", "date": "2025-11-07", "name": "Foreign_Investor", "buy": "300", "sell": "100"},
        {"stock_id": "2330", "date": "2025-11-07", "name": "Foreign_Dealer_Self", "buy": "10", "sell": "0"},
        {"stock_id": "2330", "date": "2025-11-07", "name": "Investment_Trust", "buy": "0", "sell": "50"},
        {"stock_id": "2330", "date": "2025-11-07", "name": "Dealer_self", "buy": "5", "sell": "0"},
    ]
    assert fm.parse_institutional(rows) == [
        {"stock_id": "2330", "date": date(2025, 11, 7), "foreign_net": 210, "trust_net": -50, "dealer_net": 5}
    ]
