"""回測單元測試：手續費、績效指標、參數覆寫、不偷看未來、模擬可重現。"""

import dataclasses
from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine.backtest.plan import CachedIndicators, Planner
from engine.backtest.report import build_metrics, cagr, max_drawdown, render_markdown, trade_metrics
from engine.backtest.runner import apply_overrides, parse_sets, periods
from engine.backtest.sim import TwStockCommission, simulate
from engine.config import ConfigError, load_settings
from engine.scoring.indicators import StockFrames, compute_all
from engine.tests.test_scoring import AS_OF, frames

SETTINGS = load_settings("free")


def test_commission():
    c = TwStockCommission()
    assert c._getcommission(1000, 100, False) == 142                 # 買進：0.1425% 捨去
    assert c._getcommission(-1000, 100, False) == 142 + 300          # 賣出：加 0.3% 證交稅
    assert c._getcommission(10, 10, False) == 20                     # 最低 20 元


def test_metrics():
    pts = [(date(2024, 1, 1), 100.0), (date(2024, 7, 1), 130.0), (date(2025, 1, 1), 110.0)]
    assert max_drawdown(pts) == pytest.approx(110 / 130 - 1)
    assert cagr(pts) == pytest.approx(0.0997, abs=1e-3)
    m = trade_metrics([{"pnl": 100, "pnl_pct": 0.1, "days": 10, "reason": "trailing_stop"},
                       {"pnl": -50, "pnl_pct": -0.05, "days": 4, "reason": "stop_loss"}])
    assert m["win_rate"] == 0.5 and m["expectancy_pct"] == pytest.approx(0.025)
    assert m["exit_reasons"] == {"trailing_stop": 1, "stop_loss": 1}
    assert trade_metrics([])["win_rate"] is None


def test_overrides():
    s = apply_overrides(SETTINGS, parse_sets(["risk.stop_loss.percent=0.06", "backtest.allow_odd_lots=false"]))
    assert s.risk["stop_loss"]["percent"] == 0.06 and s.backtest["allow_odd_lots"] is False
    assert SETTINGS.risk["stop_loss"]["percent"] == 0.08            # 原設定不受影響
    with pytest.raises(ConfigError):
        apply_overrides(SETTINGS, {"risk.nope": "1"})
    with pytest.raises(ConfigError):
        apply_overrides(SETTINGS, {"plans.free": "1"})
    with pytest.raises(ConfigError):                                 # 權重加總必須為 1
        apply_overrides(SETTINGS, {"scoring.weights.short.technical": "0.9"})


def test_periods_split():
    p = periods(SETTINGS, date(2026, 11, 6))
    assert p["tune"][0] == date(2023, 1, 2) and p["holdout"][1] == date(2026, 11, 6)
    assert p["tune"][1] == date(2025, 11, 6) and p["holdout"][0] == date(2025, 11, 7)


def test_cached_indicators_match_full_compute():
    f = frames("1111", 0.002, foreign=100_000, growth=0.02)
    cache = CachedIndicators({"1111": f}, SETTINGS.scoring)
    d = date(2026, 3, 13)
    a, b = cache(f, d, SETTINGS.scoring), compute_all(f, d, SETTINGS.scoring)
    for k in a:
        assert a[k].current(d) == pytest.approx(b[k].current(d), nan_ok=True), k


def _universe(n_days=900):
    stocks = {"1111": frames("1111", 0.002, 200_000, 0.02, seed=1, n=n_days),
              "2222": frames("2222", -0.001, -50_000, 0.0, seed=2, n=n_days),
              "3333": frames("3333", 0.001, 50_000, 0.01, seed=3, n=n_days)}
    groups = {"測試族群": ("custom", set(stocks))}
    idx = stocks["1111"].prices.index
    taiex = [(d.date(), 20000 * (1.0003 ** i)) for i, d in enumerate(idx)]
    return stocks, groups, {"taiex": taiex}


def test_plan_does_not_look_ahead():
    stocks, groups, series = _universe()
    d = date(2026, 6, 5)
    base = Planner(SETTINGS, stocks, groups, series, {}).day(d)

    # 把 d 之後的資料全部改成極端值，d 當天的計畫必須完全相同
    future = {}
    for sid, f in stocks.items():
        p = f.prices.copy()
        p.loc[p.index > pd.Timestamp(d), ["open", "high", "low", "close"]] *= 3
        c = f.chips.copy()
        c.loc[c.index > pd.Timestamp(d), "foreign_net"] = -1e9
        fin = f.financials.copy()
        fin.loc[pd.to_datetime(fin["published_date"]) > pd.Timestamp(d), "metrics"] = [{"revenue": 1.0}] * int(
            (pd.to_datetime(fin["published_date"]) > pd.Timestamp(d)).sum())
        future[sid] = StockFrames(sid, p, c, f.valuations, fin, f.industry)
    taiex = [(t, v * (5 if t > d else 1)) for t, v in series["taiex"]]
    other = Planner(SETTINGS, future, groups, {"taiex": taiex}, {}).day(d)

    assert other.light == base.light
    assert other.scores == base.scores
    assert [(c.stock_id, c.levels.entry_high) for c in other.candidates] == \
        [(c.stock_id, c.levels.entry_high) for c in base.candidates]


def test_simulation_deterministic_and_charges_costs():
    stocks, groups, series = _universe(700)
    settings = dataclasses.replace(SETTINGS, backtest={**SETTINGS.backtest, "initial_capital": 2_000_000})
    planner = Planner(settings, stocks, groups, series, {})
    calendar = stocks["1111"].prices.index[-260:]
    plans = {t.date(): planner.day(t.date()) for t in calendar}
    prices = {sid: f.prices for sid, f in stocks.items()}
    groups_of = {sid: ["測試族群"] for sid in stocks}
    r1 = simulate(plans, prices, calendar, settings, groups_of)
    r2 = simulate(plans, prices, calendar, settings, groups_of)
    assert r1["trades"] == r2["trades"] and r1["final_value"] == r2["final_value"]   # 可重現
    assert sum(1 for p in plans.values() if p.candidates) > 0
    assert r1["trades"] or r1["open_positions"]                                       # 有實際下單
    for t in r1["trades"]:
        gross = (t["exit"] - t["entry"]) * t["shares"]
        assert t["pnl"] < gross                                                       # 已扣手續費與證交稅
        assert t["closed"] > t["opened"]                                              # 隔日以後才出場
    m = build_metrics(r1, {"taiex": series["taiex"]}, calendar[0].date(), calendar[-1].date())
    assert m["benchmarks"]["taiex"]["total_return"] > 0
    text = render_markdown(m, "tune", "免費模式", ["選擇偏差"], {"overrides": {}})
    assert "回測報告｜調參區間" in text and "選擇偏差" in text and "年化報酬" in text
    assert not np.isnan(m["final_value"])
    assert AS_OF  # 與評分測試共用同一組日期


def test_bearish_stop_changes_never_create_short_positions():
    """大盤燈號每天在偏空與中性之間切換，停損單每天改價：不可出現兩張停損單都成交而變成空單。"""
    stocks, groups, series = _universe(700)
    settings = dataclasses.replace(SETTINGS, backtest={**SETTINGS.backtest, "initial_capital": 2_000_000})
    planner = Planner(settings, stocks, groups, series, {})
    calendar = stocks["1111"].prices.index[-260:]
    plans = {t.date(): planner.day(t.date()) for t in calendar}
    for i, p in enumerate(plans.values()):
        p.light = "bearish" if i % 2 else "neutral"
    prices = {sid: f.prices for sid, f in stocks.items()}
    r = simulate(plans, prices, calendar, settings, {sid: ["測試族群"] for sid in stocks})
    assert r["anomalies"] == []
    assert all(0 <= e <= 1.0001 for _d, e in r["exposure"])
    for t in r["trades"]:
        assert t["shares"] > 0


# ---------- 還原股價與資料清理 ----------

def test_adjust_prices_back_adjusts_only_history():
    from engine.adjust import adjust_points, adjust_prices

    idx = pd.to_datetime(["2025-06-16", "2025-06-17", "2025-06-18", "2025-06-19"])
    p = pd.DataFrame({"open": [188.0, 188.0, 47.2, 47.0], "high": [190.0, 190.0, 48.0, 47.5],
                      "low": [187.0, 187.0, 47.0, 46.5], "close": [188.65, 188.65, 47.57, 47.1],
                      "volume": [1.0, 1.0, 4.0, 4.0]}, index=idx)
    out = adjust_prices(p, [(date(2025, 6, 18), 47.16 / 188.65)])         # 0050 一拆四
    assert out["close"].iloc[1] == pytest.approx(47.16)                   # 分割前一天換算成分割後價格
    assert out["close"].iloc[-1] == 47.1                                  # 最新價格不變
    assert out["volume"].tolist() == [1.0, 1.0, 4.0, 4.0]                 # 成交量不調整
    pts = adjust_points([(date(2025, 6, 17), 188.65), (date(2025, 6, 19), 47.1)], [(date(2025, 6, 18), 0.25)])
    assert pts[-1][1] / pts[0][1] - 1 == pytest.approx(47.1 / (188.65 * 0.25) - 1)
    assert adjust_prices(p, None) is p


def test_finmind_zero_price_rows_skipped_and_adjustments_parsed():
    from engine.sources import finmind as fm

    rows = [{"stock_id": "2317", "date": "2025-07-30", "open": 0, "max": 0, "min": 0, "close": 0,
             "Trading_Volume": 0, "Trading_money": 0},
            {"stock_id": "2317", "date": "2025-07-31", "open": 160, "max": 162, "min": 159, "close": 161,
             "Trading_Volume": 100, "Trading_money": 16100}]
    assert [r["date"] for r in fm.parse_prices(rows)] == [date(2025, 7, 31)]
    adj = fm.parse_adjustments([
        {"date": "2026-06-11", "stock_id": "2330", "before_price": 2255.0, "after_price": 2248.99},
        {"date": "2026-06-12", "stock_id": "2330", "before_price": 10.0, "after_price": 10.0},   # 無變動略過
    ], "dividend")
    assert adj == [{"stock_id": "2330", "date": date(2026, 6, 11), "kind": "dividend",
                    "before_price": 2255.0, "after_price": 2248.99}]


def test_exposure_metrics():
    from datetime import date as _d

    from engine.backtest.report import exposure_metrics

    m = exposure_metrics([(_d(2026, 1, 1), 0.0), (_d(2026, 1, 2), 0.5), (_d(2026, 1, 3), 1.0), (_d(2026, 1, 4), 0.5)])
    assert m["avg_exposure"] == 0.5 and m["invested_days"] == 0.75
    assert exposure_metrics([]) == {"avg_exposure": None, "invested_days": None}
