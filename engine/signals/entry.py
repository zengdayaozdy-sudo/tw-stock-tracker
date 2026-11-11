"""進場條件（第 8 節買進條件 3）：觸發任一即可。

- above_ma60：收盤由下往上站上季線（前一日在季線下、今日在季線上）
- ma_golden_cross：短均線由下往上穿越長均線（預設 5／20 日）
  以上兩項可用 trigger_window_days 放寬為「N 日內穿越且今天仍在上方」，
  避免穿越當天剛好不在名單內（分數、燈號、族群）就永遠錯過。
- volume_breakout_20d：收盤突破前 20 日最高價，且成交量 ≥ 20 日均量 × 倍數
- institutional_buy_streak：外資＋投信合計連續 N 日買超

領先型（反彈期間使用，見 engine/signals/regime.py）：
- oversold_reversal：跌深反轉。近 N 日曾低於月線 10% 以上（CMoney 乖離率選股），
  今天收盤站上 5 日線且高於前一日最高價，成交量不低於 20 日均量（XQ「超跌後報復性上漲」的確認 K 棒）
- rs_new_high：相對強弱線先創高。股價÷加權指數創 120 日新高，但股價本身仍低於 120 日高點 5% 以上
  （O'Neil／Minervini：修正期間 RS 線先創高的股票，常是下一波領漲股）
- reclaim_ma20：站回月線。收盤 N 日內由下往上穿越 20 日線且今天仍在線上方，20 日線不再下彎超過門檻
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
    "oversold_reversal": "跌深反轉",
    "rs_new_high": "RS 線先創高",
    "reclaim_ma20": "站回月線",
}
LEADING_TRIGGERS = {"oversold_reversal", "rs_new_high", "reclaim_ma20"}


def _upto(df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    return df[df.index <= pd.Timestamp(as_of)].sort_index()


def _upto_series(s: pd.Series | None, as_of: date) -> pd.Series | None:
    if s is None:
        return None
    return s[s.index <= pd.Timestamp(as_of)].sort_index()


def _crossed_within(a: pd.Series, b: pd.Series, window: int) -> int | None:
    """a 在最近 window 個交易日內由下往上穿越 b，且今天仍在 b 之上；回傳幾天前穿越（0＝今天）。"""
    if len(a) < 2 or pd.isna(b.iloc[-1]) or not a.iloc[-1] > b.iloc[-1]:
        return None
    for ago in range(max(window, 1)):
        i = len(a) - 1 - ago
        if i < 1 or pd.isna(b.iloc[i - 1]):
            return None
        if a.iloc[i - 1] <= b.iloc[i - 1] and a.iloc[i] > b.iloc[i]:
            return ago
        if not a.iloc[i] > b.iloc[i]:
            return None
    return None


def above_ma60(close: pd.Series, window: int = 1) -> dict | None:
    ma = close.rolling(60).mean()
    if len(close) < 61:
        return None
    ago = _crossed_within(close, ma, window)
    if ago is None:
        return None
    out = {"ma60": round(float(ma.iloc[-1]), 2)}
    if ago:
        out["days_ago"] = ago
    return out


def golden_cross(close: pd.Series, fast: int, slow: int, window: int = 1) -> dict | None:
    f, s = close.rolling(fast).mean(), close.rolling(slow).mean()
    if len(close) < slow + 1:
        return None
    ago = _crossed_within(f, s, window)
    if ago is None:
        return None
    out = {f"ma{fast}": round(float(f.iloc[-1]), 2), f"ma{slow}": round(float(s.iloc[-1]), 2)}
    if ago:
        out["days_ago"] = ago
    return out


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


def oversold_reversal(p: pd.DataFrame, cfg: dict) -> dict | None:
    n = cfg["lookback_days"]
    if len(p) < 21 + n:
        return None
    close = p["close"].astype(float)
    ma20 = close.rolling(20).mean()
    bias = (close / ma20 - 1).iloc[-n:]
    worst = float(bias.min())
    if worst > -cfg["bias"]:
        return None
    ma5 = float(close.iloc[-5:].mean())
    c, prev_high = float(close.iloc[-1]), float(p["high"].iloc[-2])
    avg_vol = float(p["volume"].iloc[-21:-1].mean())
    vol = float(p["volume"].iloc[-1])
    if c > ma5 and c > prev_high and avg_vol > 0 and vol >= avg_vol * cfg["volume_ratio"]:
        return {"min_bias": round(worst, 4), "ma5": round(ma5, 2), "volume_ratio": round(vol / avg_vol, 2)}
    return None


def rs_new_high(close: pd.Series, benchmark: pd.Series | None, cfg: dict) -> dict | None:
    if benchmark is None or benchmark.empty:
        return None
    n = cfg["lookback_days"]
    b = benchmark.reindex(close.index).ffill()
    rs = (close / b).dropna()
    if len(rs) < n or rs.index[-1] != close.index[-1]:
        return None
    window_rs = rs.iloc[-n:]
    window_px = close.iloc[-n:]
    high = float(window_px.max())
    gap = float(close.iloc[-1]) / high - 1
    if float(window_rs.iloc[-1]) >= float(window_rs.max()) and gap <= -cfg["price_gap"]:
        return {"price_gap": round(gap, 4), "price_high": round(high, 2)}
    return None


def reclaim_ma20(close: pd.Series, window: int, cfg: dict) -> dict | None:
    if len(close) < 21 + cfg["slope_days"]:
        return None
    ma = close.rolling(20).mean()
    ago = _crossed_within(close, ma, window)
    if ago is None:
        return None
    slope = float(ma.iloc[-1]) / float(ma.iloc[-1 - cfg["slope_days"]]) - 1
    if slope < -cfg["max_ma_decline"]:
        return None
    out = {"ma20": round(float(ma.iloc[-1]), 2), "ma20_slope": round(slope, 4)}
    if ago:
        out["days_ago"] = ago
    return out


def entry_triggers(prices: pd.DataFrame, chips: pd.DataFrame, as_of: date, cfg: dict[str, Any],
                   benchmark: pd.Series | None = None, conditions: list[str] | None = None) -> dict[str, dict]:
    """回傳觸發的條件 {key: 細節}。as_of 當天沒有收盤價時不判斷。

    conditions：要檢查的條件（預設為 cfg["entry_conditions"]）；反彈期間由盤勢模式加入領先型條件。
    """
    p = _upto(prices, as_of).dropna(subset=["close"])
    if p.empty or p.index[-1] != pd.Timestamp(as_of):
        return {}
    close = p["close"].astype(float)
    fast, slow = cfg.get("golden_cross_ma", [5, 20])
    window = int(cfg.get("trigger_window_days", 1))
    checks = {
        "above_ma60": lambda: above_ma60(close, window),
        "ma_golden_cross": lambda: golden_cross(close, fast, slow, window),
        "volume_breakout_20d": lambda: volume_breakout(p, cfg.get("breakout_days", 20), cfg["volume_breakout_ratio"]),
        "institutional_buy_streak": lambda: institutional_streak(chips, as_of, cfg["institutional_buy_streak_days"]),
        "oversold_reversal": lambda: oversold_reversal(p, cfg["oversold_reversal"]),
        "rs_new_high": lambda: rs_new_high(close, _upto_series(benchmark, as_of), cfg["rs_new_high"]),
        "reclaim_ma20": lambda: reclaim_ma20(close, window, cfg["reclaim_ma20"]),
    }
    out: dict[str, dict] = {}
    for key in conditions if conditions is not None else cfg["entry_conditions"]:
        hit = checks[key]()
        if hit is not None:
            out[key] = hit
    return out


def trigger_text(triggers: dict[str, dict]) -> list[str]:
    out = []
    for k, d in triggers.items():
        label = TRIGGER_LABELS.get(k, k)
        if d.get("days_ago"):
            label += f"（{d['days_ago']} 日前）"
        if k == "institutional_buy_streak":
            label = f"法人連 {d['days']} 日買超"
        elif k == "volume_breakout_20d":
            label = f"帶量突破 20 日高點（量 {d['volume_ratio']:.1f} 倍）"
        elif k == "oversold_reversal":
            label = f"跌深反轉（曾低於月線 {abs(d['min_bias']):.0%}，量 {d['volume_ratio']:.1f} 倍）"
        elif k == "rs_new_high":
            label = f"RS 線先創高（股價距高點 {abs(d['price_gap']):.0%}）"
        out.append(label)
    return out
