"""還原股價：依除權息、分割、減資的參考價，把除權息日以前的價格乘上調整係數。

成交量不調整。最新一天的價格不變（訊號的參考價位仍是實際可下單的價格），歷史價格向後還原，
均線、突破、報酬計算不會被除權息缺口誤判為下跌。
"""

from __future__ import annotations

from datetime import date

import pandas as pd

PRICE_COLS = ("open", "high", "low", "close")


def cumulative_factors(index: pd.DatetimeIndex, adjustments: list[tuple[date, float]]) -> pd.Series:
    """每個日期的累積係數：該日之後（不含當天以前已發生者）所有除權息係數相乘。"""
    factors = pd.Series(1.0, index=index)
    for d, f in adjustments:
        factors[index < pd.Timestamp(d)] *= f
    return factors


def adjust_prices(prices: pd.DataFrame, adjustments: list[tuple[date, float]] | None) -> pd.DataFrame:
    if prices.empty or not adjustments:
        return prices
    factors = cumulative_factors(prices.index, adjustments)
    out = prices.copy()
    for col in PRICE_COLS:
        if col in out:
            out[col] = out[col] * factors
    return out  # 成交量維持原始股數（除息係數接近 1，分割極少見）


def adjust_points(points: list[tuple[date, float]], adjustments: list[tuple[date, float]] | None):
    """時間序列（例如 0050 收盤）還原成含息報酬。"""
    if not points or not adjustments:
        return points
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d, _ in points])
    factors = cumulative_factors(idx, adjustments)
    return [(d, v * float(f)) for (d, v), f in zip(points, factors, strict=True)]
