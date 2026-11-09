"""盤中停損監控：報價解析、排程時間判斷、錯誤重試（不需資料庫）。"""

from datetime import date, datetime

import httpx
import pytest

from engine.config import TZ, load_dotenv, load_settings
from engine.monitor.quotes import TwseRealtimeQuotes, parse_quotes
from engine.monitor.runner import Monitor, stop_message
from engine.sources.common import HttpClient

SETTINGS = load_settings("free")


def test_parse_quotes_last_and_bid_fallback():
    payload = {"rtcode": "0000", "msgArray": [
        {"c": "2330", "z": "2135.0000", "y": "2100.0000", "b": "2130.0000_2125.0000_", "tlong": "1794110400000"},
        {"c": "6488", "z": "-", "y": "512.0000", "b": "511.0000_510.0000_"},
        {"c": "1234", "z": "-", "b": "-"},       # 沒有成交也沒有委買
        {"c": ""},
    ]}
    q = parse_quotes(payload)
    assert set(q) == {"2330", "6488"}
    assert q["2330"].price == 2135 and q["2330"].source == "last" and q["2330"].time.tzinfo is not None
    assert q["6488"].price == 511 and q["6488"].source == "bid"


def test_fetch_builds_channels_and_batches():
    seen = []

    def handler(request):
        seen.append(request.url.params["ex_ch"])
        codes = [c.split("_")[1].split(".")[0] for c in request.url.params["ex_ch"].split("|")]
        return httpx.Response(200, json={"rtcode": "0000", "msgArray": [{"c": c, "z": "10"} for c in codes]})

    http = HttpClient(transport=httpx.MockTransport(handler), max_retries=0, sleep=lambda s: None)
    quotes = TwseRealtimeQuotes(SETTINGS, http)
    quotes.batch = 2
    out = quotes.fetch({"2330": "twse", "6488": "tpex", "2317": "twse"})
    assert len(out) == 3 and len(seen) == 2
    assert "otc_6488.tw" in "|".join(seen) and "tse_2330.tw" in "|".join(seen)


def test_stop_message():
    msg = stop_message([{"stock_id": "2330", "name": "台積電", "price": 1900.0, "source": "bid", "stop": 1950.0,
                         "avg_cost": 2100.0, "shares": 1000}], datetime(2026, 11, 9, 10, 3, tzinfo=TZ))
    assert "【停損提醒】11/09 10:03" in msg
    assert "2330 台積電 現價 1900（無成交，以委買價計） ≤ 停損 1950" in msg and "-9.5%" in msg


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def _monitor(now, connect=None, holidays=frozenset()):
    return Monitor(SETTINGS, connect or (lambda: (_ for _ in ()).throw(RuntimeError("不應連線"))), None, None,
                   lambda: set(holidays), clock=FakeClock(now), sleep=lambda s: None)


def test_next_wake_schedule():
    fri_morning = datetime(2026, 11, 6, 8, 0, tzinfo=TZ)
    assert _monitor(fri_morning).next_wake(fri_morning) == ("wait", 3600)
    in_session = datetime(2026, 11, 6, 10, 0, tzinfo=TZ)
    assert _monitor(in_session).next_wake(in_session) == ("check", 0)
    after_close = datetime(2026, 11, 6, 14, 0, tzinfo=TZ)   # 收盤後先等到 17:30 檢查收盤後排程
    state, wait = _monitor(after_close).next_wake(after_close)
    assert state == "wait" and wait == 3.5 * 3600
    evening = datetime(2026, 11, 6, 18, 0, tzinfo=TZ)
    m = _monitor(evening)
    assert m.next_wake(evening) == ("schedule", 0)
    m._schedule_checked = evening.date()                     # 檢查過後 → 下週一 09:00
    state, wait = m.next_wake(evening)
    assert state == "wait" and wait == (datetime(2026, 11, 9, 9, 0, tzinfo=TZ) - evening).total_seconds()
    m = _monitor(evening, holidays={date(2026, 11, 9)})      # 週一休市 → 週二
    m._schedule_checked = evening.date()
    assert m.next_wake(evening)[1] == (datetime(2026, 11, 10, 9, 0, tzinfo=TZ) - evening).total_seconds()
    sat = datetime(2026, 11, 7, 10, 0, tzinfo=TZ)
    assert _monitor(sat).next_wake(sat)[0] == "wait"


def test_tick_outside_session_sleeps_without_connecting():
    m = _monitor(datetime(2026, 11, 7, 10, 0, tzinfo=TZ))
    assert m.tick() == 3600


def test_tick_error_backoff():
    calls = []

    def broken():
        calls.append(1)
        raise ConnectionError("網路中斷")

    m = _monitor(datetime(2026, 11, 6, 10, 0, tzinfo=TZ), connect=broken)
    m.cfg = {**m.cfg, "keep_awake": False}
    waits = [m.tick() for _ in range(6)]
    assert waits == [30, 60, 120, 300, 300, 300] and len(calls) == 6


def test_holiday_fetch_failure_falls_back_to_weekdays():
    m = Monitor(SETTINGS, lambda: None, None, None, lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert m.is_trading_day(date(2026, 11, 9))
    assert not m.is_trading_day(date(2026, 11, 8))


def test_load_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# 註解\nFOO_A=1\nexport FOO_B=\"two\"\nFOO_C=\nFOO_D=keep\n", encoding="utf-8")
    monkeypatch.delenv("FOO_A", raising=False)
    monkeypatch.delenv("FOO_B", raising=False)
    monkeypatch.setenv("FOO_D", "already")
    assert load_dotenv(env) == 2
    import os
    assert os.environ["FOO_A"] == "1" and os.environ["FOO_B"] == "two" and os.environ["FOO_D"] == "already"
    monkeypatch.delenv("FOO_A")
    monkeypatch.delenv("FOO_B")
    assert load_dotenv(tmp_path / "missing") == 0


@pytest.fixture(autouse=True)
def _no_caffeinate(monkeypatch):
    monkeypatch.setattr("engine.monitor.runner.subprocess.Popen", lambda *a, **k: None)


def test_tdcc_distribution():
    from engine.sources.tdcc import parse_distribution

    text = "\ufeff資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n" + "\n".join(
        [f"20261106,2330  ,{lvl},1,1,{pct}" for lvl, pct in [(1, 1.22), (11, 1.43), (12, 1.08), (13, 0.92),
                                                             (14, 0.75), (15, 84.24), (16, 0.0), (17, 100.0)]]
        + ["20261106,000218,15,1,1,100.00", "20261106,0050  ,15,1,1,90.00"])
    rows = parse_distribution(text, 12)
    assert rows == [{"stock_id": "2330", "date": date(2026, 11, 6), "major_holder_pct": 86.99}]
