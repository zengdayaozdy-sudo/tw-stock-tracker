"""族群強弱（第 7.3 節）。

族群報酬＝成分股等權平均報酬；相對報酬＝族群報酬 − 加權指數報酬。
綜合排名＝近 1／3 個月相對報酬與族群平均分數的百分位加權平均（權重在 filters.yaml）。
排名後段（weak_bottom_pct）為弱勢族群，其成分股不發買進訊號。
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

KIND_LABELS = {"industry": "產業", "custom": "自訂"}


def period_return(s: pd.Series, as_of: date, days: int) -> float | None:
    """as_of 當天（含）以前最後一筆收盤，相對 days 個交易日前的報酬。"""
    s = s[s.index <= pd.Timestamp(as_of)].dropna()
    if len(s) <= days or s.index[-1] != pd.Timestamp(as_of):
        return None
    return float(s.iloc[-1] / s.iloc[-1 - days] - 1)


def _rank_pct(values: dict[str, float | None]) -> dict[str, float]:
    """越大越好的百分位（0–100），缺值不排名。"""
    valid = {k: v for k, v in values.items() if v is not None and not math.isnan(v)}
    if not valid:
        return {}
    arr = np.array(list(valid.values()))
    return {k: float(((arr < v).sum() + 0.5 * (arr == v).sum()) / len(arr) * 100) for k, v in valid.items()}


def group_strength(
    groups: dict[str, tuple[str, set[str]]],
    scope: set[str],
    closes: dict[str, pd.Series],
    taiex: pd.Series,
    scores: dict[str, float],
    as_of: date,
    cfg: dict[str, Any],
) -> list[dict]:
    short_days, long_days = cfg["lookback_days"]
    keys = {"return_20": short_days, "return_60": long_days}
    stock_ret = {
        k: {sid: period_return(closes[sid], as_of, d) for sid in scope if sid in closes} for k, d in keys.items()
    }
    taiex_ret = {k: period_return(taiex, as_of, d) for k, d in keys.items()} if not taiex.empty else {}

    rows: list[dict] = []
    for name, (kind, members) in groups.items():
        inside = sorted(members & scope)
        if len(inside) < cfg["min_members"]:
            continue
        row: dict[str, Any] = {"name": name, "kind": kind, "members": inside, "size": len(inside)}
        for k in keys:
            rets = [stock_ret[k].get(s) for s in inside]
            rets = [r for r in rets if r is not None]
            row[k] = round(float(np.mean(rets)), 4) if rets else None
            base = taiex_ret.get(k)
            row[f"rel_{k}"] = round(row[k] - base, 4) if row[k] is not None and base is not None else None
        member_scores = [scores[s] for s in inside if s in scores]
        row["avg_score"] = round(float(np.mean(member_scores)), 2) if member_scores else None
        rows.append(row)

    # 相對大盤報酬缺（加權指數資料不足）時改用絕對報酬排名，排名結果相同只是基準不同
    weights = cfg["rank_weights"]
    components = {
        "return_20": _rank_pct({r["name"]: r["rel_return_20"] if r["rel_return_20"] is not None else r["return_20"]
                                for r in rows}),
        "return_60": _rank_pct({r["name"]: r["rel_return_60"] if r["rel_return_60"] is not None else r["return_60"]
                                for r in rows}),
        "score": _rank_pct({r["name"]: r["avg_score"] for r in rows}),
    }
    for r in rows:
        parts = [(weights[k], comp[r["name"]]) for k, comp in components.items() if r["name"] in comp]
        total_w = sum(w for w, _ in parts)
        r["composite"] = round(sum(w * v for w, v in parts) / total_w, 2) if total_w else None

    ranked = sorted((r for r in rows if r["composite"] is not None), key=lambda r: r["composite"], reverse=True)
    unranked = [r for r in rows if r["composite"] is None]
    n = len(ranked)
    weak_n = math.ceil(n * cfg["weak_bottom_pct"]) if n >= cfg["min_groups_for_weak"] else 0
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i
        r["weak"] = i > n - weak_n
    for r in unranked:
        r["rank"] = None
        r["weak"] = False
    return ranked + unranked


def weak_group_flags(strength: list[dict], scope: set[str]) -> list[dict]:
    """個股是否屬弱勢族群。

    一檔可屬多個族群：自訂族群（題材）比產業分類更貼近股價驅動因素，
    有排名的自訂族群時只看自訂族群，否則看產業族群；所屬族群「全部」弱勢才擋買進。
    """
    by_stock: dict[str, dict[str, list[dict]]] = {}
    for g in strength:
        if g["rank"] is None:
            continue
        for sid in g["members"]:
            by_stock.setdefault(sid, {}).setdefault(g["kind"], []).append(g)
    out: list[dict] = []
    for sid in sorted(scope):
        kinds = by_stock.get(sid)
        if not kinds:
            continue
        basis = kinds.get("custom") or kinds.get("industry") or []
        if basis and all(g["weak"] for g in basis):
            names = "、".join(g["name"] for g in basis)
            out.append({
                "stock_id": sid,
                "kind": "block",
                "reason": f"弱勢族群：{names}",
                "detail": {"groups": [{"name": g["name"], "kind": g["kind"], "rank": g["rank"]} for g in basis]},
            })
    return out
