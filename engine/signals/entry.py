"""進場條件（第 8 節買進條件 3）：觸發任一即可。

- above_ma60：收盤由下往上站上季線（前一日在季線下、今日在季線上）
- ma_golden_cross：短均線由下往上穿越長均線（預設 5／20 日）
- volume_breakout_20d：收盤突破前 20 日最高價，且成交量 ≥ 20 日均量 × 倍數
- institutional_buy_streak：外資＋投信合計連續 N 日買超
只使用 as_of 當天（含）以前的資料。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

TRIGGER_LABELS = {
    "above_ma60": "站上季線",
    "ma_golden_cross": "均線黃金交叉",
    "volume_breakout_20d": "帶量突破 20 日高點",
    "institutional_buy_streak": "法人連續買超",
}


def _upto(df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    return df[df.index <= pd.Timestamp(as_of)].sort_index()


def above_ma60(close: pd.Series) -> dict | None:
    ma = close.rolling(60).mean()
    if len(close) < 61 or pd.isna(ma.iloc[-2]):
        return None
    if close.iloc[-2] <= ma.iloc[-2] and close.iloc[-1] > ma.iloc[-1]:
        return {"ma60": round(float(ma.iloc[-1]), 2)}
    return None


def golden_cross(close: pd.Series, fast: int, slow: int) -> dict | None:
    f, s = close.rolling(fast).mean(), close.rolling(slow).mean()
    if len(close) < slow + 1 or pd.isna(s.iloc[-2]):
        return None
    if f.iloc[-2] <= s.iloc[-2] and f.iloc[-1] > s.iloc[-1]:
        return {f"ma{fast}": round(float(f.iloc[-1]), 2), f"ma{slow}": round(float(s.iloc[-1]), 2)}
    return None


def volume_breakout(p: pd.DataFrame, days: int, ratio: float) -> dict | None:
    if len(p) < days + 1:
        return None
    prior = p.iloc[-days - 1:-1]
    high = float(prior["high"].max())
    avg_vol = float(prior["volume"].mean())
    close, vol = float(p["close"].iloc[-1]), float(p["volume"].iloc[-1])
    if avg_vol > 0 and close > high and vol >= avg_vol * ratio:
        return {"prior_high": round(high, 2), "volume_ratio": round(vol / avg_vol, 2)}
    return None


def institutional_streak(chips: pd.DataFrame, as_of: date, days: int) -> dict | None:
    if chips.empty:
        return None
    c = _upto(chips, as_of)
    if c.empty or c.index[-1] != pd.Timestamp(as_of):
        return None  # 當日法人資料缺漏不判斷
    net = c["foreign_net"].fillna(0).astype(float) + c["trust_net"].fillna(0).astype(float)
    tail = net.iloc[-days:]
    if len(tail) == days and (tail > 0).all():
        return {"days": days, "net_shares": int(tail.sum())}
    return None


def entry_triggers(prices: pd.DataFrame, chips: pd.DataFrame, as_of: date, cfg: dict[str, Any]) -> dict[str, dict]:
    """回傳觸發的條件 {key: 細節}。as_of 當天沒有收盤價時不判斷。"""
    p = _upto(prices, as_of).dropna(subset=["close"])
    if p.empty or p.index[-1] != pd.Timestamp(as_of):
        return {}
    close = p["close"].astype(float)
    fast, slow = cfg.get("golden_cross_ma", [5, 20])
    checks = {
        "above_ma60": lambda: above_ma60(close),
        "ma_golden_cross": lambda: golden_cross(close, fast, slow),
        "volume_breakout_20d": lambda: volume_breakout(p, cfg.get("breakout_days", 20), cfg["volume_breakout_ratio"]),
        "institutional_buy_streak": lambda: institutional_streak(chips, as_of, cfg["institutional_buy_streak_days"]),
    }
    out: dict[str, dict] = {}
    for key in cfg["entry_conditions"]:
        hit = checks[key]()
        if hit is not None:
            out[key] = hit
    return out


def trigger_text(triggers: dict[str, dict]) -> list[str]:
    out = []
    for k, d in triggers.items():
        label = TRIGGER_LABELS.get(k, k)
        if k == "institutional_buy_streak":
            label = f"法人連 {d['days']} 日買超"
        elif k == "volume_breakout_20d":
            label = f"帶量突破 20 日高點（量 {d['volume_ratio']:.1f} 倍）"
        out.append(label)
    return out
