"""大盤多空燈號（第 7.2 節）。

每項輸入給 +1 偏多／0 中性／-1 偏空，加總後依門檻判斷三態。
缺資料的輸入以 0 計並註明，不會讓整個燈號失效。
所有輸入只使用 as_of 當天（含）以前已知的資料；美股與匯率以前一個美國交易日為準。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

LIGHT_LABELS = {"bullish": "偏多", "neutral": "中性", "bearish": "偏空"}
INPUT_LABELS = {
    "taiex_vs_ma60": "加權指數相對季線",
    "sox_trend": "費城半導體趨勢",
    "tsmc_adr_premium": "台積電 ADR 溢價",
    "foreign_futures_oi": "外資台指期未平倉",
    "twd_trend": "新台幣匯率",
}


@dataclass
class MarketLight:
    date: date
    light: str
    total: int
    inputs: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return LIGHT_LABELS[self.light]

    def to_inputs_json(self) -> dict:
        return {"total": self.total, **self.inputs}


def _series(points: list[tuple[date, float]] | pd.Series | None) -> pd.Series:
    if points is None:
        return pd.Series(dtype=float)
    if isinstance(points, pd.Series):
        s = points
    else:
        s = pd.Series({pd.Timestamp(d): float(v) for d, v in points}, dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index().dropna()


def _upto(s: pd.Series, as_of: date, strict: bool = False) -> pd.Series:
    if s.empty:
        return s
    ts = pd.Timestamp(as_of)
    return s[s.index < ts] if strict else s[s.index <= ts]


def _result(vote: int, reason: str, value: Any = None, **extra: Any) -> dict:
    return {"vote": vote, "reason": reason, "value": value, **extra}


def _missing(reason: str) -> dict:
    return _result(0, reason, missing=True)


def taiex_vs_ma60(taiex: pd.Series, as_of: date, cfg: dict) -> dict:
    s = _upto(taiex, as_of)
    n = cfg["ma_days"]
    if len(s) < n:
        return _missing(f"加權指數歷史只有 {len(s)} 天，不足 {n} 天")
    close, ma = float(s.iloc[-1]), float(s.iloc[-n:].mean())
    gap = close / ma - 1
    band = cfg["band"]
    vote = 1 if gap > band else -1 if gap < -band else 0
    where = "高於" if gap >= 0 else "低於"
    return _result(vote, f"收盤 {close:,.0f}，{where}季線 {abs(gap):.1%}", round(gap, 4), ma=round(ma, 2))


def sox_trend(sox: pd.Series, as_of: date, cfg: dict) -> dict:
    s = _upto(sox, as_of, strict=True)
    n, k = cfg["ma_days"], cfg["slope_days"]
    if len(s) < n + k:
        return _missing(f"費半歷史只有 {len(s)} 天，不足 {n + k} 天")
    ma = s.rolling(n).mean()
    close, ma_now, ma_prev = float(s.iloc[-1]), float(ma.iloc[-1]), float(ma.iloc[-1 - k])
    rising = ma_now > ma_prev
    vote = 1 if close > ma_now and rising else -1 if close < ma_now and not rising else 0
    pos = "站上" if close > ma_now else "跌破"
    return _result(vote, f"{pos} {n} 日均線，均線{'上揚' if rising else '下彎'}", round(close, 2),
                   as_of=s.index[-1].date().isoformat())


def tsmc_adr_premium(adr: pd.Series, usd_twd: pd.Series, tw2330: pd.Series, as_of: date, cfg: dict) -> dict:
    """溢價 = ADR 價格 × 匯率 ÷ 換股比例 ÷ 台股收盤 − 1；與自身近 N 日平均比較。

    台股收盤日 D 對應前一個美國交易日的 ADR 收盤（D 當天美股尚未開盤）。
    """
    adr = _upto(adr, as_of, strict=True)
    fx = _upto(usd_twd, as_of)
    tw = _upto(tw2330, as_of)
    if adr.empty or fx.empty or tw.empty:
        return _missing("ADR、匯率或台積電收盤資料缺漏")
    premiums = {}
    for d, close in tw.items():
        a = adr[adr.index < d]
        f = fx[fx.index <= d]
        if a.empty or f.empty:
            continue
        premiums[d] = float(a.iloc[-1]) * float(f.iloc[-1]) / cfg["adr_ratio"] / float(close) - 1
    p = pd.Series(premiums, dtype=float).sort_index()
    if p.empty or p.index[-1] != tw.index[-1]:
        return _missing("當日溢價無法計算")
    now = float(p.iloc[-1])
    hist = p.iloc[-cfg["avg_days"] - 1:-1]
    if len(hist) < cfg["min_observations"]:
        return _result(0, f"溢價 {now:.1%}，歷史不足 {cfg['min_observations']} 天無法比較", round(now, 4),
                       missing=True)
    avg = float(hist.mean())
    diff = now - avg
    band = cfg["band"]
    vote = 1 if diff > band else -1 if diff < -band else 0
    return _result(vote, f"溢價 {now:.1%}，近 {len(hist)} 日平均 {avg:.1%}", round(now, 4), average=round(avg, 4))


def foreign_futures_oi(oi: pd.Series, as_of: date, cfg: dict) -> dict:
    s = _upto(oi, as_of)
    if s.empty:
        return _missing("外資台指期未平倉資料缺漏")
    level = float(s.iloc[-1])
    text = f"淨{'多' if level >= 0 else '空'}單 {abs(level):,.0f} 口"
    if level >= cfg["bullish_level"]:
        return _result(1, text, level)
    if level <= cfg["bearish_level"]:
        return _result(-1, text, level)
    k = cfg["change_days"]
    if len(s) <= k:
        return _result(0, f"{text}，歷史不足無法看變化", level)
    change = level - float(s.iloc[-1 - k])
    lots = cfg["change_lots"]
    vote = 1 if change >= lots else -1 if change <= -lots else 0
    return _result(vote, f"{text}，{k} 日變化 {change:+,.0f} 口", level, change=change)


def twd_trend(usd_twd: pd.Series, as_of: date, cfg: dict) -> dict:
    s = _upto(usd_twd, as_of)
    k = cfg["change_days"]
    if len(s) <= k:
        return _missing(f"匯率歷史只有 {len(s)} 天，不足 {k + 1} 天")
    change = float(s.iloc[-1]) / float(s.iloc[-1 - k]) - 1
    band = cfg["band"]
    # 美元兌台幣下跌＝台幣升值，通常代表資金流入
    vote = 1 if change < -band else -1 if change > band else 0
    move = "升值" if change < 0 else "貶值"
    return _result(vote, f"美元兌台幣 {float(s.iloc[-1]):.3f}，{k} 日台幣{move} {abs(change):.1%}",
                   round(float(s.iloc[-1]), 4), change=round(change, 4))


def compute_light(series: dict[str, Any], as_of: date, cfg: dict) -> MarketLight:
    """series 鍵值：taiex、sox、tsm_adr、usd_twd、foreign_tx_oi、tw2330（日期, 值）。"""
    s = {k: _series(v) for k, v in series.items()}
    get = lambda k: s.get(k, pd.Series(dtype=float))  # noqa: E731
    calculators = {
        "taiex_vs_ma60": lambda c: taiex_vs_ma60(get("taiex"), as_of, c),
        "sox_trend": lambda c: sox_trend(get("sox"), as_of, c),
        "tsmc_adr_premium": lambda c: tsmc_adr_premium(get("tsm_adr"), get("usd_twd"), get("tw2330"), as_of, c),
        "foreign_futures_oi": lambda c: foreign_futures_oi(get("foreign_tx_oi"), as_of, c),
        "twd_trend": lambda c: twd_trend(get("usd_twd"), as_of, c),
    }
    inputs: dict[str, dict] = {}
    for key in cfg["inputs"]:
        result = calculators[key](cfg[key])
        result["label"] = INPUT_LABELS[key]
        inputs[key] = result
    total = sum(r["vote"] for r in inputs.values())
    if total >= cfg["bullish_threshold"]:
        light = "bullish"
    elif total <= cfg["bearish_threshold"]:
        light = "bearish"
    else:
        light = "neutral"
    return MarketLight(as_of, light, total, inputs)
