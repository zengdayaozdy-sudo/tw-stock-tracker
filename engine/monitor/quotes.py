"""盤中即時報價。

免費模式：證交所基本市況報導（mis.twse.com.tw，可能延遲數秒）。最近成交價 z 為「-」
（最近一次撮合沒有成交）時改用最佳買價，也就是當下實際賣得掉的價格。
付費模式（finmind_realtime）：FinMind 即時報價端點與方案尚待確認（docs/open-questions.md #2），
確認前一律使用證交所資料，不憑記憶寫死端點。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from engine.config import TZ, Settings
from engine.sources.common import HttpClient, to_number

log = logging.getLogger(__name__)
EXCHANGE_PREFIX = {"twse": "tse", "tpex": "otc"}


@dataclass
class Quote:
    stock_id: str
    price: float
    source: str          # last：最近成交價／bid：最佳買價
    time: datetime | None
    prev_close: float | None = None


def _first(levels: Any) -> float | None:
    """'2150.0000_2145.0000_' → 2150.0"""
    if not levels:
        return None
    return to_number(str(levels).split("_")[0])


def parse_quotes(payload: dict) -> dict[str, Quote]:
    out: dict[str, Quote] = {}
    for m in payload.get("msgArray") or []:
        sid = str(m.get("c") or "").strip()
        if not sid:
            continue
        last = to_number(m.get("z"))
        price, source = (last, "last") if last else (_first(m.get("b")), "bid")
        if not price:
            continue  # 沒有成交也沒有委買（例如暫停交易）
        ts = m.get("tlong")
        when = datetime.fromtimestamp(int(ts) / 1000, TZ) if str(ts or "").isdigit() else None
        out[sid] = Quote(sid, price, source, when, to_number(m.get("y")))
    return out


class TwseRealtimeQuotes:
    name = "twse"

    def __init__(self, settings: Settings, http: HttpClient):
        self.url = settings.sources["twse"]["endpoints"]["realtime"]
        self.batch = settings.notify["intraday"]["batch_size"]
        self.http = http

    def fetch(self, stocks: dict[str, str]) -> dict[str, Quote]:
        """stocks：{stock_id: market（twse／tpex）}。"""
        out: dict[str, Quote] = {}
        items = sorted(stocks.items())
        for i in range(0, len(items), self.batch):
            chunk = items[i:i + self.batch]
            channels = "|".join(f"{EXCHANGE_PREFIX.get(m, 'tse')}_{sid}.tw" for sid, m in chunk)
            payload = self.http.get_json(self.url, params={"ex_ch": channels, "json": 1, "delay": 0})
            if not isinstance(payload, dict) or payload.get("rtcode") not in (None, "0000"):
                raise RuntimeError(f"證交所即時行情回傳異常：{str(payload)[:200]}")
            out.update(parse_quotes(payload))
        return out


def create_quotes(settings: Settings, http: HttpClient) -> TwseRealtimeQuotes:
    if settings.enabled("finmind_realtime"):
        log.warning("FinMind 即時報價端點尚待確認，暫用證交所即時行情")
    return TwseRealtimeQuotes(settings, http)
