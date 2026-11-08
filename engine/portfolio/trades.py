"""交易日誌（第 11 節頁面 5）：登錄買賣，自動計算手續費與證交稅、記錄當時的分數與訊號，並更新持股。

成本採平均成本法（含買進手續費）；停損停利優先採用最近的買進訊號，否則依 risk.yaml 規則計算。
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from engine.config import Settings
from engine.repository import Repository
from engine.signals.levels import stop_price


class TradeError(ValueError):
    """交易資料不合理（例如賣出超過持有股數）。"""


def commission(price: float, shares: int, costs: dict[str, Any]) -> int:
    """手續費 = 成交金額 × 費率 × 折扣，無條件捨去，最低 min_commission 元。"""
    fee = math.floor(price * shares * costs["commission_rate"] * costs.get("commission_discount", 1.0))
    return max(costs["min_commission"], fee)


def sell_tax(price: float, shares: int, costs: dict[str, Any]) -> int:
    return math.floor(price * shares * costs["tax_rate"])


def record_trade(
    settings: Settings,
    repo: Repository,
    stock_id: str,
    trade_date: date,
    side: str,
    price: float,
    shares: int,
    fee: float | None = None,
    tax: float | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    if side not in ("buy", "sell"):
        raise TradeError("side 必須是 buy 或 sell")
    if price <= 0 or shares <= 0:
        raise TradeError("價格與股數必須大於 0")
    if stock_id not in repo.known_stock_ids():
        raise TradeError(f"股票清單中找不到 {stock_id}")
    costs = settings.risk["costs"]
    fee = float(commission(price, shares, costs) if fee is None else fee)
    tax = float((sell_tax(price, shares, costs) if side == "sell" else 0) if tax is None else tax)

    lookback = trade_date - timedelta(days=settings.risk["exit"]["signal_lookback_days"])
    signal = repo.latest_signal(stock_id, side, lookback, trade_date)
    trade = {
        "stock_id": stock_id, "date": trade_date, "side": side, "price": price, "shares": shares,
        "fee": fee, "tax": tax, "note": note,
        "score_snapshot": repo.latest_scores(stock_id, trade_date),
        "signal_snapshot": signal,
    }

    held = repo.holding(stock_id)
    if side == "buy":
        holding = _apply_buy(settings, repo, held, trade, signal)
    else:
        holding = _apply_sell(held, trade)
    trade["trade_id"] = repo.insert_trade(trade)
    if holding is None:
        repo.delete_holding(stock_id)
    else:
        repo.save_holding(holding)
    repo.conn.commit()
    return {"trade": trade, "holding": holding}


def _apply_buy(settings: Settings, repo: Repository, held: dict | None, t: dict, signal: dict | None) -> dict:
    cost = t["price"] * t["shares"] + t["fee"]
    if held:
        shares = held["shares"] + t["shares"]
        avg = (held["avg_cost"] * held["shares"] + cost) / shares
        h = {**held, "shares": shares, "avg_cost": round(avg, 4),
             "highest_close": max(held.get("highest_close") or 0, t["price"])}
    else:
        h = {"stock_id": t["stock_id"], "shares": t["shares"], "avg_cost": round(cost / t["shares"], 4),
             "opened_date": t["date"], "stop_loss": None, "take_profit": None, "highest_close": t["price"]}
    if signal and signal.get("stop_loss"):
        h["stop_loss"], h["take_profit"] = signal["stop_loss"], signal.get("take_profit")
    elif h.get("stop_loss") is None:
        closes = [c for _d, c in repo.price_history(t["stock_id"], t["date"] - timedelta(days=100), t["date"])]
        ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
        h["stop_loss"], _basis = stop_price(t["price"], ma60, settings.risk["stop_loss"])
    return h


def _apply_sell(held: dict | None, t: dict) -> dict | None:
    if not held:
        raise TradeError(f"目前沒有持有 {t['stock_id']}，無法登錄賣出")
    if t["shares"] > held["shares"]:
        raise TradeError(f"賣出 {t['shares']:,} 股超過持有 {held['shares']:,} 股")
    remaining = held["shares"] - t["shares"]
    if remaining == 0:
        return None
    return {**held, "shares": remaining}  # 平均成本法：賣出不改變剩餘部位的平均成本
