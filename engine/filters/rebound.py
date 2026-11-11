"""大盤反彈偵測（領先型）。

大盤燈號看的是季線、20 日均線、20 日匯率變化，本質是落後指標：急跌後的 V 型反彈，
要漲一大段燈號才會翻多。本模組在燈號翻多之前，用四類領先證據判斷反彈是否成立：

| 證據 | 來源 | 預設點數 |
|---|---|---|
| 追蹤日（Follow-Through Day） | William O'Neil／IBD：反彈第 4 天以後，指數單日大漲且量增 | 2 |
| 寬度衝刺（Breadth Thrust） | Martin Zweig：上漲家數比率 10 日 EMA 由 0.40 以下衝上 0.615 以上 | 2 |
| 急漲反彈（Power Rally） | 台股常見 V 轉：自低點 5 個交易日內漲 6% 以上 | 1 |
| 寬度回升 | StockCharts 等：站上 20 日線比例由 20% 以下回到 50% 以上 | 1 |
| 外資期貨空單回補 | 台指期淨空單自近期最大值回補 1 萬口以上 | 1 |

狀態：
- none：沒有修正（近 60 日回檔不到門檻）、今天創波段新低、或反彈已超過有效天數
- attempt（反彈嘗試）：修正後自低點反彈達門檻，但證據點數不足
- confirmed（反彈確認）：證據點數 ≥ confirm_points

寬度指標以評分範圍內的股票計算（免費模式＝自選股，付費模式＝全市場）。
只使用 as_of 當天（含）以前的資料；所有門檻在 config/filters.yaml 的 rebound 區段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

STATE_LABELS = {"none": "無反彈訊號", "attempt": "反彈嘗試", "confirmed": "反彈確認"}
EVIDENCE_LABELS = {
    "ftd": "追蹤日",
    "power_rally": "急漲反彈",
    "breadth_thrust": "寬度衝刺",
    "breadth_recovery": "寬度回升",
    "short_covering": "外資期貨空單回補",
}


@dataclass
class Rebound:
    date: date
    state: str
    points: int = 0
    rally: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    breadth: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return STATE_LABELS[self.state]

    def to_json(self) -> dict:
        return {"state": self.state, "label": self.label, "points": self.points, "rally": self.rally,
                "evidence": self.evidence, "breadth": self.breadth}


def _upto(s: pd.Series, as_of: date) -> pd.Series:
    if s is None or s.empty:
        return pd.Series(dtype=float)
    return s[s.index <= pd.Timestamp(as_of)].dropna()


# ---------- 寬度 ----------

def breadth_frame(closes: pd.DataFrame, ma_days: int = 20) -> pd.DataFrame:
    """closes：index 日期、欄位股票代號。回傳每日上漲比率與站上均線比例（只依過去資料滾動計算）。"""
    if closes.empty:
        return pd.DataFrame(columns=["advancers", "decliners", "adv_ratio", "pct_above_ma", "count"])
    closes = closes.sort_index()
    ret = closes.pct_change(fill_method=None)
    adv = (ret > 0).sum(axis=1)
    dec = (ret < 0).sum(axis=1)
    ma = closes.rolling(ma_days, min_periods=ma_days).mean()
    valid = ma.notna() & closes.notna()
    above = ((closes > ma) & valid).sum(axis=1)
    count = valid.sum(axis=1)
    ratio = (adv / (adv + dec)).where(adv + dec > 0)
    return pd.DataFrame({
        "advancers": adv,
        "decliners": dec,
        "adv_ratio": ratio,
        "pct_above_ma": (above / count).where(count > 0),
        "count": count,
    })


def breadth_thrust(b: pd.DataFrame, cfg: dict) -> dict | None:
    """最近 recent_days 內完成 Zweig 寬度衝刺時回傳細節。"""
    ratio = b["adv_ratio"].dropna()
    if len(ratio) < cfg["ema_span"] + cfg["within_days"]:
        return None
    z = ratio.ewm(span=cfg["ema_span"], adjust=False).mean()
    recent = cfg["recent_days"]
    start = max(1, len(z) - recent)
    for i in range(len(z) - 1, start - 1, -1):
        if z.iloc[i] <= cfg["high"] or z.iloc[i - 1] > cfg["high"]:
            continue  # 第 i 天要是「由下往上」穿越 0.615 的那一天
        window = z.iloc[max(0, i - cfg["within_days"]):i]
        if (window < cfg["low"]).any():
            low_day = window[window < cfg["low"]].index[-1]
            return {"date": z.index[i].date().isoformat(), "ema": round(float(z.iloc[i]), 3),
                    "washout_date": low_day.date().isoformat(), "ema_now": round(float(z.iloc[-1]), 3)}
    return None


def breadth_recovery(b: pd.DataFrame, cfg: dict) -> dict | None:
    pct = b["pct_above_ma"].dropna()
    if len(pct) < 2:
        return None
    window = pct.iloc[-cfg["lookback_days"]:]
    low = float(window.min())
    now = float(pct.iloc[-1])
    if low <= cfg["oversold"] and now >= cfg["recovered"]:
        return {"low": round(low, 3), "low_date": window.idxmin().date().isoformat(), "now": round(now, 3)}
    return None


# ---------- 指數 ----------

def rally_state(taiex: pd.Series, as_of: date, cfg: dict) -> dict:
    """找近期波段高點後的最低收盤，計算反彈第幾天、自低點漲幅。"""
    s = _upto(taiex, as_of)
    c = cfg["correction"]
    n = c["lookback_days"]
    if len(s) < 10:
        return {"active": False, "reason": f"加權指數歷史只有 {len(s)} 天"}
    window = s.iloc[-n:]
    peak_at = window.idxmax()
    peak = float(window.loc[peak_at])
    after = window[window.index >= peak_at]
    low_at = after.idxmin()
    low = float(after.loc[low_at])
    depth = low / peak - 1
    close = float(s.iloc[-1])
    base = {"peak": round(peak, 2), "peak_date": peak_at.date().isoformat(), "low": round(low, 2),
            "low_date": low_at.date().isoformat(), "depth": round(depth, 4), "close": round(close, 2)}
    if depth > -c["min_depth"]:
        return {**base, "active": False, "reason": f"近 {n} 日最大回檔 {abs(depth):.1%}，未達修正門檻 {c['min_depth']:.0%}"}
    since = s[s.index > low_at]
    day = len(since)  # 反彈第幾天（低點隔天＝第 1 天）
    gain = close / low - 1
    out = {**base, "day": day, "gain": round(gain, 4),
           "retrace": round((close - low) / (peak - low), 4) if peak > low else None}
    if day == 0:
        return {**out, "active": False, "reason": "今天創波段新低"}
    if day > cfg["rally"]["max_days"]:
        return {**out, "active": False, "reason": f"低點後已 {day} 個交易日，超過反彈有效天數"}
    if gain < cfg["rally"]["attempt_min_gain"]:
        return {**out, "active": False, "reason": f"自低點反彈 {gain:.1%}，未達 {cfg['rally']['attempt_min_gain']:.0%}"}
    return {**out, "active": True, "reason": f"自 {base['low_date']} 低點反彈第 {day} 天，漲 {gain:.1%}"}


def follow_through(taiex: pd.Series, volume: pd.Series | None, rally: dict, as_of: date, cfg: dict) -> dict | None:
    """低點後第 min_day～max_day 天內，任一天單日漲幅 ≥ min_gain 且量增（低點沒被跌破，rally_state 已保證）。"""
    s = _upto(taiex, as_of)
    low_at = pd.Timestamp(rally["low_date"])
    since = s[s.index > low_at]
    prev = s.shift(1)
    vol = _upto(volume, as_of) if volume is not None else pd.Series(dtype=float)
    for i, (d, close) in enumerate(since.items(), start=1):
        if i < cfg["min_day"] or i > cfg["max_day"]:
            continue
        change = float(close) / float(prev.loc[d]) - 1
        if change < cfg["min_gain"]:
            continue
        vol_ok = None
        if cfg.get("require_volume_up"):
            if d in vol.index:
                earlier = vol[vol.index < d]
                vol_ok = bool(not earlier.empty and float(vol.loc[d]) > float(earlier.iloc[-1]))
            if vol_ok is False:
                continue
        return {"date": d.date().isoformat(), "day": i, "gain": round(change, 4),
                "volume_up": vol_ok}
    return None


def short_covering(oi: pd.Series, as_of: date, cfg: dict) -> dict | None:
    s = _upto(oi, as_of)
    if len(s) < 2:
        return None
    window = s.iloc[-cfg["lookback_days"] - 1:]
    worst = float(window.min())
    now = float(s.iloc[-1])
    covered = now - worst
    if worst < 0 and covered >= cfg["min_lots"]:
        return {"worst": worst, "worst_date": window.idxmin().date().isoformat(), "now": now, "covered": covered}
    return None


# ---------- 整合 ----------

def compute_rebound(taiex: pd.Series, breadth: pd.DataFrame, volume: pd.Series | None, foreign_oi: pd.Series | None,
                    as_of: date, cfg: dict) -> Rebound:
    """breadth：breadth_frame() 的結果（可為整段歷史，這裡只取 as_of 以前）。"""
    taiex = _upto(taiex, as_of)
    rally = rally_state(taiex, as_of, cfg)
    b = breadth[breadth.index <= pd.Timestamp(as_of)] if not breadth.empty else breadth
    if not b.empty and float(b["count"].iloc[-1] or 0) < cfg["min_stocks"]:
        b = b.iloc[0:0]
    bsum = {}
    if not b.empty:
        last = b.iloc[-1]
        bsum = {"adv_ratio": None if pd.isna(last["adv_ratio"]) else round(float(last["adv_ratio"]), 3),
                "pct_above_ma": None if pd.isna(last["pct_above_ma"]) else round(float(last["pct_above_ma"]), 3),
                "count": int(last["count"])}
    out = Rebound(as_of, "none", rally=rally, breadth=bsum)
    if not rally.get("active"):
        return out

    found: dict[str, dict | None] = {}
    found["ftd"] = follow_through(taiex, volume, rally, as_of, cfg["ftd"])
    pr = cfg["power_rally"]
    found["power_rally"] = ({"days": rally["day"], "gain": rally["gain"]}
                            if rally["day"] <= pr["max_days"] and rally["gain"] >= pr["min_gain"] else None)
    if not b.empty:
        found["breadth_thrust"] = breadth_thrust(b, cfg["breadth_thrust"])
        found["breadth_recovery"] = breadth_recovery(b, cfg["breadth_recovery"])
    if foreign_oi is not None:
        found["short_covering"] = short_covering(foreign_oi, as_of, cfg["short_covering"])

    for key, hit in found.items():
        if hit is not None:
            out.evidence[key] = {**hit, "label": EVIDENCE_LABELS[key], "points": cfg[key]["points"]}
    out.points = sum(e["points"] for e in out.evidence.values())
    out.state = "confirmed" if out.points >= cfg["confirm_points"] else "attempt"
    return out


def evidence_text(rb: dict | None) -> list[str]:
    """把證據轉成報告與網站用的短句。"""
    if not rb:
        return []
    out = []
    for key, e in (rb.get("evidence") or {}).items():
        if key == "ftd":
            out.append(f"追蹤日 {e['date']}（反彈第 {e['day']} 天，漲 {e['gain']:.1%}）")
        elif key == "power_rally":
            out.append(f"急漲反彈（{e['days']} 天漲 {e['gain']:.1%}）")
        elif key == "breadth_thrust":
            out.append(f"寬度衝刺 {e['date']}（上漲比率 EMA {e['ema']:.2f}）")
        elif key == "breadth_recovery":
            out.append(f"寬度回升（站上 20 日線 {e['low']:.0%} → {e['now']:.0%}）")
        elif key == "short_covering":
            out.append(f"外資期貨空單回補 {e['covered']:,.0f} 口")
    return out
