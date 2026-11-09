"""每日持股檢查（第 4 節步驟 8）：停損、移動停利回落、分數跌出門檻、持有過久未上漲。

觸發者寫入 signals（side = sell），由每日報告的「持股提醒」推播。只用 as_of 當天（含）以前的收盤價。
大盤燈號偏空時停損收緊（成本 × (1 − bearish_market_percent) 與原停損取較高者）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from engine.adjust import adjust_points
from engine.config import Settings
from engine.repository import Repository
from engine.signals.levels import round_tick

EXIT_LABELS = {
    "stop_loss": "跌破停損",
    "trailing_stop": "移動停利回落",
    "take_profit": "達停利目標",
    "score_drop": "分數跌出門檻",
    "stale": "持有過久未上漲",
    "news": "消息面重大利空",
}


def adjust_holding(h: dict, adjustments: list[tuple[date, float]] | None, as_of: date) -> tuple[dict, list]:
    """開倉後遇到除權息、分割或減資：成本、停損、停利換算成今天的價格基準。

    避免除息當天股價自然下跌被誤判為跌破停損或回落停利。回傳 (換算後的持股, 開倉後的調整事件)。
    最高收盤改由還原後的歷史重新計算。
    """
    events = [(d, f) for d, f in (adjustments or []) if h["opened_date"] < d <= as_of]
    if not events:
        return h, []
    factor = 1.0
    for _d, f in events:
        factor *= f
    out = dict(h)
    for key in ("avg_cost", "stop_loss", "take_profit"):
        if out.get(key):
            out[key] = float(out[key]) * factor
    out["highest_close"] = None
    return out, events


def effective_stop(h: dict, bearish: bool, cfg: dict) -> float | None:
    stop = h.get("stop_loss")
    if bearish:
        tight = round_tick(h["avg_cost"] * (1 - cfg["stop_loss"]["bearish_market_percent"]), "down")
        stop = max(stop or 0, tight)
    return stop


def check_holding(h: dict, closes: list[tuple[date, float]], scores: dict[str, dict], news: list[str], as_of: date,
                  bearish: bool, risk: dict) -> dict[str, Any] | None:
    """回傳賣出提醒（沒有觸發時為 None）。closes：開倉日以來的 (日期, 收盤)。"""
    hist = [(d, c) for d, c in closes if h["opened_date"] <= d <= as_of]
    if not hist or hist[-1][0] != as_of:
        return None  # 當日沒有收盤價（停牌或資料缺漏）不判斷
    close = hist[-1][1]
    highest = max([c for _d, c in hist] + [h.get("highest_close") or 0])
    ex = risk["exit"]
    hits: dict[str, str] = {}

    stop = effective_stop(h, bearish, risk)
    if stop and close <= stop:
        hits["stop_loss"] = f"收盤 {close:g} 跌破停損 {stop:g}" + ("（大盤偏空收緊）" if bearish else "")

    trailing = risk["take_profit"]["trailing_percent"]
    trail_price = highest * (1 - trailing)
    if highest >= h["avg_cost"] * (1 + ex["trailing_activation"]) and close <= trail_price and "stop_loss" not in hits:
        hits["trailing_stop"] = f"自高點 {highest:g} 回落 {1 - close / highest:.1%}（移動停利 {trailing:.0%}）"

    if h.get("take_profit") and close >= h["take_profit"]:
        hits["take_profit"] = f"收盤 {close:g} 達停利目標 {h['take_profit']:g}，可分批獲利或改用移動停利"

    totals = [s["total"] for s in scores.values() if s.get("total") is not None and s.get("date") == as_of.isoformat()]
    if totals and max(totals) < ex["min_score"]:
        hits["score_drop"] = f"短線／中長線分數最高 {max(totals):.0f}，低於 {ex['min_score']}"

    held_days = len(hist) - 1  # 開倉後經過的交易日數
    gain = close / h["avg_cost"] - 1
    if held_days >= ex["max_holding_days_no_gain"] and gain <= ex["no_gain_threshold"]:
        hits["stale"] = f"持有 {held_days} 個交易日，報酬 {gain:+.1%}，考慮換股"

    if news:
        hits["news"] = "；".join(news)

    if not hits:
        return {"stock_id": h["stock_id"], "close": close, "highest": highest, "triggered": {}}
    return {"stock_id": h["stock_id"], "close": close, "highest": highest, "triggered": hits, "stop": stop,
            "gain": round(gain, 4), "held_days": held_days}


def run_holdings(settings: Settings, repo: Repository, as_of: date) -> dict[str, Any]:
    fetch = repo.latest_job("fetch", as_of)
    if fetch and fetch[0] == "skipped":
        return {"status": "closed", "checked": 0, "sell_signals": []}
    holdings = repo.holdings()
    bearish = repo.market_light(as_of) == "bearish"
    news: dict[str, list[str]] = {}
    for sid, fl in repo.flags(as_of).items():
        news[sid] = [f["reason"] for f in fl if f["filter"] == "news"]
    names = repo.stock_names([h["stock_id"] for h in holdings])
    signals: list[dict] = []
    missing: list[str] = []
    adjustments = repo.adjustments([h["stock_id"] for h in holdings]) if holdings else {}
    for h in holdings:
        start = min(h["opened_date"], as_of - timedelta(days=10))
        closes = repo.price_history(h["stock_id"], start, as_of)
        scores = repo.latest_scores(h["stock_id"], as_of)
        adj_h, events = adjust_holding(h, adjustments.get(h["stock_id"]), as_of)
        if events:
            closes = adjust_points(closes, events)
        result = check_holding(adj_h, closes, scores, news.get(h["stock_id"], []), as_of, bearish, settings.risk)
        if result is None:
            missing.append(h["stock_id"])
            continue
        if not events and result["highest"] > (h.get("highest_close") or 0):
            repo.save_holding({**h, "highest_close": result["highest"]})
        if not result["triggered"]:
            continue
        labels = [EXIT_LABELS[k] for k in result["triggered"]]
        signals.append({
            "stock_id": h["stock_id"], "date": as_of, "horizon": "short", "side": "sell",
            "triggers": list(result["triggered"]),
            "entry_low": None, "entry_high": None,
            "stop_loss": result["stop"], "take_profit": h.get("take_profit"),
            "suggested_lots": h["shares"] // settings.risk["position"]["lot_size"],
            "reason": f"{'、'.join(labels)}：{'；'.join(result['triggered'].values())}",
            "detail": {"name": names.get(h["stock_id"]), "close": result["close"], "highest": result["highest"],
                       "avg_cost": h["avg_cost"], "shares": h["shares"], "gain": result["gain"],
                       "held_days": result["held_days"], "triggered": result["triggered"], "bearish": bearish,
                       "adjusted_for": [d.isoformat() for d, _f in events]},
        })
    repo.replace_signals(as_of, "sell", signals)
    repo.conn.commit()
    return {"status": "ok", "checked": len(holdings), "sell_signals": signals, "no_price": missing}
