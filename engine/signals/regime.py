"""盤勢模式：結合大盤燈號與反彈偵測，決定能不能發買進訊號、用哪些進場條件、部位縮小多少。

| 燈號 | 反彈狀態 | 模式 | 買進訊號 |
|---|---|---|---|
| 偏多／中性 | 無 | normal | 一般進場條件、正常部位 |
| 偏多／中性 | 嘗試／確認 | normal | 加入領先型進場條件（跌深反轉、RS 先創高、站回月線），正常部位 |
| 偏空 | 反彈確認 | rebound_confirmed | 加入領先型進場條件，部位 × confirmed.size_factor，停損收緊 |
| 偏空 | 反彈嘗試 | rebound_attempt | 同上，部位 × attempt.size_factor（試單；預設關閉） |
| 偏空 | 反彈嘗試（試單關閉） | blocked＋watch | 不發訊號，觸發進場條件者列入「反彈觀察」 |
| 偏空 | 無 | blocked | 不發新買進訊號 |

設定在 config/risk.yaml 的 rebound_mode。
"""

from __future__ import annotations

from typing import Any

MODE_LABELS = {
    "normal": "一般",
    "rebound_confirmed": "反彈單",
    "rebound_attempt": "反彈試單",
    "blocked": "偏空停止買進",
}


def market_regime(light: str | None, rebound_state: str | None, risk: dict[str, Any]) -> dict[str, Any]:
    cfg = risk.get("rebound_mode") or {}
    base = list(risk["entry_conditions"])
    leading = [c for c in cfg.get("entry_conditions", []) if c not in base]
    active = rebound_state in ("attempt", "confirmed") and cfg.get("enabled", False)
    entry = base + leading if active else base
    if light != "bearish":
        return {"mode": "normal", "label": MODE_LABELS["normal"], "size_factor": 1.0, "entry_conditions": entry,
                "tighten_stop": False}
    if active:
        tier = "confirmed" if rebound_state == "confirmed" else "attempt"
        tcfg = cfg.get(tier) or {}
        if tcfg.get("allow", False):
            mode = f"rebound_{tier}"
            return {"mode": mode, "label": MODE_LABELS[mode], "size_factor": float(tcfg.get("size_factor", 0.5)),
                    "entry_conditions": entry, "tighten_stop": True}
    # 反彈尚未確認：不發訊號，但照樣檢查領先型條件，觸發者列入反彈觀察（濾網擋下名單）
    return {"mode": "blocked", "label": MODE_LABELS["blocked"], "size_factor": 0.0, "entry_conditions": entry,
            "tighten_stop": True, "watch": active}


def scaled_position_cfg(position_cfg: dict[str, Any], factor: float) -> dict[str, Any]:
    """反彈單：單筆風險與單檔上限同比例縮小。"""
    if factor >= 1:
        return position_cfg
    return {**position_cfg, "risk_per_trade": position_cfg["risk_per_trade"] * factor,
            "max_position_pct": position_cfg["max_position_pct"] * factor}
