"""交易模擬（backtrader）：依逐日訊號計畫下單，套用與正式系統相同的風控與賣出規則。

成交規則（避免偷看未來）：
- D 日收盤後產生訊號，D+1 日才成交
- 買進：限價單＝買進區間上緣，D+1 開盤低於限價以開盤成交，否則盤中觸及限價以限價成交；沒成交就取消（不追高）
- 停損：持股期間掛停損單，盤中觸及即成交，跳空跌破以開盤成交；大盤偏空時停損收緊並改單
- 移動停利、達停利目標、分數跌出門檻、持有過久：D 日收盤判斷，D+1 開盤賣出
- 手續費（最低 20 元、無條件捨去）與證交稅（賣出）依 risk.yaml
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import backtrader as bt
import pandas as pd

from engine.backtest.plan import DayPlan
from engine.portfolio.monitor import check_holding, effective_stop
from engine.signals.levels import round_tick
from engine.signals.sizing import position_size


class TwStockCommission(bt.CommInfoBase):
    """台股手續費：成交金額 × 費率 × 折扣，無條件捨去，最低 min_fee；賣出另收證交稅。"""

    params = (
        ("stocklike", True),
        ("commtype", bt.CommInfoBase.COMM_FIXED),
        ("rate", 0.001425),
        ("discount", 1.0),
        ("min_fee", 20),
        ("tax", 0.003),
    )

    def _getcommission(self, size, price, pseudoexec):
        value = abs(size) * price
        fee = max(self.p.min_fee, math.floor(value * self.p.rate * self.p.discount))
        if size < 0:
            fee += math.floor(value * self.p.tax)
        return fee


def build_feed(prices: pd.DataFrame, calendar: pd.DatetimeIndex) -> bt.feeds.PandasData:
    """對齊共同交易日曆；停牌或尚未上市的日子以前一日收盤補齊、成交量 0（訊號計畫不會在這些日子產生）。"""
    p = prices[["open", "high", "low", "close", "volume"]].astype(float).reindex(calendar)
    close = p["close"].ffill().bfill()
    for col in ("open", "high", "low"):
        p[col] = p[col].fillna(close)
    p["close"] = close
    p["volume"] = p["volume"].fillna(0)
    return bt.feeds.PandasData(dataname=p, openinterest=None)


class PlanStrategy(bt.Strategy):
    params = (
        ("plans", None),          # {date: DayPlan}
        ("risk", None),
        ("groups_of", None),      # {stock_id: [族群名稱]}：計算族群曝險
        ("allow_odd", True),
        ("closes", None),         # {stock_id: pd.Series}：實際收盤（不含補齊的日子）
        ("names", None),
    )

    def __init__(self):
        self.by_id = {d._name: d for d in self.datas}
        self.book: dict[str, dict[str, Any]] = {}       # 持股資訊
        self.pending: dict[str, tuple[Any, Any]] = {}   # 尚未成交的買單
        self.stop_orders: dict[str, Any] = {}          # 目前有效的停損單
        self.stop_refs: dict[str, set[int]] = {}       # 每檔所有停損單的編號（notify_order 收到的是複本，用 ref 比對）
        self.exit_reason: dict[str, str] = {}
        self.equity: list[tuple[date, float]] = []
        self.exposure: list[tuple[date, float]] = []  # 每日持股市值佔總資產比例
        self.trades: list[dict[str, Any]] = []
        self.skipped: dict[str, int] = {"資金不足": 0, "張數為 0": 0, "限價未成交": 0}

    # ---------- 每日 ----------
    def next(self):
        d = self.datas[0].datetime.date(0)
        value = self.broker.getvalue()
        self.equity.append((d, value))
        self.exposure.append((d, 1 - self.broker.getcash() / value if value else 0.0))
        # 前一天的限價買單若今天沒成交就取消（不追價）
        for sid, (order, _c) in list(self.pending.items()):
            if order.alive():
                self.cancel(order)
                self.skipped["限價未成交"] += 1
            self.pending.pop(sid, None)

        plan: DayPlan | None = self.p.plans.get(d)
        if plan is None:
            return
        bearish = plan.light == "bearish"
        risk = self.p.risk
        self._check_exits(d, plan, bearish, risk)
        self._enter(d, plan, risk)

    def _closes_since(self, sid: str, start: date, d: date) -> list[tuple[date, float]]:
        s = self.p.closes[sid]
        s = s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(d))]
        return [(t.date(), float(v)) for t, v in s.items()]

    def _check_exits(self, d: date, plan: DayPlan, bearish: bool, risk: dict) -> None:
        for sid, info in list(self.book.items()):
            data = self.by_id[sid]
            pos = self.getposition(data)
            if pos.size <= 0 or info.get("exiting"):
                continue
            h = {"stock_id": sid, "shares": pos.size, "avg_cost": info["cost_per_share"],
                 "opened_date": info["opened"], "stop_loss": info["stop"], "take_profit": info["target"],
                 "highest_close": info["highest"]}
            res = check_holding(h, self._closes_since(sid, info["opened"], d), plan.scores.get(sid, {}), [], d,
                                bearish, risk)
            if res is None:
                continue
            info["highest"] = res["highest"]
            # 回測中「達停利目標」也直接出場（實盤可改用移動停利續抱）；收盤跌破停損但停損單未成交時同樣出場
            exits = list(res["triggered"])
            if exits:
                info["exiting"] = True
                self.exit_reason[sid] = exits[0]
                self._cancel_stop(sid)
                self.close(data=data)
                continue
            # 大盤偏空時停損收緊：改掛新的停損單
            stop = effective_stop(h, bearish, risk)
            current = self.stop_orders.get(sid)
            if stop and current is not None and abs(current.created.price - stop) > 1e-9:
                self.cancel(current)
                self._place_stop(sid, data, pos.size, stop)
                info["stop_now"] = stop

    def _place_stop(self, sid: str, data, size: int, price: float) -> None:
        order = self.sell(data=data, size=size, exectype=bt.Order.Stop, price=price)
        self.stop_orders[sid] = order
        self.stop_refs.setdefault(sid, set()).add(order.ref)

    def _cancel_stop(self, sid: str) -> None:
        order = self.stop_orders.pop(sid, None)
        if order is not None and order.alive():
            self.cancel(order)

    def _group_exposure(self, sid: str) -> float:
        groups = self.p.groups_of.get(sid, [])
        if not groups:
            return 0.0
        total: dict[str, float] = {}
        for other in self.book:
            pos = self.getposition(self.by_id[other])
            value = pos.size * self.by_id[other].close[0]
            for g in self.p.groups_of.get(other, []):
                total[g] = total.get(g, 0.0) + value
        return max((total.get(g, 0.0) for g in groups), default=0.0)

    def _enter(self, d: date, plan: DayPlan, risk: dict) -> None:
        pos_cfg = risk["position"]
        lot = pos_cfg["lot_size"]
        capital = self.broker.getvalue()
        cash = self.broker.getcash()
        count = len(self.book) + len(self.pending)
        for c in plan.candidates:
            if c.stock_id in self.book or c.stock_id in self.pending:
                continue
            lv = c.levels
            size = position_size(capital, lv.entry_high, lv.stop_loss, pos_cfg, holdings_count=count,
                                 group_exposure=self._group_exposure(c.stock_id))
            shares = size["lots"] * lot or (size["odd_shares"] if self.p.allow_odd else 0)
            if shares <= 0:
                self.skipped["張數為 0"] += 1
                continue
            need = shares * lv.entry_high * (1 + risk["costs"]["commission_rate"]) + risk["costs"]["min_commission"]
            if need > cash:
                self.skipped["資金不足"] += 1
                continue
            order = self.buy(data=self.by_id[c.stock_id], size=shares, exectype=bt.Order.Limit, price=lv.entry_high)
            self.pending[c.stock_id] = (order, c)
            cash -= need
            count += 1

    # ---------- 成交回報 ----------
    def notify_order(self, order):
        if order.status not in (order.Completed,):
            return
        sid = order.data._name
        ex = order.executed
        d = bt.num2date(ex.dt).date()
        if order.isbuy():
            _o, c = self.pending.pop(sid, (None, None))
            if c is None:
                return
            cost_total = ex.price * ex.size + ex.comm
            stop = min(c.levels.stop_loss, round_tick(ex.price * 0.999, "down"))
            self.book[sid] = {
                "opened": d, "shares": ex.size, "cost_total": cost_total, "cost_per_share": cost_total / ex.size,
                "stop": stop, "target": c.levels.take_profit, "highest": ex.price, "horizons": c.horizons,
                "entry_price": ex.price, "triggers": list(c.triggers), "score": c.score,
            }
            self._place_stop(sid, order.data, ex.size, stop)
            return
        info = self.book.pop(sid, None)
        if info is None:
            return
        if order.ref in self.stop_refs.get(sid, set()):
            reason = "stop_loss"
            self.exit_reason.pop(sid, None)
        else:
            reason = self.exit_reason.pop(sid, "end")
        self._cancel_stop(sid)
        self.stop_refs.pop(sid, None)
        size = abs(ex.size)
        proceeds = ex.price * size - ex.comm
        pnl = proceeds - info["cost_total"]
        self.trades.append({
            "stock_id": sid, "name": (self.p.names or {}).get(sid), "opened": info["opened"].isoformat(),
            "closed": d.isoformat(), "days": (d - info["opened"]).days, "shares": size,
            "entry": round(info["entry_price"], 2), "exit": round(ex.price, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl / info["cost_total"], 6),
            "reason": reason, "horizons": info["horizons"], "triggers": info["triggers"],
        })

    def stop(self):
        # 期末仍持有的部位以最後收盤價計算未實現損益（不計入已實現交易）
        self.open_positions = []
        for sid, info in self.book.items():
            data = self.by_id[sid]
            pos = self.getposition(data)
            if pos.size > 0:
                self.open_positions.append({
                    "stock_id": sid, "opened": info["opened"].isoformat(), "shares": pos.size,
                    "entry": round(info["entry_price"], 2), "last": float(data.close[0]),
                    "unrealized_pct": round(float(data.close[0]) * pos.size / info["cost_total"] - 1, 6),
                })


def simulate(plans: dict[date, DayPlan], prices: dict[str, pd.DataFrame], calendar: pd.DatetimeIndex,
             settings, groups_of: dict[str, list[str]], names: dict[str, str] | None = None) -> dict[str, Any]:
    bt_cfg = settings.backtest
    risk = settings.risk
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(float(bt_cfg["initial_capital"]))
    costs = risk["costs"]
    cerebro.broker.addcommissioninfo(TwStockCommission(
        rate=costs["commission_rate"], discount=costs.get("commission_discount", 1.0),
        min_fee=costs["min_commission"], tax=costs["tax_rate"]))
    if bt_cfg.get("slippage"):
        cerebro.broker.set_slippage_perc(bt_cfg["slippage"])
    closes = {}
    for sid in sorted(prices):
        cerebro.adddata(build_feed(prices[sid], calendar), name=sid)
        closes[sid] = prices[sid]["close"].astype(float).dropna()
    cerebro.addstrategy(PlanStrategy, plans=plans, risk=risk, groups_of=groups_of,
                        allow_odd=bt_cfg.get("allow_odd_lots", True), closes=closes, names=names or {})
    strat = cerebro.run(runonce=False)[0]
    return {"equity": strat.equity, "exposure": strat.exposure, "trades": strat.trades,
            "open_positions": strat.open_positions,
            "skipped": strat.skipped, "final_value": cerebro.broker.getvalue()}
