"""交易登錄、持股檢查、統計的資料庫整合測試。"""

from datetime import date

import pandas as pd
import pytest

from engine.config import load_settings
from engine.notify.report import render_blocks, report_data
from engine.pipeline import universe
from engine.portfolio.monitor import run_holdings
from engine.portfolio.stats import portfolio_stats
from engine.portfolio.trades import TradeError, record_trade
from engine.repository import Repository

AS_OF = date(2026, 11, 6)
DAYS = [d.date() for d in pd.bdate_range(end=pd.Timestamp(AS_OF), periods=80)]
SETTINGS = load_settings("free")


@pytest.fixture()
def repo(conn):
    r = Repository(conn)
    r.upsert_stocks([{"stock_id": s, "name": n, "market": "twse", "industry": "半導體業"}
                     for s, n in (("2330", "台積電"), ("2317", "鴻海"))])
    rows = []
    for sid, base in (("2330", 100.0), ("2317", 200.0)):
        for i, d in enumerate(DAYS):
            c = base + (i if sid == "2330" else 0)  # 2330 一路上漲，2317 持平
            rows.append({"stock_id": sid, "date": d, "open": c, "high": c, "low": c, "close": c, "volume": 1_000_000,
                         "turnover": None})
    r.upsert("daily_prices", rows)
    r.upsert("market_series", [{"series": "taiex", "date": DAYS[0], "value": 20000},
                               {"series": "taiex", "date": AS_OF, "value": 21000}])
    with conn.cursor() as cur:
        cur.execute("INSERT INTO job_logs (job, run_date, status, detail) VALUES ('fetch', %s, 'success', '{}')",
                    (AS_OF,))
    conn.commit()
    return r


def test_record_buy_with_signal_snapshot(repo):
    repo.upsert("scores", [{"stock_id": "2330", "date": DAYS[10], "horizon": "short", "total": 82, "fundamental": 50,
                            "technical": 80, "chips": 70, "breakdown": {}, "status": "ok"}])
    repo.replace_signals(DAYS[10], "buy", [{
        "stock_id": "2330", "date": DAYS[10], "horizon": "short", "side": "buy", "triggers": ["above_ma60"],
        "entry_low": 108, "entry_high": 111, "stop_loss": 101, "take_profit": 130, "suggested_lots": 1,
        "reason": "短線 82 分", "detail": {}}])
    repo.conn.commit()
    result = record_trade(SETTINGS, repo, "2330", DAYS[11], "buy", 111, 1000)
    t, h = result["trade"], result["holding"]
    assert t["fee"] == 158 and t["tax"] == 0
    assert t["score_snapshot"]["short"]["total"] == 82
    assert t["signal_snapshot"]["stop_loss"] == 101
    assert h["stop_loss"] == 101 and h["take_profit"] == 130
    assert h["avg_cost"] == pytest.approx((111_000 + 158) / 1000)
    assert "2330" in universe(SETTINGS, repo)  # 持股自動納入追蹤範圍


def test_buy_without_signal_uses_rule_stop_and_average(repo):
    record_trade(SETTINGS, repo, "2317", DAYS[0], "buy", 200, 1000, fee=0)
    h = record_trade(SETTINGS, repo, "2317", DAYS[1], "buy", 210, 1000, fee=0)["holding"]
    assert h["stop_loss"] == 184.0             # 200 × (1 − 8%)
    assert h["shares"] == 2000 and h["avg_cost"] == 205
    assert h["opened_date"] == DAYS[0]


def test_sell_rules(repo):
    with pytest.raises(TradeError):
        record_trade(SETTINGS, repo, "2330", AS_OF, "sell", 150, 1000)
    record_trade(SETTINGS, repo, "2330", DAYS[0], "buy", 100, 2000)
    with pytest.raises(TradeError):
        record_trade(SETTINGS, repo, "2330", AS_OF, "sell", 150, 3000)
    part = record_trade(SETTINGS, repo, "2330", DAYS[40], "sell", 140, 1000)
    assert part["trade"]["tax"] == 420 and part["holding"]["shares"] == 1000
    done = record_trade(SETTINGS, repo, "2330", DAYS[50], "sell", 150, 1000)
    assert done["holding"] is None and repo.holding("2330") is None
    with pytest.raises(TradeError):
        record_trade(SETTINGS, repo, "9999", AS_OF, "buy", 10, 1000)


def test_holdings_check_writes_sell_signals(repo):
    record_trade(SETTINGS, repo, "2317", DAYS[50], "buy", 230, 1000)   # 停損 211.5，現價 200
    record_trade(SETTINGS, repo, "2330", DAYS[70], "buy", 170, 1000)   # 上漲中，不觸發
    result = run_holdings(SETTINGS, repo, AS_OF)
    assert result["checked"] == 2
    sells = {s["stock_id"]: s for s in result["sell_signals"]}
    assert list(sells) == ["2317"] and "跌破停損" in sells["2317"]["reason"]
    assert repo.holding("2330")["highest_close"] == 179     # 更新開倉以來最高收盤
    # 重跑不重複，且報告列出賣出提醒
    run_holdings(SETTINGS, repo, AS_OF)
    assert len(repo.signals_on(AS_OF, "sell")) == 1
    text = "\n".join(render_blocks(report_data(SETTINGS, repo, AS_OF)))
    assert "【持股提醒】" in text and "2317 鴻海：跌破停損" in text
    assert "2330 台積電 1 張｜成本 170.24 現價 179（+5.1%）｜停損 156" in text


def test_holdings_check_skips_holiday(repo):
    with repo.conn.cursor() as cur:
        cur.execute("INSERT INTO job_logs (job, run_date, status) VALUES ('fetch', '2026-11-07', 'skipped')")
    repo.conn.commit()
    assert run_holdings(SETTINGS, repo, date(2026, 11, 7))["status"] == "closed"


def test_portfolio_stats(repo):
    record_trade(SETTINGS, repo, "2330", DAYS[0], "buy", 100, 1000, fee=0)
    record_trade(SETTINGS, repo, "2330", DAYS[40], "sell", 140, 1000, fee=0, tax=0)
    record_trade(SETTINGS, repo, "2317", DAYS[60], "buy", 200, 1000, fee=0)
    st = portfolio_stats(repo, AS_OF)
    assert st["closed_trades"] == 1 and st["win_rate"] == 1.0
    assert st["realized_pnl"] == 40_000 and st["unrealized_pnl"] == 0
    assert st["portfolio_return"] == pytest.approx(0.04)
    assert st["benchmarks"]["taiex"] == pytest.approx(0.05)
    assert st["benchmarks"]["etf_0050"] is None    # 尚無 0050 資料
