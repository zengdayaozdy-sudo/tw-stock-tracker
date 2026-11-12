"""回測的逐日訊號計畫：沿用正式引擎的函式，每天只用當天（含）以前已知的資料。

每個交易日 D（收盤後）：大盤燈號 → 三面向評分 → 族群強弱 → 流動性 → 名單 → 進場條件 → 參考價位。
與資金、持股有關的部分（張數、持股上限、族群上限）在模擬時處理（sim.py）。
消息面無法可靠回測，不參與（第 13 節）；處置／注意／全額交割沒有歷史資料，只用日均量過濾。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import pandas as pd

from engine.config import Settings
from engine.filters.groups import group_strength, weak_group_flags
from engine.filters.market_light import compute_light
from engine.filters.rebound import breadth_frame, compute_rebound
from engine.scoring.indicators import Indicator, StockFrames, chips, fundamental, technical
from engine.scoring.model import score_universe
from engine.signals.entry import entry_triggers
from engine.signals.levels import Levels, compute_levels
from engine.signals.regime import market_regime
from engine.signals.selection import select

HORIZONS = ("short", "long")


@dataclass
class Candidate:
    stock_id: str
    horizons: list[str]
    score: float              # 入選週期中的最高總分（排序用）
    triggers: dict[str, dict]
    levels: Levels
    size_factor: float = 1.0  # 反彈單縮小部位
    regime: str = "normal"


@dataclass
class DayPlan:
    date: date
    light: str
    rebound: str | None = None
    regime: str = "normal"
    candidates: list[Candidate] = field(default_factory=list)
    blocked: dict[str, list[str]] = field(default_factory=dict)
    scores: dict[str, dict[str, Any]] = field(default_factory=dict)   # {stock_id: {horizon: {date, total}}}


class CachedIndicators:
    """技術面、籌碼面只依過去資料滾動計算，整段歷史算一次；基本面依當天已公布的財報每天重算。"""

    def __init__(self, frames: dict[str, StockFrames], cfg: dict):
        self.daily: dict[str, dict[str, Indicator]] = {}
        for sid, f in frames.items():
            ind = technical(f.prices, cfg, f.benchmark)
            ind.update(chips(f.chips, f.prices, cfg, f.shares, f.pledge))
            self.daily[sid] = ind

    def __call__(self, stock: StockFrames, as_of: date, cfg: dict) -> dict[str, Indicator]:
        return {**self.daily[stock.stock_id], **fundamental(stock.financials, stock.valuations, as_of, cfg)}


class Planner:
    def __init__(self, settings: Settings, frames: dict[str, StockFrames], groups: dict[str, tuple[str, set[str]]],
                 series: dict[str, list[tuple[date, float]]], shares: dict[str, float | None]):
        self.settings = settings
        self.frames = frames
        self.groups = groups
        self.series = series
        self.shares = shares
        self.indicators = CachedIndicators(frames, settings.scoring)
        # 每日評分結果快取（stock_id, horizon, total）：調參只改風控、濾網時不必重新評分（tune 指令）
        self._scores: dict[date, list[tuple[str, str, float]]] = {}
        self.closes = {sid: f.prices["close"].astype(float).dropna() for sid, f in frames.items()}
        taiex = series.get("taiex") or []
        self.taiex = pd.Series({pd.Timestamp(d): v for d, v in taiex}, dtype=float).sort_index()
        # 反彈偵測：寬度指標只依過去資料滾動計算，整段歷史算一次
        rcfg = settings.filters.get("rebound") or {}
        self.rebound_cfg = rcfg if rcfg.get("enabled") else None
        if self.rebound_cfg:
            panel = pd.DataFrame(self.closes)
            self.breadth = breadth_frame(panel, rcfg["breadth_recovery"]["ma_days"])
            vols = pd.DataFrame({sid: f.prices["volume"].astype(float) for sid, f in frames.items()})
            self.volume = vols.sum(axis=1, min_count=1).dropna().sort_index()
            oi = series.get("foreign_tx_oi") or []
            self.foreign_oi = pd.Series({pd.Timestamp(d): v for d, v in oi}, dtype=float).sort_index()

    def retarget(self, settings: Settings) -> None:
        """換成另一組設定（評分與反彈寬度設定必須相同，評分快取才有效）。"""
        self.settings = settings

    def _frames_on(self, d: date) -> dict[str, StockFrames]:
        """市值依當天收盤 × 股數（股數為目前資料，屬近似）。"""
        out = {}
        for sid, f in self.frames.items():
            c = self.closes[sid]
            c = c[c.index <= pd.Timestamp(d)]
            cap = self.shares.get(sid) * float(c.iloc[-1]) if self.shares.get(sid) and not c.empty else None
            out[sid] = replace(f, market_cap=cap)
        return out

    def day(self, d: date) -> DayPlan:
        s = self.settings
        light = compute_light(self.series, d, s.filters["market_light"])
        rebound = (compute_rebound(self.taiex, self.breadth, self.volume, self.foreign_oi, d, self.rebound_cfg)
                   if self.rebound_cfg else None)
        regime = market_regime(light.light, rebound.state if rebound else None, s.risk)
        plan = DayPlan(d, light.light, rebound.state if rebound else None, regime["mode"])

        scored = self._scores.get(d)
        if scored is None:
            frames = self._frames_on(d)
            results = score_universe(s, frames, self.groups, d, indicator_fn=self.indicators)
            scored = [(r.stock_id, r.horizon, r.total) for r in results if r.status == "ok" and r.total is not None]
            self._scores[d] = scored
        totals = {h: {} for h in HORIZONS}
        for sid, horizon, total in scored:
            totals[horizon][sid] = total
            plan.scores.setdefault(sid, {})[horizon] = {"date": d.isoformat(), "total": total}

        # 族群強弱（範圍：回測的全部股票）
        gcfg = s.filters["group_strength"]
        scope = set(self.frames)
        strength = group_strength(self.groups, scope, self.closes, self.taiex, totals[gcfg["score_horizon"]], d, gcfg)
        weak = {f["stock_id"]: f["reason"] for f in weak_group_flags(strength, scope)}

        # 流動性：20 日均量
        liq = s.risk["liquidity"]
        lot = s.risk["position"]["lot_size"]
        illiquid = set()
        for sid, f in self.frames.items():
            v = f.prices["volume"][f.prices.index <= pd.Timestamp(d)].tail(20)
            if v.empty or float(v.mean()) / lot < liq["min_avg_volume_lots"]:
                illiquid.add(sid)

        selected = {h: select(totals[h], self.groups, s.enabled("top_percent_list"), s.scoring) for h in HORIZONS}
        for sid in sorted(set(selected["short"]) | set(selected["long"])):
            f = self.frames[sid]
            triggers = entry_triggers(f.prices, f.chips, d, s.risk, f.benchmark, regime["entry_conditions"])
            if not triggers:
                continue
            reasons = []
            if sid in illiquid:
                reasons.append("流動性不足")
            if regime["mode"] == "blocked":
                reasons.append("大盤燈號偏空")
            if sid in weak:
                reasons.append(weak[sid])
            if reasons:
                plan.blocked[sid] = reasons
                continue
            levels = compute_levels(f.prices, f.valuations, d, s.risk, bearish=regime["mode"] != "normal")
            if levels is None:
                continue
            horizons = [h for h in HORIZONS if sid in selected[h]]
            plan.candidates.append(Candidate(sid, horizons, max(totals[h][sid] for h in horizons), triggers, levels,
                                             regime["size_factor"], regime["mode"]))
        plan.candidates.sort(key=lambda c: (-c.score, c.stock_id))
        return plan
