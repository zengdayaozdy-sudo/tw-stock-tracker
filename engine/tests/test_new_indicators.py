"""2026-11-09 新增指標：營收加速、財報驚喜、本益成長比、存貨與合約負債、相對強弱、投信持股比、借券、質押、MSCI。"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine.config import load_settings
from engine.filters.events import TradingCalendar, index_review_events
from engine.scoring.indicators import chips, fundamental, pledge_value, relative_strength
from engine.scoring.model import indicator_weights

CFG = load_settings("free").scoring
AS_OF = date(2026, 11, 9)


def _months(values: list[float], start="2024-01"):
    rows = []
    p = pd.Period(start, "M")
    for v in values:
        pub = (p + 1).to_timestamp() + pd.Timedelta(days=9)
        rows.append({"period_type": "month", "period": str(p), "published_date": pub.date(), "metrics": {"revenue": v}})
        p += 1
    return rows


def _quarters(items: list[dict], start="2023Q1"):
    rows = []
    p = pd.Period(start, "Q")
    for m in items:
        end = p.end_time.date()
        rows.append({"period_type": "quarter", "period": f"{p.year}Q{p.quarter}",
                     "published_date": date(end.year, end.month, 28) + pd.Timedelta(days=45), "metrics": m})
        p += 1
    return rows


def test_revenue_acceleration_positive_when_growth_speeds_up():
    # 前兩年持平，最近 3 個月大增 → 近 3 月年增率高於近 12 月 → 加速為正
    vals = [100.0] * 24 + [100.0] * 9 + [150.0] * 3
    fin = pd.DataFrame(_months(vals))
    as_of = pd.Timestamp(fin["published_date"].max()).date()
    ind = fundamental(fin, pd.DataFrame(columns=["pe", "pb", "dividend_yield"]), as_of, CFG)
    assert ind["revenue_acceleration"].current(as_of) > 0.3
    # 公布日前不可用（不偷看未來）
    earlier = fundamental(fin, pd.DataFrame(columns=["pe", "pb", "dividend_yield"]), AS_OF, CFG)
    assert earlier["revenue_acceleration"].current(AS_OF) == pytest.approx(0)


def test_earnings_surprise_and_peg():
    eps = [1.0, 1.0, 1.0, 1.0, 1.1, 0.9, 1.05, 0.95, 1.0, 1.1, 0.9, 1.0, 2.0]  # 最後一季大幅優於去年同季
    fin = pd.DataFrame(_quarters([{"EPS": e} for e in eps]))
    last_pub = pd.Timestamp(fin["published_date"].max())
    dates = pd.bdate_range(last_pub - pd.Timedelta(days=30), last_pub + pd.Timedelta(days=5))
    val = pd.DataFrame({"pe": 20.0, "pb": 2.0, "dividend_yield": 3.0}, index=dates)
    as_of = (last_pub + pd.Timedelta(days=5)).date()
    ind = fundamental(fin, val, as_of, CFG)
    assert ind["earnings_surprise"].current(as_of) > 3
    # 近四季 EPS 1.1+0.9+1.0+2.0=5.0，前四季 0.9+1.05+0.95+1.0=3.9 → 年增 28.2%
    assert ind["peg"].current(as_of) == pytest.approx(20 / ((5.0 / 3.9 - 1) * 100))


def test_peg_missing_when_eps_shrinks():
    eps = [2.0] * 8 + [1.0] * 4
    fin = pd.DataFrame(_quarters([{"EPS": e} for e in eps]))
    last_pub = pd.Timestamp(fin["published_date"].max())
    val = pd.DataFrame({"pe": 20.0, "pb": 2.0, "dividend_yield": 3.0},
                       index=pd.bdate_range(last_pub, last_pub + pd.Timedelta(days=3)))
    as_of = (last_pub + pd.Timedelta(days=3)).date()
    assert fundamental(fin, val, as_of, CFG)["peg"].current(as_of) is None


def test_order_backlog_rewards_falling_inventory_days_and_rising_contract_liabilities():
    base = {"Inventories": 100.0, "CostOfGoodsSold": 100.0, "ContractLiabilitiesCurrent": 50.0, "EPS": 1.0}
    better = {"Inventories": 80.0, "CostOfGoodsSold": 100.0, "ContractLiabilitiesCurrent": 75.0, "EPS": 1.0}
    fin = pd.DataFrame(_quarters([base] * 4 + [better]))
    as_of = pd.Timestamp(fin["published_date"].max()).date()
    v = fundamental(fin, pd.DataFrame(columns=["pe", "pb", "dividend_yield"]), as_of, CFG)["order_backlog"]
    assert v.current(as_of) == pytest.approx((0.2 + 0.5) / 2)


def test_relative_strength_excludes_last_month_and_uses_benchmark():
    idx = pd.bdate_range("2026-01-01", periods=200)
    close = pd.Series(np.linspace(100, 200, 200), index=idx)
    bench = pd.Series(np.linspace(100, 120, 200), index=idx)
    rs = relative_strength(close, bench, 120, 20)
    own = close.iloc[-21] / close.iloc[-141] - 1
    b = bench.iloc[-21] / bench.iloc[-141] - 1
    assert rs.iloc[-1] == pytest.approx(own - b)


def test_trust_holding_lending_and_pledge():
    idx = pd.bdate_range("2026-06-01", periods=80)
    prices = pd.DataFrame({"close": 100.0, "volume": 1_000_000.0}, index=idx)
    ch = pd.DataFrame({"foreign_net": 0.0, "trust_net": 10_000.0, "dealer_net": 0.0, "foreign_holding_pct": 50.0,
                       "margin_balance": 1000.0, "short_balance": 10.0, "major_holder_pct": np.nan,
                       "sbl_balance": np.linspace(1_000_000, 2_000_000, 80), "branches": None}, index=idx)
    ind = chips(ch, prices, CFG, shares=100_000_000, pledge=(date(2026, 8, 19), 85.0))
    as_of = idx[-1].date()
    assert ind["trust_holding"].current(as_of) == pytest.approx(10_000 * 60 / 1e8 * 100)
    assert ind["securities_lending"].current(as_of) > 0          # 借券餘額增加（之後反向計分）
    assert ind["pledge"].current(as_of, 200) == 0.0              # 質押 85% → 0 分
    assert ind["pledge"].current(date(2026, 8, 1)) is None       # 公布前不可用
    assert pledge_value(10) == 0.5 and pledge_value(50) == pytest.approx(0.3)


def test_locked_indicators_weights_redistributed():
    free, pro = load_settings("free"), load_settings("pro")
    w = indicator_weights(free, "long", "fundamental")
    assert "analyst_revision" not in w and sum(w.values()) == pytest.approx(1)
    assert "analyst_revision" not in indicator_weights(pro, "long", "fundamental")  # 任何 FinMind 方案都沒有
    assert "broker_branches" in indicator_weights(pro, "short", "chips")


def test_msci_events_only_for_large_caps():
    cfg = load_settings("free").filters["events"]
    ev = index_review_events(["2330", "6488"], {"2330": 4e13, "6488": 1e10}, cfg, TradingCalendar())
    nov = [e for e in ev if "2026 年 11 月" in e["note"]]
    assert {e["stock_id"] for e in nov} == {"2330"}
    assert {e["date"] for e in nov} == {date(2026, 11, 11), date(2026, 11, 30)}  # 12/01 生效 → 11/30 收盤換股
