"""三面向指標的原始值計算。

每個指標都回傳完整的歷史序列（index 為日期），方便和自身歷史比較百分位。
基本面序列以「公布日」為索引，計算前先剔除 as_of 之後才公布的資料，避免偷看未來。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

DAILY, MONTHLY, QUARTERLY = "daily", "monthly", "quarterly"

# FinMind 財報欄位名稱（type）候選，依序嘗試；實際名稱以 probe 確認
METRIC_KEYS = {
    "eps": ("EPS",),
    "revenue": ("Revenue", "OperatingRevenue"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncome",),
    "net_income": ("IncomeAfterTaxes", "EquityAttributableToOwnersOfParent", "TotalConsolidatedProfitForThePeriod"),
    "equity": ("Equity", "EquityAttributableToOwnersOfParent_per", "TotalEquity"),
    "liabilities": ("Liabilities", "TotalLiabilities"),
    "assets": ("TotalAssets",),
}


@dataclass
class Indicator:
    series: pd.Series
    freq: str
    extras: dict[str, Any] = field(default_factory=dict)

    def history(self, as_of: date) -> pd.Series:
        s = self.series.replace([np.inf, -np.inf], np.nan).dropna()
        return s[s.index <= pd.Timestamp(as_of)]

    def current(self, as_of: date, max_stale_days: int | None = None) -> float | None:
        s = self.history(as_of)
        if s.empty:
            return None
        if max_stale_days is not None and (pd.Timestamp(as_of) - s.index[-1]).days > max_stale_days:
            return None
        return float(s.iloc[-1])


def _empty(freq: str) -> Indicator:
    return Indicator(pd.Series(dtype=float, index=pd.DatetimeIndex([])), freq)


# ---------- 技術面 ----------

def rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + gain / loss)
    out = out.where(loss != 0, np.where(gain > 0, 100.0, 50.0))
    return out.where(gain.notna())


def kd_k(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> pd.Series:
    lo, hi = low.rolling(n, min_periods=n).min(), high.rolling(n, min_periods=n).max()
    rsv = ((close - lo) / (hi - lo)).where(hi > lo, 0.5) * 100
    return rsv.ewm(alpha=1 / 3, adjust=False).mean().where(lo.notna())


def macd_hist(close: pd.Series, fast: int, slow: int, signal: int) -> pd.Series:
    macd = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    hist = macd - macd.ewm(span=signal, adjust=False).mean()
    return (hist / close * 100).where(close.expanding().count() >= slow)


def technical(prices: pd.DataFrame, cfg: dict) -> dict[str, Indicator]:
    if prices.empty:
        return {k: _empty(DAILY) for k in ("ma_alignment", "momentum", "volume_ratio", "breakout")}
    p = prices.sort_index()
    close, high, low = p["close"], p["high"].fillna(p["close"]), p["low"].fillna(p["close"])
    volume = p["volume"].fillna(0)
    params = cfg["params"]

    ma = {n: close.rolling(n, min_periods=n).mean() for n in (5, 20, 60, 240)}
    conditions = pd.concat(
        [close > ma[60], close > ma[240], ma[5] > ma[20], ma[20] > ma[60], ma[60] > ma[240]], axis=1
    )
    alignment = conditions.mean(axis=1).where(ma[240].notna())

    kind = cfg["momentum_indicator"]
    if kind == "kd":
        momentum = kd_k(high, low, close, params["kd_period"])
    elif kind == "macd":
        momentum = macd_hist(close, *params["macd"])
    else:
        momentum = rsi(close, params["rsi_period"])

    # 帶量上漲為正、帶量下跌為負
    avg20 = volume.shift(1).rolling(20, min_periods=20).mean()
    ratio = (volume / avg20).where(avg20 > 0)
    volume_signal = (ratio - 1) * np.sign(close.diff())

    # 相對前 20 日高低區間的位置：>1 為突破、<0 為跌破
    hi20 = high.shift(1).rolling(20, min_periods=20).max()
    lo20 = low.shift(1).rolling(20, min_periods=20).min()
    breakout = ((close - lo20) / (hi20 - lo20)).where(hi20 > lo20)

    return {
        "ma_alignment": Indicator(alignment, DAILY, {"ma60": ma[60], "ma240": ma[240]}),
        "momentum": Indicator(momentum, DAILY, {"kind": kind}),
        "volume_ratio": Indicator(volume_signal, DAILY),
        "breakout": Indicator(breakout, DAILY),
    }


# ---------- 籌碼面 ----------

def chips(chips_df: pd.DataFrame, prices: pd.DataFrame, cfg: dict) -> dict[str, Indicator]:
    keys = ("foreign_net", "trust_net", "foreign_holding_change", "margin", "major_holders", "broker_branches")
    if prices.empty or chips_df.empty:
        return {k: _empty(DAILY) for k in keys}
    params = cfg["params"]
    c = chips_df.sort_index().reindex(prices.index)
    avg_volume = prices["volume"].rolling(20, min_periods=10).mean()
    n = params["chips_net_days"]

    def net(col: str) -> pd.Series:
        s = c[col].rolling(n, min_periods=max(1, n - 2)).sum()
        return (s / avg_volume).where(avg_volume > 0)

    holding = c["foreign_holding_pct"].ffill(limit=10)
    margin = c["margin_balance"].ffill(limit=3)
    major = c["major_holder_pct"].ffill(limit=10)
    m = params["margin_change_days"]
    branches = c["branches"].map(lambda b: (b or {}).get("concentration") if isinstance(b, dict) else np.nan)

    return {
        "foreign_net": Indicator(net("foreign_net"), DAILY),
        "trust_net": Indicator(net("trust_net"), DAILY),
        "foreign_holding_change": Indicator(holding - holding.shift(params["holding_change_days"]), DAILY),
        "margin": Indicator((margin / margin.shift(m) - 1).where(margin.shift(m) > 0), DAILY),
        "major_holders": Indicator(major - major.shift(20), DAILY),
        "broker_branches": Indicator(pd.to_numeric(branches, errors="coerce"), DAILY),
    }


# ---------- 基本面 ----------

def _metric(metrics: dict, name: str) -> float | None:
    for key in METRIC_KEYS[name]:
        v = metrics.get(key)
        if v is not None and not (isinstance(v, float) and np.isnan(v)):
            return float(v)
    return None


def _shift_period(period: str, years: int) -> str:
    if "Q" in period:
        y, q = period.split("Q")
        return f"{int(y) + years}Q{q}"
    y, m = period.split("-")
    return f"{int(y) + years}-{m}"


def _by_publish(values: dict[str, float], published: dict[str, pd.Timestamp]) -> pd.Series:
    items = [(published[p], v) for p, v in sorted(values.items()) if v is not None and np.isfinite(v)]
    s = pd.Series([v for _, v in items], index=pd.DatetimeIndex([d for d, _ in items]), dtype=float)
    return s[~s.index.duplicated(keep="last")].sort_index()


def _growth(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


def fundamental(financials: pd.DataFrame, valuations: pd.DataFrame, as_of: date, cfg: dict) -> dict[str, Indicator]:
    out: dict[str, Indicator] = {
        k: _empty(QUARTERLY) for k in ("eps_growth", "margin_trend", "roe", "balance_sheet")
    }
    out["revenue_growth"] = _empty(MONTHLY)

    fin = financials
    if not fin.empty:
        fin = fin[pd.to_datetime(fin["published_date"]) <= pd.Timestamp(as_of)]  # 不偷看未來

    # 月營收
    months = fin[fin["period_type"] == "month"] if not fin.empty else fin
    if not months.empty:
        rev = {r.period: (r.metrics or {}).get("revenue") for r in months.itertuples()}
        rev = {p: float(v) for p, v in rev.items() if v is not None}
        pub = {r.period: pd.Timestamp(r.published_date) for r in months.itertuples()}
        periods = sorted(rev)
        yoy = {p: _growth(rev[p], rev.get(_shift_period(p, -1))) for p in periods}
        yoy_list = [(p, yoy[p]) for p in periods if yoy[p] is not None]
        yoy3 = {}
        for i, (p, _) in enumerate(yoy_list):
            window = [v for _, v in yoy_list[max(0, i - 2): i + 1]]
            if len(window) >= 2:
                yoy3[p] = float(np.mean(window))
        streak = 0
        for p in reversed(periods):
            if yoy.get(p) is not None and yoy[p] > 0:
                streak += 1
            else:
                break
        lookback = cfg["revenue_components"]["new_high_lookback_months"]
        prior = [rev[p] for p in periods[-lookback - 1: -1]]
        new_high = bool(periods) and len(prior) >= lookback and rev[periods[-1]] >= max(prior)
        out["revenue_growth"] = Indicator(
            _by_publish(yoy3, pub), MONTHLY,
            {"streak": streak, "new_high": new_high, "latest_period": periods[-1] if periods else None},
        )

    # 季報
    quarters = fin[fin["period_type"] == "quarter"] if not fin.empty else fin
    if not quarters.empty:
        q = {r.period: (r.metrics or {}) for r in quarters.itertuples()}
        pub = {r.period: pd.Timestamp(r.published_date) for r in quarters.itertuples()}
        periods = sorted(q)
        get = {name: {p: _metric(q[p], name) for p in periods} for name in METRIC_KEYS}

        def ratio(num: str, den: str, p: str) -> float | None:
            a, b = get[num][p], get[den][p]
            return None if a is None or not b else a / b * 100

        eps_growth, margin_trend, roe, debt = {}, {}, {}, {}
        for i, p in enumerate(periods):
            py = _shift_period(p, -1)
            eps_growth[p] = _growth(get["eps"][p], get["eps"].get(py))
            if py in q:
                parts = [
                    (ratio(a, "revenue", p), ratio(a, "revenue", py)) for a in ("gross_profit", "operating_income")
                ]
                diffs = [x - y for x, y in parts if x is not None and y is not None]
                margin_trend[p] = sum(diffs) if diffs else None
            last4 = periods[max(0, i - 3): i + 1]
            incomes = [get["net_income"][x] for x in last4]
            if len(last4) == 4 and None not in incomes and get["equity"][p]:
                roe[p] = sum(incomes) / get["equity"][p] * 100
            debt[p] = ratio("liabilities", "assets", p)
        out["eps_growth"] = Indicator(_by_publish(eps_growth, pub), QUARTERLY)
        out["margin_trend"] = Indicator(_by_publish(margin_trend, pub), QUARTERLY)
        out["roe"] = Indicator(_by_publish(roe, pub), QUARTERLY)
        out["balance_sheet"] = Indicator(_by_publish(debt, pub), QUARTERLY)

    # 估值（每日）：本益比為負或缺值時改用股價淨值比
    if valuations.empty:
        out["valuation_position"] = _empty(DAILY)
        out["dividend"] = _empty(DAILY)
    else:
        v = valuations.sort_index()
        pe = v["pe"].where(v["pe"] > 0)
        val = Indicator(pe, DAILY, {"basis": "本益比"})
        if val.current(as_of, cfg["max_stale_days"][DAILY]) is None:
            val = Indicator(v["pb"].where(v["pb"] > 0), DAILY, {"basis": "股價淨值比"})
        out["valuation_position"] = val
        out["dividend"] = Indicator(v["dividend_yield"], DAILY)
    return out


def compute_all(stock: StockFrames, as_of: date, cfg: dict) -> dict[str, Indicator]:
    result: dict[str, Indicator] = {}
    result.update(technical(stock.prices, cfg))
    result.update(chips(stock.chips, stock.prices, cfg))
    result.update(fundamental(stock.financials, stock.valuations, as_of, cfg))
    return result


@dataclass
class StockFrames:
    stock_id: str
    prices: pd.DataFrame       # index 日期；open high low close volume
    chips: pd.DataFrame        # index 日期；chips 資料表欄位
    valuations: pd.DataFrame   # index 日期；pe pb dividend_yield
    financials: pd.DataFrame   # period_type period published_date metrics
    industry: str | None = None
    market_cap: float | None = None
