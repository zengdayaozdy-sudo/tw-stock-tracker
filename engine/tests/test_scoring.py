from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine.config import load_settings
from engine.scoring.indicators import StockFrames, fundamental, rsi, technical
from engine.scoring.model import indicator_weights, percentile, reasons, score_universe

AS_OF = date(2026, 11, 6)
DATES = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=900)


def make_prices(drift: float, seed: int = 1, n: int = 900) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.01, n)))
    dates = DATES[-n:]
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
         "volume": rng.integers(800_000, 1_200_000, n).astype(float)},
        index=dates,
    )


def make_chips(prices: pd.DataFrame, foreign: float, seed: int = 2) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(prices)
    return pd.DataFrame(
        {"foreign_net": foreign + rng.normal(0, 50_000, n), "trust_net": rng.normal(0, 20_000, n),
         "dealer_net": 0.0, "foreign_holding_pct": np.linspace(40, 40 + foreign / 10_000, n),
         "margin_balance": np.linspace(10_000, 9_000, n), "short_balance": 500.0,
         "major_holder_pct": np.nan, "branches": None},
        index=prices.index,
    )


def make_financials(growth: float) -> pd.DataFrame:
    rows = []
    for i in range(48):  # 2022-11 至 2026-10
        k = 2022 * 12 + 10 + i
        y, m = k // 12, k % 12 + 1
        pub_y, pub_m = (y + 1, 1) if m == 12 else (y, m + 1)
        rows.append({"period_type": "month", "period": f"{y}-{m:02d}",
                     "published_date": date(pub_y, pub_m, 10),
                     "metrics": {"revenue": 1000 * (1 + growth) ** i}})
    return pd.DataFrame(rows)


def frames(sid, drift, foreign=0.0, growth=0.0, seed=1, n=900) -> StockFrames:
    p = make_prices(drift, seed, n)
    v = pd.DataFrame({"pe": 15.0, "pb": 2.0, "dividend_yield": 3.0}, index=p.index)
    return StockFrames(sid, p, make_chips(p, foreign, seed + 10), v, make_financials(growth), industry="半導體業")


GROUPS = {"測試族群": ("custom", {"1111", "2222", "3333"})}


@pytest.fixture()
def results():
    settings = load_settings("free")
    stocks = {
        "1111": frames("1111", 0.002, foreign=200_000, growth=0.02, seed=1),
        "2222": frames("2222", -0.002, foreign=-200_000, growth=-0.01, seed=2),
        "3333": frames("3333", 0.0, seed=3),
    }
    return {(r.stock_id, r.horizon): r for r in score_universe(settings, stocks, GROUPS, AS_OF)}


def test_percentile():
    assert percentile([1, 2, 3, 4], 4) == pytest.approx(87.5)
    assert percentile([1, 2, 3, 4], 1) == pytest.approx(12.5)
    assert percentile([5, 5, 5], 5) == pytest.approx(50)
    assert percentile([np.nan], 1) is None


def test_weights_sum_and_rules():
    free, pro = load_settings("free"), load_settings("pro")
    for s in (free, pro):
        for h in ("short", "long"):
            for d in ("fundamental", "technical", "chips"):
                assert sum(indicator_weights(s, h, d).values()) == pytest.approx(1)
    assert "dividend" not in indicator_weights(free, "short", "fundamental")
    assert "dividend" in indicator_weights(free, "long", "fundamental")
    assert "broker_branches" not in indicator_weights(free, "short", "chips")
    assert indicator_weights(pro, "short", "chips")["broker_branches"] == pytest.approx(0.30)
    assert indicator_weights(pro, "long", "chips")["broker_branches"] == pytest.approx(0.10)
    base = indicator_weights(free, "short", "chips")["trust_net"]
    assert indicator_weights(free, "short", "chips", small_cap=True)["trust_net"] > base


def test_rsi_rises_with_uptrend():
    up = pd.Series(np.linspace(10, 20, 50))
    down = pd.Series(np.linspace(20, 10, 50))
    assert rsi(up, 14).iloc[-1] > 90
    assert rsi(down, 14).iloc[-1] < 10


def test_uptrend_beats_downtrend(results):
    up, down = results[("1111", "short")], results[("2222", "short")]
    assert up.status == down.status == "ok"
    assert up.dimensions["technical"] > down.dimensions["technical"]
    assert up.dimensions["chips"] > down.dimensions["chips"]
    assert results[("1111", "long")].dimensions["fundamental"] > results[("2222", "long")].dimensions["fundamental"]
    assert up.total > down.total


def test_breakdown_adds_up(results):
    for r in results.values():
        items = r.breakdown["indicators"]
        assert sum(i["contribution"] for i in items) == pytest.approx(r.total, abs=0.05)
        assert all(0 <= i["score"] <= 100 for i in items if not i.get("locked"))
        locked = {i["key"] for i in items if i.get("locked")}
        assert locked == {"broker_branches", "analyst_revision"}  # 免費模式以鎖頭列出、權重為 0
        for d in ("fundamental", "technical", "chips"):
            assert sum(i["weight"] for i in items if i["dimension"] == d) == pytest.approx(1, abs=1e-3)


def test_missing_data_is_neutral(results):
    items = {i["key"]: i for i in results[("1111", "long")].breakdown["indicators"]}
    # 沒有季報與股權分散資料
    assert items["eps_growth"]["score"] == 50
    assert "資料缺漏" in items["eps_growth"]["note"]
    assert items["major_holders"]["score"] == 50


def test_revenue_streak_and_new_high(results):
    items = {i["key"]: i for i in results[("1111", "long")].breakdown["indicators"]}
    rev = items["revenue_growth"]
    assert rev["streak"] >= 6 and rev["new_high"] is True
    assert any("月營收" in t for t in reasons(results[("1111", "long")].breakdown))


def test_insufficient_history():
    settings = load_settings("free")
    stocks = {"9999": frames("9999", 0.001, n=100)}
    out = score_universe(settings, stocks, {}, AS_OF)
    assert {r.status for r in out} == {"insufficient_data"}
    assert "240" in out[0].breakdown["reason"]


def test_no_trade_today_not_scored():
    settings = load_settings("free")
    s = frames("8888", 0.001)
    s.prices = s.prices.iloc[:-1]
    out = score_universe(settings, {"8888": s}, {}, AS_OF)
    assert out[0].status == "insufficient_data"


def test_fundamental_ignores_unpublished():
    fin = make_financials(0.02)
    cfg = load_settings("free").scoring
    # 2026-10 月營收在 2026-11-10 才公布
    before = fundamental(fin, pd.DataFrame(), date(2026, 11, 9), cfg)["revenue_growth"]
    after = fundamental(fin, pd.DataFrame(), date(2026, 11, 10), cfg)["revenue_growth"]
    assert before.extras["latest_period"] == "2026-09"
    assert after.extras["latest_period"] == "2026-10"
    assert before.history(date(2026, 11, 9)).index.max() < pd.Timestamp("2026-11-10")


def test_valuation_falls_back_to_pb():
    p = make_prices(0.0)
    v = pd.DataFrame({"pe": np.nan, "pb": 1.5, "dividend_yield": 0.0}, index=p.index)
    out = fundamental(pd.DataFrame(), v, AS_OF, load_settings("free").scoring)
    assert out["valuation_position"].extras["basis"] == "股價淨值比"


def test_technical_alignment_range():
    ind = technical(make_prices(0.003), load_settings("free").scoring)
    assert ind["ma_alignment"].current(AS_OF) == 1.0


def test_market_mode_uses_peer_only():
    settings = load_settings("sponsor")
    stocks = {sid: frames(sid, d, seed=i) for i, (sid, d) in enumerate([("1111", 0.002), ("2222", -0.002)])}
    out = score_universe(settings, stocks, {}, AS_OF)
    r = next(x for x in out if x.stock_id == "1111" and x.horizon == "short")
    assert r.breakdown["comparison"] == "market"
    assert all(i["self_percentile"] is None for i in r.breakdown["indicators"])
