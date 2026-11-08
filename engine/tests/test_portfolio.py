"""持股檢查、交易費用、交易統計的單元測試。"""

from datetime import date, timedelta

import pandas as pd
import pytest

from engine.config import load_settings
from engine.portfolio.monitor import check_holding, effective_stop
from engine.portfolio.stats import closed_trades, trade_stats
from engine.portfolio.trades import commission, sell_tax

RISK = load_settings("free").risk
COSTS = RISK["costs"]
AS_OF = date(2026, 11, 6)


def test_commission_and_tax():
    assert commission(100, 1000, COSTS) == 142          # 100,000 × 0.1425% = 142.5 → 142
    assert commission(10, 100, COSTS) == 20             # 最低 20 元
    assert sell_tax(100, 1000, COSTS) == 300
    discounted = {**COSTS, "commission_discount": 0.28}
    assert commission(100, 1000, discounted) == 39


def _days(n, end=AS_OF):
    return [d.date() for d in pd.bdate_range(end=pd.Timestamp(end), periods=n)]


def _holding(**kw):
    h = {"stock_id": "2330", "shares": 1000, "avg_cost": 100.0, "opened_date": _days(10)[0], "stop_loss": 92.0,
         "take_profit": 120.0, "highest_close": 100.0}
    h.update(kw)
    return h


def _closes(values):
    return list(zip(_days(len(values)), values, strict=True))


def test_stop_loss():
    r = check_holding(_holding(), _closes([100] * 9 + [91]), {}, [], AS_OF, False, RISK)
    assert list(r["triggered"]) == ["stop_loss"]


def test_bearish_tightens_stop():
    h = _holding()
    assert effective_stop(h, True, RISK) == 95.0
    r = check_holding(h, _closes([100] * 9 + [94.5]), {}, [], AS_OF, True, RISK)
    assert "stop_loss" in r["triggered"] and "收緊" in r["triggered"]["stop_loss"]
    assert not check_holding(h, _closes([100] * 9 + [94.5]), {}, [], AS_OF, False, RISK)["triggered"]


def test_trailing_stop_needs_activation():
    # 最高 115（+15%）回落到 103（−10.4%）→ 觸發
    r = check_holding(_holding(), _closes([100, 105, 110, 115, 112, 108, 106, 105, 104, 103]), {}, [], AS_OF, False,
                      RISK)
    assert "trailing_stop" in r["triggered"] and r["highest"] == 115
    # 最高只漲 3%，未啟動移動停利
    r = check_holding(_holding(stop_loss=80), _closes([100, 103, 95, 93, 93, 93, 93, 93, 93, 92.5]), {}, [], AS_OF,
                      False, RISK)
    assert "trailing_stop" not in r["triggered"]


def test_take_profit_and_score_drop_and_news():
    scores = {"short": {"date": AS_OF.isoformat(), "total": 42.0}, "long": {"date": AS_OF.isoformat(), "total": 48.0}}
    r = check_holding(_holding(), _closes([100] * 9 + [121]), scores, ["現金增資：公告"], AS_OF, False, RISK)
    assert {"take_profit", "score_drop", "news"} <= set(r["triggered"])
    old = {"short": {"date": (AS_OF - timedelta(days=3)).isoformat(), "total": 10.0}}
    assert "score_drop" not in check_holding(_holding(), _closes([100] * 10), old, [], AS_OF, False, RISK)["triggered"]


def test_stale_holding():
    days = RISK["exit"]["max_holding_days_no_gain"]
    h = _holding(opened_date=_days(days + 1)[0])
    r = check_holding(h, _closes([100] * (days + 1)), {}, [], AS_OF, False, RISK)
    assert "stale" in r["triggered"] and r["held_days"] == days
    r = check_holding(h, _closes([100] * days + [101]), {}, [], AS_OF, False, RISK)
    assert "stale" not in r["triggered"]


def test_no_price_today_skips():
    assert check_holding(_holding(), _closes([100] * 10)[:-1], {}, [], AS_OF, False, RISK) is None


def _t(i, d, side, price, shares, fee=0.0, tax=0.0, sid="2330"):
    return {"trade_id": i, "stock_id": sid, "date": d, "side": side, "price": price, "shares": shares,
            "fee": fee, "tax": tax}


def test_closed_trades_average_cost():
    trades = [
        _t(1, date(2026, 1, 5), "buy", 100, 1000, fee=142),
        _t(2, date(2026, 1, 6), "buy", 110, 1000, fee=156),
        _t(3, date(2026, 2, 5), "sell", 120, 1000, fee=171, tax=360),
        _t(4, date(2026, 3, 5), "sell", 90, 1000, fee=128, tax=270),
    ]
    closed, open_pos = closed_trades(trades)
    avg = (100_000 + 142 + 110_000 + 156) / 2000
    assert closed[0]["pnl"] == pytest.approx(120_000 - 171 - 360 - avg * 1000, abs=0.01)
    assert closed[1]["pnl"] == pytest.approx(90_000 - 128 - 270 - avg * 1000, abs=0.01)
    assert closed[0]["days"] == 31 and open_pos == {}


def test_trade_stats_and_benchmarks():
    trades = [
        _t(1, date(2026, 1, 5), "buy", 100, 1000), _t(2, date(2026, 2, 5), "sell", 110, 1000),   # +10%
        _t(3, date(2026, 3, 5), "buy", 100, 1000), _t(4, date(2026, 4, 5), "sell", 95, 1000),    # −5%
        _t(5, date(2026, 5, 5), "buy", 50, 2000, sid="2317"),                                     # 未實現
    ]
    bench = {"taiex": [(date(2026, 1, 2), 20000.0), (AS_OF, 22000.0)],
             "etf_0050": [(date(2026, 1, 5), 80.0), (AS_OF, 92.0)]}
    st = trade_stats(trades, {"2317": 55.0}, 1_000_000, AS_OF, bench)
    assert st["closed_trades"] == 2 and st["win_rate"] == 0.5
    assert st["avg_win_pct"] == pytest.approx(0.10) and st["avg_loss_pct"] == pytest.approx(-0.05)
    assert st["expectancy_pct"] == pytest.approx(0.025)
    assert st["realized_pnl"] == 5000 and st["unrealized_pnl"] == 10000
    assert st["portfolio_return"] == pytest.approx(0.015)
    assert st["benchmarks"] == {"taiex": pytest.approx(0.10), "etf_0050": pytest.approx(0.15)}


def test_trade_stats_empty():
    st = trade_stats([], {}, 1_000_000, AS_OF, {"taiex": []})
    assert st["closed_trades"] == 0 and st["win_rate"] is None and st["start"] is None
