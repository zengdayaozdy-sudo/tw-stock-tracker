"""流動性過濾（第 4 節步驟 3）：排除日均量過低、全額交割、處置、注意股。"""

from __future__ import annotations

from typing import Any

STATUS_LABELS = {"disposition": "處置股", "attention": "注意股", "full_delivery": "全額交割股"}
AVG_DAYS = 20


def liquidity_flags(
    ids: list[str],
    avg_volume: dict[str, tuple[float, int]],
    status: dict[str, list[str]],
    cfg: dict[str, Any],
) -> list[dict]:
    """回傳 screen_flags 列（kind=exclude）。

    avg_volume: {stock_id: (近 20 日平均成交股數, 實際天數)}
    cfg: risk.yaml 的 liquidity 區塊（含 lot_size 時以其為一張股數）
    """
    lot = cfg.get("lot_size", 1000)
    min_lots = cfg["min_avg_volume_lots"]
    excluded = set(cfg.get("exclude_status") or [])
    out: list[dict] = []
    for sid in ids:
        avg, days = avg_volume.get(sid, (0.0, 0))
        lots = avg / lot
        if days == 0:
            out.append(_flag(sid, "沒有成交量資料", {"avg_lots": None, "days": 0}))
        elif lots < min_lots:
            out.append(_flag(sid, f"{days} 日均量 {lots:,.0f} 張，低於 {min_lots:,} 張",
                             {"avg_lots": round(lots, 1), "days": days, "min_lots": min_lots}))
        for s in status.get(sid, []):
            if s in excluded:
                out.append(_flag(sid, STATUS_LABELS.get(s, s), {"status": s}))
    return out


def _flag(sid: str, reason: str, detail: dict) -> dict:
    return {"stock_id": sid, "kind": "exclude", "reason": reason, "detail": detail}
