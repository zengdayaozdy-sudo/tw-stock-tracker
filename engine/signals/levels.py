"""參考價位（第 8 節）：建議買進區間、停損價、停利目標。

- 買進區間：下緣取最近的支撐（月線、季線、近 20 日低點、本益比河流圖偏低區），上緣為收盤價 +chase_pct
- 停損：進場參考價 −8%（大盤偏空時收緊），或跌破季線（設定檔切換）
- 停利：前波壓力或本益比河流圖偏高區中較近者；都沒有時用報酬風險比推算
所有價格依證交所升降單位取整。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

# 證交所／櫃買股票升降單位（交易所規則，非可調參數）
TICKS = [(10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1.0), (math.inf, 5.0)]


def tick_size(price: float) -> float:
    for upper, tick in TICKS:
        if price < upper:
            return tick
    return TICKS[-1][1]


def round_tick(price: float, direction: str = "nearest") -> float:
    """依升降單位取整；down 往下、up 往上。"""
    tick = tick_size(price)
    n = price / tick
    n = math.floor(n + 1e-9) if direction == "down" else math.ceil(n - 1e-9) if direction == "up" else round(n)
    return round(n * tick, 2)


@dataclass
class Levels:
    close: float
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit: float
    detail: dict[str, Any] = field(default_factory=dict)


def pe_river(close: float, valuations: pd.DataFrame, as_of: date, cfg: dict) -> dict | None:
    """以目前本益比反推 EPS，乘上自身近 N 年本益比分位，得到偏低／偏高區價位。"""
    if valuations is None or valuations.empty or "pe" not in valuations:
        return None
    pe = valuations["pe"].astype(float)
    start = pd.Timestamp(as_of) - pd.DateOffset(years=cfg["lookback_years"])
    pe = pe[(pe.index <= pd.Timestamp(as_of)) & (pe.index > start)]
    pe = pe[pe > 0].dropna()
    if len(pe) < cfg["min_observations"] or pe.index[-1] < pd.Timestamp(as_of) - pd.Timedelta(days=7):
        return None
    eps = close / float(pe.iloc[-1])
    low_pe, high_pe = float(pe.quantile(cfg["low_pct"])), float(pe.quantile(cfg["high_pct"]))
    return {"pe": round(float(pe.iloc[-1]), 2), "low_pe": round(low_pe, 2), "high_pe": round(high_pe, 2),
            "low_price": round(eps * low_pe, 2), "high_price": round(eps * high_pe, 2)}


def stop_price(close: float, ma60: float | None, cfg: dict, bearish: bool = False) -> tuple[float, str]:
    pct = cfg["bearish_market_percent"] if bearish else cfg["percent"]
    percent_stop = close * (1 - pct)
    if cfg["method"] == "ma60" and ma60 and ma60 < close:
        stop = ma60 * (1 - cfg.get("ma60_buffer", 0))
        if 1 - stop / close <= cfg.get("max_percent", 1):
            return round_tick(stop, "down"), "跌破季線"
    label = f"進場價 −{pct:.0%}" + ("（大盤偏空收緊）" if bearish else "")
    return round_tick(percent_stop, "down"), label


def compute_levels(prices: pd.DataFrame, valuations: pd.DataFrame | None, as_of: date, risk: dict,
                   bearish: bool = False) -> Levels | None:
    p = prices[prices.index <= pd.Timestamp(as_of)].dropna(subset=["close"]).sort_index()
    if p.empty or p.index[-1] != pd.Timestamp(as_of):
        return None
    close = float(p["close"].iloc[-1])
    ez, tp = risk["entry_zone"], risk["take_profit"]
    ma20 = float(p["close"].iloc[-20:].mean()) if len(p) >= 20 else None
    ma60 = float(p["close"].iloc[-60:].mean()) if len(p) >= 60 else None
    low_n = float(p["low"].iloc[-ez["support_lookback_days"]:].min())
    river = pe_river(close, valuations, as_of, risk["pe_river"])

    supports = {"月線": ma20, "季線": ma60, f"近 {ez['support_lookback_days']} 日低點": low_n,
                "本益比河流圖偏低區": river["low_price"] if river else None}
    floor = close * (1 - ez["max_pullback"])
    valid = {k: v for k, v in supports.items() if v is not None and floor <= v < close}
    if valid:
        basis, low = max(valid.items(), key=lambda kv: kv[1])
    else:
        basis, low = f"收盤價 −{ez['default_pullback']:.0%}", close * (1 - ez["default_pullback"])
    entry_low = round_tick(low, "down")
    entry_high = round_tick(close * (1 + ez["chase_pct"]), "up")

    stop, stop_basis = stop_price(close, ma60, risk["stop_loss"], bearish)
    stop = min(stop, round_tick(entry_low - tick_size(entry_low), "down"))  # 停損一定低於買進區間

    lookback = p.iloc[-tp["resistance_lookback_days"] - 1:-1]
    resistance = float(lookback["high"].max()) if not lookback.empty else None
    targets = {"前波壓力": resistance, "本益比河流圖偏高區": river["high_price"] if river else None}
    min_target = close * (1 + tp["min_reward_pct"])
    valid_t = {k: v for k, v in targets.items() if v is not None and v >= min_target}
    if valid_t:
        target_basis, target = min(valid_t.items(), key=lambda kv: kv[1])
    else:
        target_basis = f"報酬風險比 {tp['reward_risk_ratio']:g} 倍"
        target = close + tp["reward_risk_ratio"] * (close - stop)
    take_profit = round_tick(target, "down")

    return Levels(close, entry_low, entry_high, stop, take_profit, {
        "entry_basis": basis,
        "stop_basis": stop_basis,
        "target_basis": target_basis,
        "ma20": _r(ma20), "ma60": _r(ma60), "support_low": _r(low_n), "resistance": _r(resistance),
        "pe_river": river,
        "trailing_percent": tp["trailing_percent"],
    })


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 2)
