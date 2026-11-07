"""證交所（上市）與櫃買中心（上櫃）免費公開資料。

全市場一次抓取，不受 FinMind 額度限制。官方欄位偶有改名，
解析時用 `pick` 依序嘗試多個名稱；欄位須以 `engine.cli probe` 在 GitHub Actions 實測確認。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from engine.config import Settings
from engine.sources.common import HttpClient, SourceError, parse_tw_date, pick, to_int, to_number


def _rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "aaData", "tables"):
            if isinstance(payload.get(key), list):
                return payload[key]
    raise SourceError(f"無法解析回傳內容：{str(payload)[:200]}")


def _table(payload: dict) -> list[dict]:
    """把 {fields: [...], data: [[...]]} 轉成 dict 列表。"""
    fields = payload.get("fields") or []
    return [dict(zip(fields, row, strict=False)) for row in payload.get("data") or []]


def _col(row: dict, *keywords: str) -> Any:
    """以關鍵字比對中文欄位名稱，例如 _col(row, '投信', '買賣超')。"""
    for name, value in row.items():
        if all(k in name for k in keywords):
            return value
    return None


def _is_common_stock(code: str | None) -> bool:
    """只保留 4 碼普通股，排除 ETF、權證、特別股等。"""
    return bool(code) and len(code) == 4 and code.isdigit() and not code.startswith("0")


@dataclass
class ExchangeClient:
    settings: Settings
    http: HttpClient

    def _url(self, market: str, key: str, **fmt: str) -> str:
        url = self.settings.sources[market]["endpoints"][key]
        return url.format(**fmt) if fmt else url

    def raw(self, market: str, key: str, **fmt: str) -> Any:
        return self.http.get_json(self._url(market, key, **fmt))

    # ---------- 每日行情 ----------
    def daily_prices(self, trade_date: date) -> list[dict]:
        out: list[dict] = []
        for r in _rows(self.raw("twse", "daily_all")):
            code = pick(r, "Code", "證券代號")
            if not _is_common_stock(code):
                continue
            d = parse_tw_date(pick(r, "Date", "日期")) or trade_date
            out.append(
                {
                    "stock_id": code,
                    "name": pick(r, "Name", "證券名稱"),
                    "market": "twse",
                    "date": d,
                    "open": to_number(pick(r, "OpeningPrice", "開盤價")),
                    "high": to_number(pick(r, "HighestPrice", "最高價")),
                    "low": to_number(pick(r, "LowestPrice", "最低價")),
                    "close": to_number(pick(r, "ClosingPrice", "收盤價")),
                    "volume": to_int(pick(r, "TradeVolume", "成交股數")),
                    "turnover": to_number(pick(r, "TradeValue", "成交金額")),
                }
            )
        for r in _rows(self.raw("tpex", "daily_all")):
            code = pick(r, "SecuritiesCompanyCode", "Code", "代號")
            if not _is_common_stock(code):
                continue
            d = parse_tw_date(pick(r, "Date", "資料日期")) or trade_date
            out.append(
                {
                    "stock_id": code,
                    "name": pick(r, "CompanyName", "Name", "名稱"),
                    "market": "tpex",
                    "date": d,
                    "open": to_number(pick(r, "Open", "開盤")),
                    "high": to_number(pick(r, "High", "最高")),
                    "low": to_number(pick(r, "Low", "最低")),
                    "close": to_number(pick(r, "Close", "收盤")),
                    "volume": to_int(pick(r, "TradingShares", "TradeVolume", "成交股數")),
                    "turnover": to_number(pick(r, "TransactionAmount", "TradeValue", "成交金額")),
                }
            )
        return out

    # ---------- 估值 ----------
    def valuations(self, trade_date: date) -> list[dict]:
        out: list[dict] = []
        for market, code_keys in (("twse", ("Code", "證券代號")), ("tpex", ("SecuritiesCompanyCode", "Code"))):
            for r in _rows(self.raw(market, "valuation_all")):
                code = pick(r, *code_keys)
                if not _is_common_stock(code):
                    continue
                out.append(
                    {
                        "stock_id": code,
                        "date": parse_tw_date(pick(r, "Date", "資料日期")) or trade_date,
                        "pe": to_number(pick(r, "PEratio", "PERatio", "本益比")),
                        "pb": to_number(pick(r, "PBratio", "PriceBookRatio", "股價淨值比")),
                        "dividend_yield": to_number(pick(r, "DividendYield", "YieldRatio", "殖利率(%)")),
                    }
                )
        return out

    # ---------- 三大法人 ----------
    def institutional(self, trade_date: date) -> list[dict]:
        out: list[dict] = []
        payload = self.raw("twse", "institutional", date=trade_date.strftime("%Y%m%d"))
        if isinstance(payload, dict) and payload.get("stat") not in (None, "OK"):
            raise SourceError(f"證交所三大法人：{payload.get('stat')}")
        for r in _table(payload):
            code = str(_col(r, "證券代號") or "").strip()
            if not _is_common_stock(code):
                continue
            out.append(
                {
                    "stock_id": code,
                    "date": trade_date,
                    "foreign_net": to_int(_col(r, "外陸資買賣超股數")),
                    "trust_net": to_int(_col(r, "投信買賣超股數")),
                    "dealer_net": to_int(_col(r, "自營商買賣超股數")),
                }
            )
        for r in _rows(self.raw("tpex", "institutional")):
            code = pick(r, "SecuritiesCompanyCode", "Code")
            if not _is_common_stock(code):
                continue
            out.append(
                {
                    "stock_id": code,
                    "date": parse_tw_date(pick(r, "Date")) or trade_date,
                    "foreign_net": to_int(
                        pick(r, "ForeignInvestorsIncludeMainlandAreaInvestors-Difference",
                             "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference")
                    ),
                    "trust_net": to_int(pick(r, "SecuritiesInvestmentTrustCompanies-Difference")),
                    "dealer_net": to_int(pick(r, "Dealers-Difference")),
                }
            )
        return out

    # ---------- 融資融券 ----------
    def margin(self, trade_date: date) -> list[dict]:
        out: list[dict] = []
        for market, code_keys in (("twse", ("Code", "股票代號")), ("tpex", ("SecuritiesCompanyCode", "Code"))):
            for r in _rows(self.raw(market, "margin_all")):
                code = pick(r, *code_keys)
                if not _is_common_stock(code):
                    continue
                out.append(
                    {
                        "stock_id": code,
                        "date": parse_tw_date(pick(r, "Date")) or trade_date,
                        "margin_balance": to_int(
                            pick(r, "MarginPurchaseTodayBalance", "MarginBalance", "融資今日餘額")
                        ),
                        "short_balance": to_int(pick(r, "ShortSaleTodayBalance", "ShortBalance", "融券今日餘額")),
                    }
                )
        return out

    # ---------- 公司資料與交易狀態 ----------
    def companies(self) -> list[dict]:
        out: list[dict] = []
        for market in ("twse", "tpex"):
            for r in _rows(self.raw(market, "company_info")):
                code = pick(r, "公司代號", "Code", "SecuritiesCompanyCode")
                if not _is_common_stock(code):
                    continue
                out.append(
                    {
                        "stock_id": code,
                        "name": pick(r, "公司簡稱", "Name", "CompanyAbbreviation"),
                        "market": market,
                        "industry": pick(r, "產業別", "Industry", "SecuritiesIndustryCode"),
                        "listed_date": parse_tw_date(pick(r, "上市日期", "上櫃日期", "ListingDate")),
                    }
                )
        return out

    def status_flags(self) -> dict[str, set[str]]:
        """回傳 {stock_id: {'disposition', 'attention'}}。全額交割股由公司資料或公告補充。"""
        flags: dict[str, set[str]] = {}
        for key, flag in (("disposition", "disposition"), ("attention", "attention")):
            for r in _rows(self.raw("twse", key)):
                code = str(pick(r, "Code", "證券代號") or "").strip()
                if _is_common_stock(code):
                    flags.setdefault(code, set()).add(flag)
        return flags
