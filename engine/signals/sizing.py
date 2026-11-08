"""風控張數（第 8 節）：依停損距離反推，再受單檔、單一族群、持股檔數上限限制。"""

from __future__ import annotations

import math
from typing import Any


def position_size(
    capital: float,
    entry: float,
    stop: float,
    cfg: dict[str, Any],
    holdings_count: int = 0,
    group_exposure: float = 0.0,
    already_held: bool = False,
) -> dict[str, Any]:
    """回傳建議張數與計算過程。

    entry：以買進區間上緣計算（最差成交價），stop：停損價。
    group_exposure：同族群現有持股市值（取最高的那個族群）。
    """
    lot = cfg["lot_size"]
    per_share_risk = entry - stop
    notes: list[str] = []
    if capital <= 0 or entry <= 0 or per_share_risk <= 0:
        return {"lots": 0, "odd_shares": 0, "limited_by": "invalid", "notes": ["價位或資金設定異常"]}

    limits = {
        "單筆風險": capital * cfg["risk_per_trade"] / per_share_risk,
        "單檔上限": capital * cfg["max_position_pct"] / entry,
        "族群上限": max(0.0, capital * cfg["max_group_pct"] - group_exposure) / entry,
    }
    if holdings_count >= cfg["max_holdings"] and not already_held:
        limits["持股檔數"] = 0.0
        notes.append(f"持股已達 {cfg['max_holdings']} 檔上限，需先換股")
    limited_by, shares = min(limits.items(), key=lambda kv: kv[1])
    shares = max(0.0, shares)
    lots = math.floor(shares / lot + 1e-9)
    odd = 0
    if lots == 0 and shares >= 1:
        odd = math.floor(shares)
        notes.append(f"資金不足一張，可考慮零股 {odd:,} 股")
    if limited_by == "族群上限" and group_exposure > 0:
        notes.append("同族群持股已接近上限")
    used = (lots * lot or odd) * entry
    return {
        "lots": lots,
        "odd_shares": odd,
        "limited_by": limited_by,
        "amount": round(used),
        "risk_amount": round((lots * lot or odd) * per_share_risk),
        "risk_budget": round(capital * cfg["risk_per_trade"]),
        "per_share_risk": round(per_share_risk, 2),
        "notes": notes,
    }
