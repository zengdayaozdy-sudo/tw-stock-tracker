"""進場條件放寬視窗（trigger_window_days）與分數出場最短持有天數。"""

from datetime import date, timedelta

import pandas as pd

from engine.portfolio.monitor import check_holding
from engine.signals.entry import above_ma60, golden_cross


def _series(values):
    idx = pd.date_range("2026-01-01", periods=len(values), freq="D")
    return pd.Series(values, index=idx, dtype=float)


def test_above_ma60_window():
    # 前 60 天 100，接著跌到 90 再兩天後站回 120、125、126
    close = _series([100.0] * 60 + [90.0, 120.0, 125.0, 126.0])
    assert above_ma60(close) is None                      # 今天不是穿越日
    hit = above_ma60(close, window=3)
    assert hit is not None and hit["days_ago"] == 2
    assert above_ma60(close, window=2) is None            # 穿越在 2 天前，視窗只有 2 天（今天、昨天）


def test_window_requires_still_above():
    close = _series([100.0] * 60 + [90.0, 120.0, 80.0])   # 穿越後又跌回季線下
    assert above_ma60(close, window=5) is None


def test_golden_cross_same_day_unchanged():
    close = _series([10.0] * 25 + [9.0] * 5 + [12.0])
    assert golden_cross(close, 5, 20) == golden_cross(close, 5, 20, window=1)


def _risk(min_days):
    return {"exit": {"min_score": 50, "score_drop_min_days": min_days, "max_holding_days_no_gain": 99,
                     "no_gain_threshold": 0.0, "trailing_activation": 0.05},
            "take_profit": {"trailing_percent": 0.10},
            "stop_loss": {"method": "percent", "percent": 0.08, "bearish_market_percent": 0.05}}


def test_score_drop_waits_min_days():
    d0 = date(2026, 11, 2)
    closes = [(d0 + timedelta(days=i), 100.0) for i in range(3)]
    h = {"stock_id": "2330", "opened_date": d0, "avg_cost": 100.0, "stop_loss": 90.0}
    as_of = closes[-1][0]
    scores = {"short": {"total": 40, "date": as_of.isoformat()}}
    early = check_holding(h, closes, scores, [], as_of, False, _risk(5))
    assert "score_drop" not in early["triggered"]
    now = check_holding(h, closes, scores, [], as_of, False, _risk(0))
    assert "score_drop" in now["triggered"]
