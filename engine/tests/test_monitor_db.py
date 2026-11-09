"""盤中停損監控與資料庫整合測試：觸及停損、同日不重複、偏空收緊、通知開關、心跳。"""

import json
from datetime import date, datetime

import httpx
import psycopg
import pytest

from engine.config import TZ, load_settings
from engine.monitor.quotes import Quote
from engine.monitor.runner import Monitor, check_once
from engine.notify.alerts import collect_alerts
from engine.notify.line import LineClient
from engine.repository import Repository

SETTINGS = load_settings("free")
NOW = datetime(2026, 11, 9, 10, 3, tzinfo=TZ)   # 週一盤中


class FakeQuotes:
    def __init__(self, prices):
        self.prices = prices
        self.calls = []

    def fetch(self, stocks):
        self.calls.append(dict(stocks))
        return {s: Quote(s, p, "last", NOW) for s, p in self.prices.items() if s in stocks}


def _line():
    sent = []
    client = LineClient("token", "U1", SETTINGS.notify["line"], sleep=lambda s: None,
                        transport=httpx.MockTransport(lambda r: sent.append(json.loads(r.content))
                                                      or httpx.Response(200, json={})))
    return client, sent


@pytest.fixture()
def repo(conn):
    r = Repository(conn)
    r.upsert_stocks([{"stock_id": "2330", "name": "台積電", "market": "twse"},
                     {"stock_id": "6488", "name": "環球晶", "market": "tpex"}])
    for sid, cost, stop in (("2330", 2100, 1950), ("6488", 500, 460)):
        r.save_holding({"stock_id": sid, "shares": 1000, "avg_cost": cost, "opened_date": date(2026, 10, 1),
                        "stop_loss": stop, "take_profit": None, "highest_close": cost})
    conn.commit()
    return r


def test_stop_alert_once_per_day(repo):
    quotes, (line, sent) = FakeQuotes({"2330": 1940.0, "6488": 480.0}), _line()
    result = check_once(SETTINGS, repo, quotes, line, NOW)
    assert result["alerts"] == ["2330"] and len(sent) == 1
    assert quotes.calls[0] == {"2330": "twse", "6488": "tpex"}
    text = sent[0]["messages"][0]["text"]
    assert "2330 台積電 現價 1940 ≤ 停損 1950" in text
    # 同一天再次跌破不重複提醒
    again = check_once(SETTINGS, repo, quotes, line, NOW.replace(hour=11))
    assert again["alerts"] == [] and len(sent) == 1
    # 隔天會再提醒
    nxt = check_once(SETTINGS, repo, quotes, line, NOW.replace(day=10))
    assert nxt["alerts"] == ["2330"] and len(sent) == 2


def test_bearish_tightens_intraday_stop(repo):
    repo.upsert("market", [{"date": date(2026, 11, 6), "light": "bearish", "inputs": {}, "group_strength": []}])
    repo.conn.commit()
    line, sent = _line()
    # 6488 成本 500，偏空收緊為 475；現價 474 觸發（原停損 460 不會）
    result = check_once(SETTINGS, repo, FakeQuotes({"2330": 2000.0, "6488": 474.0}), line, NOW)
    assert result["alerts"] == ["6488"]


def test_notification_switch_off(repo):
    with repo.conn.cursor() as cur:
        cur.execute("UPDATE settings SET value = %s WHERE key = 'notify'", (json.dumps({"intraday_stop_loss": False}),))
    repo.conn.commit()
    line, sent = _line()
    result = check_once(SETTINGS, repo, FakeQuotes({"2330": 1000.0}), line, NOW)
    assert result["skipped"] == "盤中停損通知已關閉" and sent == []


def test_tick_writes_heartbeat(repo, database_url):
    line, _ = _line()
    m = Monitor(SETTINGS, lambda: psycopg.connect(database_url), FakeQuotes({"2330": 2000.0}), line, lambda: set(),
                clock=lambda: NOW, sleep=lambda s: None)
    m.cfg = {**m.cfg, "keep_awake": False}
    assert m.tick() == SETTINGS.notify["intraday"]["interval_minutes"] * 60
    assert repo.last_heartbeat("monitor_heartbeat") is not None
    # 收盤後批次：最後心跳在 10:03 左右（測試資料庫用實際時間），心跳很久沒更新時會警告
    with repo.conn.cursor() as cur:
        cur.execute("UPDATE job_logs SET started_at = %s WHERE job = 'monitor_heartbeat'",
                    (datetime(2026, 11, 9, 10, 3, tzinfo=TZ),))
        cur.execute("INSERT INTO job_logs (job, run_date, status, detail) VALUES ('fetch', '2026-11-09', 'success', "
                    "'{\"quality\": {\"ok\": true}}'), ('fetch', '2026-11-06', 'success', '{}')")
    repo.conn.commit()
    alerts = collect_alerts(SETTINGS, repo, date(2026, 11, 9), datetime(2026, 11, 9, 16, 0, tzinfo=TZ))
    assert alerts == ["盤中停損監控可能已停止：最後心跳 11/09 10:03"]


def test_schedule_check_alerts_once(repo, database_url):
    line, sent = _line()
    evening = datetime(2026, 11, 9, 17, 35, tzinfo=TZ)
    m = Monitor(SETTINGS, lambda: psycopg.connect(database_url), FakeQuotes({}), line, lambda: set(),
                clock=lambda: evening, sleep=lambda s: None)
    m.tick()
    assert len(sent) == 1 and "排程未執行" in sent[0]["messages"][0]["text"]
    m._schedule_checked = None                     # 程式重啟後同一天不重複推播
    m.tick()
    assert len(sent) == 1
    with repo.conn.cursor() as cur:                # 有抓取紀錄就不警告
        cur.execute("INSERT INTO job_logs (job, run_date, status) VALUES ('fetch', '2026-11-10', 'success')")
    repo.conn.commit()
    later = datetime(2026, 11, 10, 17, 35, tzinfo=TZ)
    m2 = Monitor(SETTINGS, lambda: psycopg.connect(database_url), FakeQuotes({}), line, lambda: set(),
                 clock=lambda: later, sleep=lambda s: None)
    m2.tick()
    assert len(sent) == 1
