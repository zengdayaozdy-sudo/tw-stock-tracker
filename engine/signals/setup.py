"""醞釀名單：跌深、但已出現強勢跡象，只差一根轉強 K 棒的股票（第 7.5 節延伸，2026-11-11 擁有者要求）。

不是買進訊號，是讓擁有者隔天盤中盯著看。每檔附上：
- 轉強價：隔天收盤要站上這個價位（今天最高價與 5 日線取高者），才會成立「跌深反轉」
- 建議買進區間：轉強價到轉強價 +chase_pct（不追高）
- 停損價：近 N 日最低點再往下 buffer，與買進區間上緣 −max_stop 取較近者
- 目標價：20 日線（跌深後的第一個均值回歸目標）；離進場太近時改用報酬風險比推算

條件：
1. 近 lookback_days 日內曾低於 20 日線 bias 以上（與 oversold_reversal 相同的跌深門檻）
2. 今天尚未出現跌深反轉，且收盤仍在 20 日線下方（已經彈回月線上方就不算醞釀）
3. 強勢跡象至少 min_signs 項：
   - 抗跌：股價÷加權指數 rs_days 日上升（跌得比大盤少）
   - 法人逆勢買：外資＋投信近 institutional_days 日合計買超
   - 融資減肥：融資餘額近 margin_days 日減少 margin_drop 以上（散戶籌碼清洗）
只使用 as_of 當天（含）以前的資料；門檻在 config/risk.yaml 的 setup_watch。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from engine.signals.levels import round_tick, tick_size

SIGN_LABELS = {"rs_up": "抗跌", "institutional_buy": "法人逆勢買", "margin_drop": "融資減肥"}


def _upto(df, as_of: date):
    if df is None:
        return None
    return df[df.index <= pd.Timestamp(as_of)].sort_index()


def setup_signs(prices: pd.DataFrame, chips: pd.DataFrame | None, benchmark: pd.Series | None, as_of: date,
                cfg: dict[str, Any]) -> dict[str, dict] | None:
    """符合跌深前提時回傳強勢跡象 {key: 細節}（可能為空 dict）；不符合跌深前提回傳 None。"""
    p = _upto(prices, as_of).dropna(subset=["close"])
    n = cfg["lookback_days"]
    if p.empty or p.index[-1] != pd.Timestamp(as_of) or len(p) < 21 + n:
        return None
    close = p["close"].astype(float)
    ma20 = close.rolling(20).mean()
    bias = (close / ma20 - 1).iloc[-n:]
    if float(bias.min()) > -cfg["bias"] or float(close.iloc[-1]) >= float(ma20.iloc[-1]):
        return None
    signs: dict[str, dict] = {}

    b = _upto(benchmark, as_of)
    k = cfg["rs_days"]
    if b is not None and not b.empty:
        rs = (close / b.reindex(close.index).ffill()).dropna()
        if len(rs) > k and rs.index[-1] == close.index[-1]:
            change = float(rs.iloc[-1]) / float(rs.iloc[-1 - k]) - 1
            if change > 0:
                signs["rs_up"] = {"rs_change": round(change, 4), "days": k}

    c = _upto(chips, as_of) if chips is not None and not chips.empty else None
    if c is not None and not c.empty:
        m = cfg["institutional_days"]
        if {"foreign_net", "trust_net"} <= set(c.columns):
            net = (c["foreign_net"].astype(float).fillna(0) + c["trust_net"].astype(float).fillna(0)).iloc[-m:]
            if len(net) == m and float(net.sum()) > 0:
                signs["institutional_buy"] = {"net_shares": int(net.sum()), "days": m}
        if "margin_balance" in c.columns:
            mb = c["margin_balance"].astype(float).dropna()
            j = cfg["margin_days"]
            if len(mb) > j and float(mb.iloc[-1 - j]) > 0:
                drop = float(mb.iloc[-1]) / float(mb.iloc[-1 - j]) - 1
                if drop <= -cfg["margin_drop"]:
                    signs["margin_drop"] = {"change": round(drop, 4), "days": j}
    return signs


def setup_levels(prices: pd.DataFrame, as_of: date, cfg: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
    """轉強價、建議買進區間、停損、目標價。"""
    p = _upto(prices, as_of).dropna(subset=["close"])
    close = p["close"].astype(float)
    high = float(p["high"].iloc[-1])
    ma5 = float(close.iloc[-5:].mean())
    ma20 = float(close.iloc[-20:].mean())
    trigger = round_tick(max(high, ma5) + tick_size(max(high, ma5)), "up")
    entry_high = round_tick(trigger * (1 + cfg["chase_pct"]), "up")
    swing_low = float(p["low"].iloc[-cfg["stop_lookback_days"]:].min())
    stop_swing = swing_low * (1 - cfg["stop_buffer"])
    stop_cap = entry_high * (1 - cfg["max_stop"])
    stop_basis = f"近 {cfg['stop_lookback_days']} 日低點 {swing_low:,.2f} 下方 {cfg['stop_buffer']:.0%}"
    stop = stop_swing
    if stop_swing < stop_cap:
        stop, stop_basis = stop_cap, f"買進上緣 −{cfg['max_stop']:.0%}（低點離太遠）"
    stop = round_tick(stop, "down")
    risk_per_share = entry_high - stop
    min_target = entry_high * (1 + risk["take_profit"]["min_reward_pct"])
    if ma20 >= min_target:
        target, target_basis = ma20, "20 日線（均值回歸）"
    else:
        rr = risk["take_profit"]["reward_risk_ratio"]
        target, target_basis = entry_high + rr * risk_per_share, f"報酬風險比 {rr:g} 倍"
    return {
        "close": round(float(close.iloc[-1]), 2),
        "trigger_price": trigger,
        "entry_low": trigger,
        "entry_high": entry_high,
        "stop_loss": stop,
        "take_profit": round_tick(target, "down"),
        "stop_basis": stop_basis,
        "target_basis": target_basis,
        "ma5": round(ma5, 2),
        "ma20": round(ma20, 2),
        "swing_low": round(swing_low, 2),
        "reward_risk": round((target - entry_high) / risk_per_share, 2) if risk_per_share > 0 else None,
    }


def setup_text(signs: dict[str, dict]) -> list[str]:
    out = []
    for k, d in signs.items():
        if k == "rs_up":
            out.append(f"抗跌（{d['days']} 日相對大盤 +{d['rs_change']:.1%}）")
        elif k == "institutional_buy":
            out.append(f"法人逆勢買（{d['days']} 日 {d['net_shares'] / 1000:+,.0f} 張）")
        elif k == "margin_drop":
            out.append(f"融資減肥（{d['days']} 日 {d['change']:.1%}）")
    return out
