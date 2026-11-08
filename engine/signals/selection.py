"""入選名單（第 8 節買進條件 1）。

免費模式：每個族群內總分前 N 名（且總分 ≥ min_total）。
付費模式（top_percent_list）：全市場總分前 5%。
"""

from __future__ import annotations

import math
from typing import Any


def group_top_list(
    totals: dict[str, float],
    groups: dict[str, tuple[str, set[str]]],
    top_n: int,
    min_total: float,
) -> dict[str, list[str]]:
    """回傳 {stock_id: [入選的族群名稱]}。"""
    out: dict[str, list[str]] = {}
    for name, (_kind, members) in sorted(groups.items()):
        ranked = sorted((s for s in members if s in totals), key=lambda s: (-totals[s], s))
        for sid in ranked[:top_n]:
            if totals[sid] >= min_total:
                out.setdefault(sid, []).append(name)
    # 沒有被歸到任何族群的股票，自成一組比較
    grouped = set().union(*(m for _, m in groups.values())) if groups else set()
    for sid, total in totals.items():
        if sid not in grouped and total >= min_total:
            out.setdefault(sid, []).append("未分類")
    return out


def top_percent_list(totals: dict[str, float], pct: float, min_total: float = 0) -> dict[str, list[str]]:
    if not totals:
        return {}
    n = max(1, math.ceil(len(totals) * pct))
    ranked = sorted(totals, key=lambda s: (-totals[s], s))[:n]
    label = f"全市場前 {pct:.0%}"
    return {sid: [label] for sid in ranked if totals[sid] >= min_total}


def select(totals: dict[str, float], groups: dict[str, tuple[str, set[str]]], top_percent: bool,
           cfg: dict[str, Any]) -> dict[str, list[str]]:
    sel = cfg["selection"]
    if top_percent:
        return top_percent_list(totals, sel["top_percent"], sel.get("min_total", 0))
    return group_top_list(totals, groups, sel["per_group_top_n"], sel.get("min_total", 0))
