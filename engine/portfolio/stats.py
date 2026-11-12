"""交易日誌統計（第 11 節頁面 5）：勝率、平均獲利、平均虧損、期望值，與加權指數及 0050 比較。

每一筆賣出視為一筆已實現交易，成本採平均成本法（含買進手續費），收入扣除賣出手續費與證交稅。
整體績效 =（已實現損益 + 未實現損益）÷ 總資金，期間為第一筆交易日到 as_of；
同期加權指數（價格指數）、0050（還原除息與分割，含息報酬）以 market_series 收盤計算。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from engine.adjust import adjust_points


def closed_trades(trades: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """回傳 (已實現交易列表, 目前部位 {stock_id: {shares, cost, opened}})。"""
    pos: dict[str, dict] = {}
    closed: list[dict] = []
    for t in sorted(trades, key=lambda t: (t["date"], t["trade_id"])):
        p = pos.setdefault(t["stock_id"], {"shares": 0, "cost": 0.0, "opened": None})
        if t["side"] == "buy":
            if p["shares"] == 0:
                p["opened"] = t["date"]
            p["shares"] += t["shares"]
            p["cost"] += t["price"] * t["shares"] + t["fee"]
            continue
        if p["shares"] <= 0:
            continue  # 資料不一致（沒有買進紀錄）時略過
        shares = min(t["shares"], p["shares"])
        basis = p["cost"] / p["shares"] * shares
        proceeds = t["price"] * shares - t["fee"] - t["tax"]
        pnl = proceeds - basis
        closed.append({
            "stock_id": t["stock_id"], "date": t["date"], "opened": p["opened"], "shares": shares,
            "pnl": round(pnl, 2), "pnl_pct": round(pnl / basis, 6) if basis else 0.0,
            "days": (t["date"] - p["opened"]).days if p["opened"] else None,
        })
        p["cost"] -= basis
        p["shares"] -= shares
    return closed, {s: p for s, p in pos.items() if p["shares"] > 0}


def _series_return(points: list[tuple[date, float]], start: date, end: date) -> float | None:
    before = [v for d, v in points if d <= start]
    upto = [v for d, v in points if d <= end]
    if not upto:
        return None
    base = before[-1] if before else next((v for d, v in points if d >= start), None)
    return round(upto[-1] / base - 1, 6) if base else None


def trade_stats(trades: list[dict], last_close: dict[str, float], capital: float, as_of: date,
                benchmarks: dict[str, list[tuple[date, float]]]) -> dict[str, Any]:
    closed, open_pos = closed_trades([t for t in trades if t["date"] <= as_of])
    wins = [c for c in closed if c["pnl"] > 0]
    losses = [c for c in closed if c["pnl"] <= 0]
    n = len(closed)
    win_rate = len(wins) / n if n else None
    avg_win = sum(c["pnl_pct"] for c in wins) / len(wins) if wins else None
    avg_loss = sum(c["pnl_pct"] for c in losses) / len(losses) if losses else None
    expectancy = None
    if n:
        expectancy = (win_rate or 0) * (avg_win or 0) + (1 - (win_rate or 0)) * (avg_loss or 0)
    realized = sum(c["pnl"] for c in closed)
    unrealized = 0.0
    for sid, p in open_pos.items():
        close = last_close.get(sid)
        if close is not None:
            unrealized += close * p["shares"] - p["cost"]

    start = min((t["date"] for t in trades), default=None)
    total = realized + unrealized
    out: dict[str, Any] = {
        "closed_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": _r(win_rate),
        "avg_win_pct": _r(avg_win),
        "avg_loss_pct": _r(avg_loss),
        "expectancy_pct": _r(expectancy),
        "avg_win_amount": round(sum(c["pnl"] for c in wins) / len(wins)) if wins else None,
        "avg_loss_amount": round(sum(c["pnl"] for c in losses) / len(losses)) if losses else None,
        "realized_pnl": round(realized),
        "unrealized_pnl": round(unrealized),
        "avg_holding_days": round(sum(c["days"] or 0 for c in closed) / n, 1) if n else None,
        "start": start.isoformat() if start else None,
        "end": as_of.isoformat(),
        "portfolio_return": _r(total / capital) if capital and start else None,
        "benchmarks": {},
        "recent": closed[-10:],
    }
    if start:
        for name, points in benchmarks.items():
            out["benchmarks"][name] = _series_return(points, start, as_of)
    return out


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 6)


BENCHMARK_LABELS = {"taiex": "加權指數", "etf_0050": "0050"}


def portfolio_stats(repo, as_of: date) -> dict[str, Any]:
    """從資料庫讀交易與價格計算統計（CLI 與網站共用）。"""
    from datetime import timedelta

    trades = repo.trades(as_of)
    ids = sorted({t["stock_id"] for t in trades})
    last_close: dict[str, float] = {}
    for sid, _d, c in repo.closes(ids, as_of - timedelta(days=30), as_of):
        last_close[sid] = c  # closes 依日期排序，最後一筆為最新
    start = min((t["date"] for t in trades), default=as_of)
    benchmarks = {k: repo.series(k, start - timedelta(days=10), as_of) for k in BENCHMARK_LABELS}
    benchmarks["etf_0050"] = adjust_points(benchmarks["etf_0050"], repo.adjustments(["0050"]).get("0050"))
    capital = repo.total_capital()
    return trade_stats(trades, last_close, capital, as_of, benchmarks)
