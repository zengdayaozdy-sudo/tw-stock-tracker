"""FinMind API。免費模式逐檔查詢自選股；付費模式可用全市場與進階資料。"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from engine.config import Settings
from engine.sources.common import HttpClient, SourceError, to_int, to_number
from engine.sources.quota import QuotaTracker


@dataclass
class FinMindClient:
    settings: Settings
    http: HttpClient
    quota: QuotaTracker
    token: str | None = None

    @classmethod
    def create(cls, settings: Settings, http: HttpClient, on_record=None) -> FinMindClient:
        cfg = settings.sources["finmind"]
        limit = cfg["hourly_limit"][settings.plan.name]
        return cls(
            settings=settings,
            http=http,
            quota=QuotaTracker("finmind", limit, cfg.get("safety_margin", 0.9), on_record=on_record),
            token=os.environ.get("FINMIND_TOKEN") or None,
        )

    def dataset(self, key: str) -> str:
        return self.settings.sources["finmind"]["datasets"][key]

    def fetch(self, key: str, stock_id: str | None, start: date, end: date | None = None) -> list[dict]:
        params: dict[str, Any] = {"dataset": self.dataset(key), "start_date": start.isoformat()}
        if stock_id:
            params["data_id"] = stock_id
        if end:
            params["end_date"] = end.isoformat()
        if self.token:
            params["token"] = self.token
        self.quota.acquire()
        payload = self.http.get_json(self.settings.sources["finmind"]["base_url"], params=params)
        if not isinstance(payload, dict) or "data" not in payload:
            raise SourceError(f"FinMind 回傳格式不符：{str(payload)[:200]}")
        status = payload.get("status")
        if status not in (None, 200):
            raise SourceError(f"FinMind 錯誤 {status}：{payload.get('msg')}")
        return payload["data"]


# ---------- 轉換成資料表格式 ----------

def parse_prices(rows: list[dict]) -> list[dict]:
    return [
        {
            "stock_id": r["stock_id"],
            "date": date.fromisoformat(r["date"]),
            "open": to_number(r.get("open")),
            "high": to_number(r.get("max")),
            "low": to_number(r.get("min")),
            "close": to_number(r.get("close")),
            "volume": to_int(r.get("Trading_Volume")),
            "turnover": to_number(r.get("Trading_money")),
        }
        for r in rows
    ]


def revenue_published_date(year: int, month: int) -> date:
    """月營收法定公布期限為次月 10 日。"""
    if month == 12:
        return date(year + 1, 1, 10)
    return date(year, month + 1, 10)


def parse_month_revenue(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        y, m = int(r["revenue_year"]), int(r["revenue_month"])
        out.append(
            {
                "stock_id": r["stock_id"],
                "period_type": "month",
                "period": f"{y:04d}-{m:02d}",
                # 取「實際揭露日」與「法定期限」較晚者，避免回測偷看未來
                "published_date": max(date.fromisoformat(r["date"]), revenue_published_date(y, m)),
                "metrics": {"revenue": to_number(r.get("revenue"))},
            }
        )
    return out


def quarter_published_date(period_end: date) -> date:
    """季報法定公布期限：Q1 5/15、Q2 8/14、Q3 11/14、年報次年 3/31。"""
    y, m = period_end.year, period_end.month
    return {3: date(y, 5, 15), 6: date(y, 8, 14), 9: date(y, 11, 14), 12: date(y + 1, 3, 31)}[m]


def parse_statements(rows: list[dict]) -> list[dict]:
    """將長格式（type/value）財報整理成每季一筆。"""
    grouped: dict[tuple[str, str], dict[str, float | None]] = defaultdict(dict)
    for r in rows:
        grouped[(r["stock_id"], r["date"])][r["type"]] = to_number(r.get("value"))
    out = []
    for (stock_id, d), metrics in sorted(grouped.items()):
        end = date.fromisoformat(d)
        q = (end.month - 1) // 3 + 1
        out.append(
            {
                "stock_id": stock_id,
                "period_type": "quarter",
                "period": f"{end.year}Q{q}",
                "published_date": quarter_published_date(end),
                "metrics": metrics,
            }
        )
    return out


def parse_per(rows: list[dict]) -> list[dict]:
    return [
        {
            "stock_id": r["stock_id"],
            "date": date.fromisoformat(r["date"]),
            "pe": to_number(r.get("PER")),
            "pb": to_number(r.get("PBR")),
            "dividend_yield": to_number(r.get("dividend_yield")),
        }
        for r in rows
    ]


INSTITUTION_NAMES = {
    "Foreign_Investor": "foreign_net",
    "Foreign_Dealer_Self": "foreign_net",
    "Investment_Trust": "trust_net",
    "Dealer_self": "dealer_net",
    "Dealer_Hedging": "dealer_net",
}


def parse_institutional(rows: list[dict]) -> list[dict]:
    acc: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"foreign_net": 0, "trust_net": 0, "dealer_net": 0}
    )
    for r in rows:
        col = INSTITUTION_NAMES.get(r.get("name", ""))
        if not col:
            continue
        acc[(r["stock_id"], r["date"])][col] += (to_int(r.get("buy")) or 0) - (to_int(r.get("sell")) or 0)
    return [
        {"stock_id": s, "date": date.fromisoformat(d), **v} for (s, d), v in sorted(acc.items())
    ]


def parse_margin(rows: list[dict]) -> list[dict]:
    return [
        {
            "stock_id": r["stock_id"],
            "date": date.fromisoformat(r["date"]),
            "margin_balance": to_int(r.get("MarginPurchaseTodayBalance")),
            "short_balance": to_int(r.get("ShortSaleTodayBalance")),
        }
        for r in rows
    ]


def parse_shareholding(rows: list[dict]) -> list[dict]:
    return [
        {
            "stock_id": r["stock_id"],
            "date": date.fromisoformat(r["date"]),
            "foreign_holding_pct": to_number(r.get("ForeignInvestmentSharesRatio")),
        }
        for r in rows
    ]
