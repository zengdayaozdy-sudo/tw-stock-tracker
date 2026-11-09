"""評分模型：指標百分位 → 面向分數 → 短線／中長線總分，並產生評分拆解。

免費模式：指標分數 = 「與自身近 3 年歷史比較」與「與自選股同族群比較」的百分位平均。
付費模式（market_percentile）：指標分數 = 全市場同產業、同市值組的百分位。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from engine.config import Settings
from engine.scoring.indicators import DAILY, Indicator, StockFrames, compute_all

HORIZONS = ("short", "long")
DIMENSIONS = ("fundamental", "technical", "chips")
DIMENSION_LABELS = {"fundamental": "基本面", "technical": "技術面", "chips": "籌碼面"}

# 指標：(面向, 方向 +1 越高越好／-1 越低越好, 計分方式, 中文名稱)
SPEC: dict[str, tuple[str, int, str, str]] = {
    "revenue_growth": ("fundamental", 1, "revenue", "月營收成長"),
    "eps_growth": ("fundamental", 1, "percentile", "EPS 年增率"),
    "margin_trend": ("fundamental", 1, "percentile", "毛利率與營益率改善"),
    "roe": ("fundamental", 1, "percentile", "ROE"),
    "valuation_position": ("fundamental", -1, "percentile", "估值位置"),
    "balance_sheet": ("fundamental", -1, "percentile", "負債比"),
    "dividend": ("fundamental", 1, "percentile", "殖利率"),
    "ma_alignment": ("technical", 1, "absolute", "均線多頭排列"),
    "momentum": ("technical", 1, "percentile", "動能"),
    "volume_ratio": ("technical", 1, "percentile", "量能"),
    "breakout": ("technical", 1, "percentile", "突破 20 日區間"),
    "foreign_net": ("chips", 1, "percentile", "外資買超"),
    "trust_net": ("chips", 1, "percentile", "投信買超"),
    "foreign_holding_change": ("chips", 1, "percentile", "外資持股增加"),
    "margin": ("chips", -1, "percentile", "融資減少"),
    "major_holders": ("chips", 1, "percentile", "大戶持股增加"),
    "broker_branches": ("chips", 1, "percentile", "主力分點集中"),
}


@dataclass
class ScoreResult:
    stock_id: str
    date: date
    horizon: str
    status: str = "ok"
    total: float | None = None
    dimensions: dict[str, float] = field(default_factory=dict)
    breakdown: dict[str, Any] = field(default_factory=dict)

    def row(self) -> dict:
        return {
            "stock_id": self.stock_id,
            "date": self.date,
            "horizon": self.horizon,
            "total": self.total,
            "fundamental": self.dimensions.get("fundamental"),
            "technical": self.dimensions.get("technical"),
            "chips": self.dimensions.get("chips"),
            "breakdown": self.breakdown,
            "status": self.status,
        }


# ---------- 權重 ----------

def indicator_weights(settings: Settings, horizon: str, dimension: str, small_cap: bool = False) -> dict[str, float]:
    cfg = settings.scoring
    w = dict(cfg["indicators"][dimension])
    if horizon == "short":
        for k in cfg.get("long_only_indicators", []):
            w.pop(k, None)
    for key, target in (cfg.get("horizon_overrides", {}).get(horizon, {}).get(dimension) or {}).items():
        if key in w:
            others = sum(v for k, v in w.items() if k != key)
            scale = (1 - target) / others if others else 0
            w = {k: (target if k == key else v * scale) for k, v in w.items()}
    if dimension == "chips" and not settings.enabled("broker_branches") and "broker_branches" in w:
        freed = w.pop("broker_branches")  # 停用時平均分給其他籌碼指標
        w = {k: v + freed / len(w) for k, v in w.items()}
    if dimension == "chips" and small_cap and "trust_net" in w:
        w["trust_net"] *= cfg["small_cap"]["trust_weight_boost"]
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}


# ---------- 百分位 ----------

def percentile(values: np.ndarray | list[float], x: float) -> float | None:
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    if v.size == 0 or x is None or math.isnan(x):
        return None
    return float(((v < x).sum() + 0.5 * (v == x).sum()) / v.size * 100)


def self_percentile(ind: Indicator, as_of: date, cfg: dict) -> float | None:
    hist = ind.history(as_of)
    hist = hist[hist.index > pd.Timestamp(as_of) - pd.DateOffset(years=cfg["lookback_years"])]
    if len(hist) < cfg["min_observations"][ind.freq]:
        return None
    return percentile(hist.values, float(hist.iloc[-1]))


# ---------- 比較對象 ----------

def peer_sets(
    settings: Settings, stocks: dict[str, StockFrames], groups: dict[str, tuple[str, set[str]]]
) -> dict[str, set[str]]:
    """每檔股票的比較對象（含自己）。"""
    ids = set(stocks)
    cfg = settings.scoring
    out: dict[str, set[str]] = {}
    if settings.enabled("market_percentile"):
        caps = sorted(((s.market_cap or 0, sid) for sid, s in stocks.items()), reverse=True)
        large = {sid for _, sid in caps[: cfg["large_cap_top_n"]] if stocks[sid].market_cap}
        by_bucket_industry: dict[tuple[bool, str | None], set[str]] = {}
        by_bucket: dict[bool, set[str]] = {}
        for sid, s in stocks.items():
            b = sid in large
            by_bucket_industry.setdefault((b, s.industry), set()).add(sid)
            by_bucket.setdefault(b, set()).add(sid)
        for sid, s in stocks.items():
            b = sid in large
            same = by_bucket_industry[(b, s.industry)]
            out[sid] = same if s.industry and len(same) >= cfg["min_industry_size"] else by_bucket[b]
        return out
    for sid, s in stocks.items():
        custom = set().union(*[m for kind, m in groups.values() if kind == "custom" and sid in m] or [set()])
        peers = (custom & ids) | {sid}
        if len(peers) < cfg["min_peers"]:
            industry = set().union(*[m for kind, m in groups.values() if kind == "industry" and sid in m] or [set()])
            same_industry = {x for x, st in stocks.items() if s.industry and st.industry == s.industry}
            peers = ((industry | same_industry) & ids) | {sid}
        out[sid] = peers if len(peers) >= cfg["min_peers"] else {sid}
    return out


# ---------- 主流程 ----------

def _clean(x: Any) -> Any:
    if isinstance(x, (np.floating, float)):
        return None if math.isnan(float(x)) else round(float(x), 4)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def score_universe(settings: Settings, stocks: dict[str, StockFrames],
                   groups: dict[str, tuple[str, set[str]]], as_of: date,
                   indicator_fn: Callable[[StockFrames, date, dict], dict[str, Indicator]] = compute_all,
                   ) -> list[ScoreResult]:
    """indicator_fn：預設每次重算；回測可傳入快取版本（技術面、籌碼面只算一次）。"""
    cfg = settings.scoring
    market_mode = settings.enabled("market_percentile")
    neutral = cfg["missing_value_score"]

    # 1. 指標原始值
    indicators: dict[str, dict[str, Indicator]] = {}
    current: dict[str, dict[str, float | None]] = {}
    insufficient: dict[str, str] = {}
    for sid, s in stocks.items():
        p = s.prices[s.prices.index <= pd.Timestamp(as_of)]
        if len(p.dropna(subset=["close"])) < cfg["min_history_days"]:
            insufficient[sid] = f"股價歷史 {len(p)} 天，少於 {cfg['min_history_days']} 天"
            continue
        if p.index[-1] != pd.Timestamp(as_of):
            insufficient[sid] = "當日沒有股價資料"
            continue
        ind = indicator_fn(s, as_of, cfg)
        indicators[sid] = ind
        current[sid] = {k: i.current(as_of, cfg["max_stale_days"][i.freq]) for k, i in ind.items()}

    peers = peer_sets(settings, {k: v for k, v in stocks.items() if k in indicators}, groups)

    # 2. 指標分數
    scored: dict[str, dict[str, dict]] = {}
    for sid, ind in indicators.items():
        scored[sid] = {}
        for key, (_dim, direction, method, _label) in SPEC.items():
            raw = current[sid].get(key)
            entry: dict[str, Any] = {"raw": raw, "self": None, "peer": None}
            if raw is not None:
                if method == "absolute":
                    entry["score"] = max(0.0, min(100.0, raw * 100))
                else:
                    peer_values = [current[p].get(key) for p in peers[sid]]
                    peer_values = [v for v in peer_values if v is not None]
                    if len(peer_values) >= (2 if market_mode else cfg["min_peers"]):
                        entry["peer"] = percentile(peer_values, raw)
                    if not market_mode:
                        entry["self"] = self_percentile(ind[key], as_of, cfg)
                    parts = [v for v in (entry["self"], entry["peer"]) if v is not None]
                    if parts:
                        base = float(np.mean(parts))
                        entry["score"] = base if direction > 0 else 100 - base
                if method == "revenue" and "score" in entry:
                    rc = cfg["revenue_components"]
                    extras = ind[key].extras
                    streak = min(extras.get("streak", 0) / rc["streak_cap_months"], 1) * 100
                    high = 100.0 if extras.get("new_high") else 0.0
                    entry["score"] = rc["yoy"] * entry["score"] + rc["streak"] * streak + rc["new_high"] * high
                    entry["streak"] = extras.get("streak")
                    entry["new_high"] = extras.get("new_high")
            if "score" not in entry:
                entry["score"] = float(neutral)
                entry["missing"] = True
            scored[sid][key] = entry

    # 3. 加權
    results: list[ScoreResult] = []
    small_cap_limit = cfg["small_cap"]["market_cap_threshold"]
    for sid in stocks:
        if sid in insufficient:
            for h in HORIZONS:
                results.append(ScoreResult(sid, as_of, h, "insufficient_data",
                                           breakdown={"reason": insufficient[sid]}))
            continue
        cap = stocks[sid].market_cap
        small_cap = bool(cap) and cap < small_cap_limit
        quarter_end = as_of.month in (3, 6, 9, 12)
        for h in HORIZONS:
            dims: dict[str, float] = {}
            items = []
            for dim in DIMENSIONS:
                dim_weight = cfg["weights"][h][dim]
                weights = indicator_weights(settings, h, dim, small_cap)
                dim_score = 0.0
                for key, w in weights.items():
                    e = scored[sid][key]
                    dim_score += w * e["score"]
                    note = []
                    if e.get("missing"):
                        note.append("資料缺漏，以中性分計")
                    if key == "trust_net" and quarter_end:
                        note.append("季底作帳期")
                    if key == "trust_net" and small_cap:
                        note.append("中小型股，投信權重加成")
                    if key == "valuation_position" and not e.get("missing"):
                        note.append(f"以{indicators[sid][key].extras.get('basis', '本益比')}計")
                    items.append({
                        "key": key,
                        "label": SPEC[key][3],
                        "dimension": dim,
                        "raw": _clean(e["raw"]),
                        "self_percentile": _clean(e["self"]),
                        "peer_percentile": _clean(e["peer"]),
                        "score": _clean(e["score"]),
                        "weight": _clean(w),
                        "dimension_weight": dim_weight,
                        "contribution": _clean(dim_weight * w * e["score"]),
                        **({"streak": e["streak"], "new_high": _clean(e["new_high"])} if "streak" in e else {}),
                        "note": "；".join(note) or None,
                    })
                dims[dim] = round(dim_score, 2)
            total = round(sum(cfg["weights"][h][d] * dims[d] for d in DIMENSIONS), 2)
            breakdown = {
                "plan": settings.plan.name,
                "comparison": "market" if market_mode else "self+peer",
                "peers": sorted(peers[sid]),
                "dimensions": {d: {"score": dims[d], "weight": cfg["weights"][h][d]} for d in DIMENSIONS},
                "indicators": items,
            }
            results.append(ScoreResult(sid, as_of, h, "ok", total, dims, breakdown))
    return results


def reasons(breakdown: dict, n: int = 3) -> list[str]:
    """高分理由：對總分貢獻高於中性分最多的指標。"""
    items = [i for i in breakdown.get("indicators", []) if not (i.get("note") or "").startswith("資料缺漏")]
    ranked = sorted(items, key=lambda i: i["dimension_weight"] * i["weight"] * (i["score"] - 50), reverse=True)
    out = []
    for i in ranked[:n]:
        if i["score"] < 60:
            break
        text = f"{i['label']} {i['score']:.0f} 分"
        if i["key"] == "revenue_growth":
            extra = []
            if i.get("streak"):
                extra.append(f"連 {i['streak']} 月年增")
            if i.get("new_high"):
                extra.append("創新高")
            if extra:
                text += f"（{'、'.join(extra)}）"
        out.append(text)
    return out


__all__ = ["DAILY", "SPEC", "ScoreResult", "indicator_weights", "percentile", "reasons", "score_universe"]
