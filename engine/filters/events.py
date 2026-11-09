"""事件行事曆（第 7.4 節）：財報、月營收、除權息、法說會、股東會。

除權息與股東會由證交所／櫃買公開資料自動帶入；法說會沒有公開 API，由網站手動登錄（events.source = manual）。
月營收與季報以法定期限推算：該期尚未公布才提示，已公布就不再提醒。
交易日以週一至週五扣除證交所公告的休市日計算。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


class TradingCalendar:
    def __init__(self, holidays: set[date] | None = None):
        self.holidays = holidays or set()

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def offset(self, start: date, end: date) -> int:
        """end 相對 start 的交易日差（end 在後為正）。非交易日的事件以其後第一個交易日計。"""
        while not self.is_trading_day(end):
            end += timedelta(days=1)
        if start == end:
            return 0
        step = 1 if end > start else -1
        n, d = 0, start
        while d != end:
            d += timedelta(days=step)
            if self.is_trading_day(d):
                n += step
        return n

    def add(self, start: date, days: int) -> date:
        """start 之後（days < 0 為之前）第 |days| 個交易日。"""
        d, step, left = start, (1 if days >= 0 else -1), abs(days)
        while left:
            d += timedelta(days=step)
            if self.is_trading_day(d):
                left -= 1
        return d


def statutory_events(ids: list[str], latest: dict[tuple[str, str], str], as_of: date,
                     horizon_days: int = 20) -> list[dict]:
    """依法定期限推算尚未公布的月營收、季報。latest：{(stock_id, period_type): 已公布最新期別}。"""
    out: list[dict] = []
    end = as_of + timedelta(days=horizon_days)
    start = as_of - timedelta(days=horizon_days)
    deadlines: list[tuple[date, str, str, str]] = []
    for y in (as_of.year - 1, as_of.year, as_of.year + 1):
        for m in range(1, 13):
            d = date(y, m, 10)
            prev = date(y, m, 1) - timedelta(days=1)
            deadlines.append((d, "month", f"{prev.year:04d}-{prev.month:02d}", "月營收公布期限"))
        deadlines += [
            (date(y, 3, 31), "quarter", f"{y - 1}Q4", "年報公布期限"),
            (date(y, 5, 15), "quarter", f"{y}Q1", "第一季財報公布期限"),
            (date(y, 8, 14), "quarter", f"{y}Q2", "第二季財報公布期限"),
            (date(y, 11, 14), "quarter", f"{y}Q3", "第三季財報公布期限"),
        ]
    for d, ptype, period, label in deadlines:
        if not start <= d <= end:
            continue
        event_type = "月營收" if ptype == "month" else "財報"
        for sid in ids:
            if (latest.get((sid, ptype)) or "") < period:
                out.append({"stock_id": sid, "date": d, "event_type": event_type, "note": f"{period} {label}"})
    return out


def index_review_events(ids: list[str], caps: dict[str, float | None], cfg: dict[str, Any],
                        cal: TradingCalendar) -> list[dict]:
    """MSCI 等指數季度調整：公布日與換股日（生效日前一個交易日收盤）。只套用在市值達門檻的股票。"""
    from datetime import date as _date

    min_cap = cfg.get("index_review_min_market_cap") or 0
    large = [s for s in ids if (caps.get(s) or 0) >= min_cap]
    out: list[dict] = []
    for r in cfg.get("index_reviews") or []:
        announce = r["announce"] if isinstance(r["announce"], _date) else _date.fromisoformat(str(r["announce"]))
        effective = r["effective"] if isinstance(r["effective"], _date) else _date.fromisoformat(str(r["effective"]))
        rebalance = cal.add(effective, -1)
        for sid in large:
            out.append({"stock_id": sid, "date": announce, "event_type": "指數調整公布", "note": r["name"]})
            out.append({"stock_id": sid, "date": rebalance, "event_type": "指數調整換股",
                        "note": f"{r['name']}（收盤換股）"})
    return out


def event_flags(events: list[dict], as_of: date, cal: TradingCalendar, cfg: dict[str, Any]) -> list[dict]:
    before, after = cfg["warn_days_before"], cfg["warn_days_after"]
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for e in sorted(events, key=lambda e: e["date"]):
        k = cal.offset(as_of, e["date"])
        if not -after <= k <= before:
            continue
        when = "今天" if k == 0 else f"{k} 個交易日後" if k > 0 else f"{-k} 個交易日前"
        reason = f"{e['event_type']} {e['date']:%m/%d}（{when}）"
        key = (e["stock_id"], reason)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "stock_id": e["stock_id"],
            "kind": "warn",
            "reason": reason,
            "detail": {"event_type": e["event_type"], "date": e["date"].isoformat(), "offset": k,
                       "note": e.get("note")},
        })
    return out
