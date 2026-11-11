"""醞釀名單事件研究：列入名單的股票，之後真的轉強的比例，以及照建議價位操作的結果。

不是投組回測（醞釀名單不是買進訊號），而是逐筆事件統計：
- 事件：某檔股票符合跌深前提且今天尚未轉強；同一檔連續多天符合只算第一天
- 轉強率：之後 entry_days 個交易日內出現「跌深反轉」（收盤條件）
- 照價位操作：之後 entry_days 日內盤中觸及轉強價就以轉強價（跳空以開盤價，超過買進上緣不追）買進，
  之後觸及停損或目標出場（同一天兩者都觸及以停損計，保守），否則持有滿 hold_days 以收盤價出場；扣手續費與證交稅
- 以強勢跡象數量分組，與「跌深但沒有強勢跡象」的對照組比較
只用事件當天以前的資料判斷是否列入，之後的資料只用來計算結果。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from engine.signals.entry import oversold_reversal
from engine.signals.setup import SIGN_LABELS, setup_levels, setup_signs


def _simulate(p: pd.DataFrame, i: int, lv: dict, cfg: dict, costs: dict) -> dict | None:
    """p：完整日 K；i：事件當天位置。回傳交易結果或 None（沒進場）。"""
    st = cfg["study"]
    entry_at, price = None, None
    for j in range(i + 1, min(i + 1 + st["entry_days"], len(p))):
        o, h = float(p["open"].iloc[j]), float(p["high"].iloc[j])
        if h >= lv["trigger_price"]:
            fill = max(o, lv["trigger_price"])
            if fill <= lv["entry_high"]:
                entry_at, price = j, fill
            break  # 跳空超過買進上緣：不追，放棄這次
    if entry_at is None:
        return None
    exit_price, reason, k = None, "到期", entry_at
    for k in range(entry_at, min(entry_at + st["hold_days"], len(p))):
        lo, hi, o = float(p["low"].iloc[k]), float(p["high"].iloc[k]), float(p["open"].iloc[k])
        if lo <= lv["stop_loss"]:
            exit_price, reason = (min(o, lv["stop_loss"]) if k > entry_at else lv["stop_loss"]), "停損"
            break
        if hi >= lv["take_profit"]:
            exit_price, reason = (max(o, lv["take_profit"]) if k > entry_at else lv["take_profit"]), "達目標"
            break
    if exit_price is None:
        exit_price = float(p["close"].iloc[k])
    fee = costs["commission_rate"] * costs.get("commission_discount", 1.0)
    ret = exit_price * (1 - fee - costs["tax_rate"]) / (price * (1 + fee)) - 1
    return {"return": ret, "reason": reason, "days": k - entry_at + 1}


def _summary(events: list[dict]) -> dict[str, Any]:
    n = len(events)
    if not n:
        return {"events": 0}
    reversed_ = sum(1 for e in events if e["reversed"])
    trades = [e["trade"] for e in events if e["trade"]]
    wins = [t for t in trades if t["return"] > 0]
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    fwd = [e["fwd10"] for e in events if e["fwd10"] is not None]
    return {
        "events": n,
        "reversal_rate": reversed_ / n,
        "fwd10_avg": sum(fwd) / len(fwd) if fwd else None,
        "trades": len(trades),
        "fill_rate": len(trades) / n,
        "win_rate": len(wins) / len(trades) if trades else None,
        "avg_return": sum(t["return"] for t in trades) / len(trades) if trades else None,
        "avg_win": sum(t["return"] for t in wins) / len(wins) if wins else None,
        "avg_loss": (sum(t["return"] for t in trades if t["return"] <= 0) / (len(trades) - len(wins))
                     if len(trades) > len(wins) else None),
        "exit_reasons": reasons,
    }


def run_study(frames: dict, start: date, end: date, risk: dict) -> dict[str, Any]:
    cfg = risk["setup_watch"]
    rcfg = risk["oversold_reversal"]
    events: list[dict] = []
    for sid, f in frames.items():
        p = f.prices.dropna(subset=["close"]).sort_index()
        if len(p) < 60:
            continue
        idx = p.index
        prev_setup = False
        for i in range(len(p)):
            d = idx[i]
            if d < pd.Timestamp(start) or d > pd.Timestamp(end):
                continue
            hist = p.iloc[max(0, i - 80): i + 1]  # 判斷只需要近期資料，截短加速
            if oversold_reversal(hist, rcfg) is not None:  # 今天已轉強，不是「醞釀中」
                prev_setup = False
                continue
            signs = setup_signs(hist, f.chips, f.benchmark, d.date(), cfg)
            if signs is None:
                prev_setup = False
                continue
            if prev_setup:
                continue
            prev_setup = True
            lv = setup_levels(hist, d.date(), cfg, risk)
            reversed_ = any(oversold_reversal(p.iloc[max(0, j - 80): j + 1], rcfg) is not None
                            for j in range(i + 1, min(i + 1 + cfg["study"]["entry_days"], len(p))))
            fwd10 = float(p["close"].iloc[i + 10]) / float(p["close"].iloc[i]) - 1 if i + 10 < len(p) else None
            events.append({"stock_id": sid, "date": d.date().isoformat(), "signs": sorted(signs),
                           "reversed": reversed_, "fwd10": fwd10,
                           "trade": _simulate(p, i, lv, cfg, risk["costs"])})

    groups = {
        "對照組：跌深、沒有強勢跡象": [e for e in events if not e["signs"]],
        "醞釀名單：1 項以上": [e for e in events if len(e["signs"]) >= 1],
        "醞釀名單：2 項以上": [e for e in events if len(e["signs"]) >= 2],
    }
    for k, label in SIGN_LABELS.items():
        groups[f"含「{label}」"] = [e for e in events if k in e["signs"]]
    return {"start": start.isoformat(), "end": end.isoformat(), "universe": len(frames),
            "groups": {k: _summary(v) for k, v in groups.items()}}


def _pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x:+.{digits}%}"


def _ratio(x: float | None) -> str:
    return "—" if x is None else f"{x:.0%}"


def render(result: dict) -> str:
    lines = [
        "# 醞釀名單事件研究",
        "",
        f"期間 {result['start']} 至 {result['end']}｜{result['universe']} 檔",
        "",
        "| 分組 | 事件 | 5 日內轉強 | 10 日後平均漲跌 | 觸及轉強價 | 勝率 | 每筆平均 | 平均獲利 | 平均虧損 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, g in result["groups"].items():
        if not g["events"]:
            lines.append(f"| {name} | 0 | — | — | — | — | — | — | — |")
            continue
        lines.append(f"| {name} | {g['events']} | {_ratio(g['reversal_rate'])} | {_pct(g['fwd10_avg'])} | "
                     f"{_ratio(g['fill_rate'])} | {_ratio(g['win_rate'])} | {_pct(g['avg_return'], 2)} | "
                     f"{_pct(g['avg_win'], 2)} | {_pct(g['avg_loss'], 2)} |")
    lines += ["", "出場原因（醞釀名單 1 項以上）：" + "、".join(
        f"{k} {v}" for k, v in (result["groups"]["醞釀名單：1 項以上"].get("exit_reasons") or {}).items())]
    lines += ["", "注意：只涵蓋自選股（事後挑選，有選擇偏差）；同一檔連續符合只算第一天；同日觸及停損與目標以停損計"]
    return "\n".join(lines) + "\n"
