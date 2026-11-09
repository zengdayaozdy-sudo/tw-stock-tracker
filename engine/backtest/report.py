"""回測績效指標與報告（第 13 節）：年化報酬、最大回撤、勝率、平均獲利／虧損、期望值、交易次數，
並與同期加權指數、0050 比較。"""

from __future__ import annotations

from datetime import date
from typing import Any

EXIT_LABELS = {
    "stop_loss": "停損", "trailing_stop": "移動停利", "take_profit": "達停利目標", "score_drop": "分數跌出門檻",
    "stale": "持有過久未上漲", "news": "消息面", "end": "期末",
}


def cagr(points: list[tuple[date, float]]) -> float | None:
    if len(points) < 2 or points[0][1] <= 0:
        return None
    years = (points[-1][0] - points[0][0]).days / 365.25
    if years <= 0:
        return None
    return (points[-1][1] / points[0][1]) ** (1 / years) - 1


def max_drawdown(points: list[tuple[date, float]]) -> float | None:
    if not points:
        return None
    peak, mdd = points[0][1], 0.0
    for _d, v in points:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd


def total_return(points: list[tuple[date, float]]) -> float | None:
    if len(points) < 2 or points[0][1] <= 0:
        return None
    return points[-1][1] / points[0][1] - 1


def trade_metrics(trades: list[dict]) -> dict[str, Any]:
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / n if n else None
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else None
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else None
    expectancy = (win_rate or 0) * (avg_win or 0) + (1 - (win_rate or 0)) * (avg_loss or 0) if n else None
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    return {
        "trades": n, "wins": len(wins), "losses": len(losses), "win_rate": win_rate,
        "avg_win_pct": avg_win, "avg_loss_pct": avg_loss, "expectancy_pct": expectancy,
        "avg_holding_days": sum(t["days"] for t in trades) / n if n else None,
        "exit_reasons": reasons,
    }


def align(points: list[tuple[date, float]], start: date, end: date) -> list[tuple[date, float]]:
    return [(d, v) for d, v in points if start <= d <= end]


def exposure_metrics(points: list[tuple[date, float]]) -> dict:
    """平均資金使用率與有持股的天數比例：用來分辨報酬落後是選股問題，還是資金閒置太多。"""
    if not points:
        return {"avg_exposure": None, "invested_days": None}
    values = [v for _d, v in points]
    return {"avg_exposure": sum(values) / len(values),
            "invested_days": sum(1 for v in values if v > 0.01) / len(values)}


def build_metrics(result: dict, benchmarks: dict[str, list[tuple[date, float]]], start: date, end: date) -> dict:
    eq = result["equity"]
    out = {
        "start": start.isoformat(), "end": end.isoformat(),
        "final_value": round(result["final_value"]),
        "total_return": total_return(eq), "cagr": cagr(eq), "max_drawdown": max_drawdown(eq),
        **trade_metrics(result["trades"]),
        "open_positions": len(result["open_positions"]),
        **exposure_metrics(result.get("exposure") or []),
        "skipped": result["skipped"],
        "benchmarks": {},
    }
    for name, pts in benchmarks.items():
        b = align(pts, start, end)
        out["benchmarks"][name] = {"total_return": total_return(b), "cagr": cagr(b), "max_drawdown": max_drawdown(b)}
    return out


def _ratio(x: float | None) -> str:
    return "—" if x is None else f"{x:.0%}"


def _pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x:+.{digits}%}"


BENCH_LABELS = {"taiex": "加權指數（價格指數）", "etf_0050": "0050（含息還原）"}


def render_markdown(m: dict, period: str, plan_label: str, warnings: list[str], params: dict) -> str:
    title = {"tune": "調參區間", "holdout": "保留驗證區間", "full": "全期間"}[period]
    win_rate = "—" if m["win_rate"] is None else f"{m['win_rate']:.1%}"
    holding = "—" if m["avg_holding_days"] is None else f"{m['avg_holding_days']:.1f}"
    lines = [
        f"# 回測報告｜{title}",
        "",
        f"期間 {m['start']} 至 {m['end']}｜{plan_label}｜期末資產 {m['final_value']:,} 元",
        "",
        "| 指標 | 策略 | " + " | ".join(BENCH_LABELS.get(k, k) for k in m["benchmarks"]) + " |",
        "|---|---|" + "---|" * len(m["benchmarks"]),
        "| 總報酬 | " + _pct(m["total_return"]) + " | "
        + " | ".join(_pct(b["total_return"]) for b in m["benchmarks"].values()) + " |",
        "| 年化報酬 | " + _pct(m["cagr"]) + " | " + " | ".join(_pct(b["cagr"]) for b in m["benchmarks"].values()) + " |",
        "| 最大回撤 | " + _pct(m["max_drawdown"]) + " | "
        + " | ".join(_pct(b["max_drawdown"]) for b in m["benchmarks"].values()) + " |",
        "",
        "| 交易統計 | |",
        "|---|---|",
        f"| 交易次數 | {m['trades']}（勝 {m['wins']}／敗 {m['losses']}） |",
        f"| 勝率 | {win_rate} |",
        f"| 平均資金使用率 | {_ratio(m.get('avg_exposure'))} |",
        f"| 有持股的交易日 | {_ratio(m.get('invested_days'))} |",
        f"| 平均獲利 | {_pct(m['avg_win_pct'], 2)} |",
        f"| 平均虧損 | {_pct(m['avg_loss_pct'], 2)} |",
        f"| 期望值（每筆） | {_pct(m['expectancy_pct'], 2)} |",
        f"| 平均持有天數 | {holding} |",
        f"| 期末未平倉 | {m['open_positions']} 檔 |",
        "",
        "出場原因：" + ("、".join(f"{EXIT_LABELS.get(k, k)} {v}" for k, v in m["exit_reasons"].items()) or "無"),
        "",
        "未下單：" + "、".join(f"{k} {v}" for k, v in m["skipped"].items()),
    ]
    if params.get("overrides"):
        lines += ["", "參數覆寫：" + "、".join(f"`{k}={v}`" for k, v in params["overrides"].items())]
    if warnings:
        lines += ["", "## 注意", *(f"- {w}" for w in warnings)]
    return "\n".join(lines) + "\n"
