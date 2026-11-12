"""證交所（上市）與櫃買中心（上櫃）免費公開資料。

全市場一次抓取，不受 FinMind 額度限制。官方欄位偶有改名，
解析時用 `pick` 依序嘗試多個名稱；欄位須以 `engine.cli probe` 在 GitHub Actions 實測確認。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from functools import lru_cache
from typing import Any

import yaml

from engine.config import CONFIG_DIR, TZ, Settings
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


def _col(row: dict, *keywords: str, exclude: tuple[str, ...] = ()) -> Any:
    """以關鍵字比對欄位名稱。完全相符優先，其次為包含全部關鍵字且不含排除字的欄位。"""
    if len(keywords) == 1 and keywords[0] in row:
        return row[keywords[0]]
    for name, value in row.items():
        key = name.strip().lower()
        if all(k.lower() in key for k in keywords) and not any(x.lower() in key for x in exclude):
            return value
    return None


def industry_name(code: Any) -> str | None:
    """證交所／櫃買產業代碼轉名稱（對照表在 config/industries.yaml），查無則保留代碼。"""
    if code in (None, ""):
        return None
    code = str(code).strip().zfill(2)
    return _industries().get(code, code)


@lru_cache(maxsize=1)
def _industries() -> dict[str, str]:
    with (CONFIG_DIR / "industries.yaml").open(encoding="utf-8") as f:
        return {str(k).zfill(2): v for k, v in (yaml.safe_load(f) or {}).items()}


def shares_outstanding(capital: Any, par: Any) -> int | None:
    """實收資本額 ÷ 每股面額 = 發行股數（面額字串如「新台幣 10.0000元」）。"""
    cap = to_number(capital)
    m = re.search(r"[\d.]+", str(par or ""))
    face = float(m.group()) if m else 10.0
    if not cap or face <= 0:
        return None
    return round(cap / face)


def _is_common_stock(code: str | None) -> bool:
    """只保留 4 碼普通股，排除 ETF、權證、特別股等。"""
    return bool(code) and len(code) == 4 and code.isdigit() and not code.startswith("0")


@dataclass
class ExchangeClient:
    settings: Settings
    http: HttpClient
    _openapi_date: date | None = field(default=None, init=False, repr=False)
    last_errors: list[str] = field(default_factory=list)

    def _url(self, market: str, key: str, **fmt: str) -> str:
        url = self.settings.sources[market]["endpoints"][key]
        return url.format(**fmt) if fmt else url

    def raw(self, market: str, key: str, **fmt: str) -> Any:
        return self.http.get_json(self._url(market, key, **fmt))

    def _twse_site(self, key: str, trade_date: date, table_title: str | None = None) -> list[dict] | None:
        """證交所網站版（當天更新）。查無當日資料或格式不符時回傳 None，由呼叫端改用 OpenAPI。"""
        if key not in self.settings.sources["twse"]["endpoints"]:
            return None
        try:
            payload = self.raw("twse", key, date=trade_date.strftime("%Y%m%d"))
        except SourceError:
            return None
        if not isinstance(payload, dict) or payload.get("stat") not in (None, "OK"):
            return None
        if table_title:
            for t in payload.get("tables") or []:
                if table_title in (t.get("title") or "") and t.get("data"):
                    return [dict(zip(t.get("fields") or [], row, strict=False)) for row in t["data"]]
            return None
        return _table(payload) or None

    def openapi_date(self) -> date | None:
        """證交所 OpenAPI 目前資料的交易日（OpenAPI 各資料集同步更新，融資券沒有日期欄位，以此為準）。"""
        if self._openapi_date is None:
            dates = [parse_tw_date(pick(r, "Date")) for r in _rows(self.raw("twse", "daily_all"))]
            self._openapi_date = max((d for d in dates if d), default=None)
        return self._openapi_date

    # ---------- 每日行情 ----------
    def daily_prices(self, trade_date: date) -> list[dict]:
        out: list[dict] = []
        site = self._twse_site("daily_all_site", trade_date, table_title="每日收盤行情")
        if site is not None:
            twse_rows, twse_date = site, trade_date
        else:
            twse_rows, twse_date = _rows(self.raw("twse", "daily_all")), None
        for r in twse_rows:
            code = str(pick(r, "Code", "證券代號") or "").strip()
            if not _is_common_stock(code):
                continue
            d = twse_date or parse_tw_date(pick(r, "Date", "日期")) or trade_date
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
            site = self._twse_site("valuation_site", trade_date) if market == "twse" else None
            rows = site if site is not None else _rows(self.raw(market, "valuation_all"))
            for r in rows:
                code = str(pick(r, *code_keys) or "").strip()
                if not _is_common_stock(code):
                    continue
                d = trade_date if site is not None else parse_tw_date(pick(r, "Date", "資料日期"))
                if d is None:
                    continue  # 沒有日期無法確認是哪一天的資料，寧可不存
                out.append(
                    {
                        "stock_id": code,
                        "date": d,
                        "pe": to_number(pick(r, "PEratio", "PriceEarningRatio", "PERatio", "本益比")),
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
                    "dealer_net": to_int(_col(r, "自營商買賣超股數", exclude=("外資", "自行", "避險"))),
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
                        pick(r, "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference")
                        or _col(r, "foreign", "difference", exclude=("dealer", "total"))
                    ),
                    "trust_net": to_int(_col(r, "trust", "difference")),
                    "dealer_net": to_int(
                        _col(r, "dealers", "difference", exclude=("foreign", "proprietary", "hedge"))
                    ),
                }
            )
        return out

    # ---------- 融資融券 ----------
    def _twse_margin_site(self, trade_date: date) -> list[dict] | None:
        """證交所網站版融資融券彙總（當天晚間公布）；還沒公布或格式不符時回傳 None。"""
        try:
            payload = self.raw("twse", "margin_site", date=trade_date.strftime("%Y%m%d"))
        except SourceError:
            return None
        if not isinstance(payload, dict) or payload.get("stat") != "OK" \
                or str(payload.get("date")) != trade_date.strftime("%Y%m%d"):
            return None
        for table in payload.get("tables") or []:
            fields = table.get("fields") or []
            if "融資融券彙總" not in str(table.get("title")) or fields.count("今日餘額") < 2:
                continue
            first = fields.index("今日餘額")
            second = fields.index("今日餘額", first + 1)
            out = []
            for row in table.get("data") or []:
                code = str(row[0]).strip()
                if _is_common_stock(code) and len(row) > second:
                    out.append({"stock_id": code, "date": trade_date, "market": "twse",
                                "margin_balance": to_int(row[first]), "short_balance": to_int(row[second])})
            return out or None
        return None

    def margin(self, trade_date: date) -> list[dict]:
        """融資融券。上市先用證交所網站版（當天晚間公布）；還沒公布時改用 OpenAPI，
        OpenAPI 沒有日期欄位，以 OpenAPI 每日行情的日期標記（通常是前一交易日），不會把舊資料標成今天。"""
        out: list[dict] = []
        site = self._twse_margin_site(trade_date)
        if site:
            out.extend(site)
        markets = (("tpex", ("SecuritiesCompanyCode", "Code")),) if site else \
            (("twse", ("Code", "股票代號")), ("tpex", ("SecuritiesCompanyCode", "Code")))
        for market, code_keys in markets:
            fallback = self.openapi_date() if market == "twse" else None
            for r in _rows(self.raw(market, "margin_all")):
                code = str(pick(r, *code_keys) or "").strip()
                if not _is_common_stock(code):
                    continue
                d = parse_tw_date(pick(r, "Date")) or fallback
                if d is None:
                    continue
                out.append(
                    {
                        "stock_id": code,
                        "date": d,
                        "market": market,
                        "margin_balance": to_int(
                            pick(r, "融資今日餘額", "MarginPurchaseBalance", "MarginPurchaseTodayBalance")
                        ),
                        "short_balance": to_int(pick(r, "融券今日餘額", "ShortSaleBalance", "ShortSaleTodayBalance")),
                    }
                )
        return out

    # ---------- 借券賣出餘額 ----------
    def securities_lending(self, trade_date: date) -> list[dict]:
        """借券賣出餘額（股）。上市用證交所網站版（當天），上櫃用櫃買 OpenAPI；日期不是 trade_date 的不收。"""
        out: list[dict] = []
        payload = self.raw("twse", "sbl_site", date=trade_date.strftime("%Y%m%d"))
        ymd = trade_date.strftime("%Y%m%d")
        if isinstance(payload, dict) and payload.get("stat") == "OK" and str(payload.get("date")) == ymd:
            fields = payload.get("fields") or []
            if "當日餘額" in fields:
                idx = fields.index("當日餘額")
                for row in payload.get("data") or []:
                    code = str(row[0]).strip()
                    if _is_common_stock(code) and len(row) > idx:
                        out.append({"stock_id": code, "date": trade_date, "sbl_balance": to_int(row[idx])})
        for r in _rows(self.raw("tpex", "sbl")):
            code = str(pick(r, "SecuritiesCompanyCode") or "").strip()
            if not _is_common_stock(code) or parse_tw_date(pick(r, "Date")) != trade_date:
                continue
            out.append({"stock_id": code, "date": trade_date,
                        "sbl_balance": to_int(pick(r, "SecuritiesBorrowingBalanceOfTheMarketDay"))})
        return out

    # ---------- 董監質押比 ----------
    def pledge_ratios(self) -> tuple[dict[str, float], date | None]:
        """董監質權設定占持有股數彙總表：每列是一個級距，公司名稱欄是「代號 名稱 比例」多行文字。"""
        ratios: dict[str, float] = {}
        latest: date | None = None
        for market in ("twse", "tpex"):
            for r in _rows(self.raw(market, "pledge")):
                d = parse_tw_date(pick(r, "出表日期", "Date"))
                if d and (latest is None or d > latest):
                    latest = d
                text = str(pick(r, "公司名稱", "CompanyName") or "")
                for line in text.splitlines():
                    parts = line.split()
                    if len(parts) >= 3 and _is_common_stock(parts[0]):
                        pct = to_number(parts[-1])
                        if pct is not None:
                            ratios[parts[0]] = pct
        return ratios, latest

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
                        "industry": industry_name(pick(r, "產業別", "SecuritiesIndustryCode")),
                        "listed_date": parse_tw_date(pick(r, "上市日期", "上櫃日期", "DateOfListing")),
                        "shares": shares_outstanding(
                            pick(r, "實收資本額", "Paidin.Capital.NTDollars"),
                            pick(r, "普通股每股面額", "ParValueOfCommonStock"),
                        ),
                    }
                )
        return out

    def status_flags(self, trade_date: date | None = None) -> dict[str, set[str]]:
        """回傳 {stock_id: {'disposition', 'attention', 'full_delivery'}}。

        處置股只計入處置期間涵蓋 trade_date 者；任一來源失敗不影響其他來源。
        """
        flags: dict[str, set[str]] = {}
        errors: list[str] = []

        def add(code: Any, flag: str) -> None:
            code = str(code or "").strip()
            if _is_common_stock(code):
                flags.setdefault(code, set()).add(flag)

        def safe(label: str, fn) -> None:
            try:
                fn()
            except Exception as e:  # 單一公告格式異動不影響其他來源
                errors.append(f"{label}：{e}")

        def twse_disposition() -> None:
            for r in _rows(self.raw("twse", "disposition")):
                if _in_period(pick(r, "DispositionPeriod", "處置期間"), trade_date):
                    add(pick(r, "Code", "證券代號"), "disposition")

        def twse_attention() -> None:
            for r in _rows(self.raw("twse", "attention")):
                add(pick(r, "Code", "證券代號"), "attention")

        def twse_full() -> None:
            for r in _rows(self.raw("twse", "full_delivery")):
                add(pick(r, "Code", "證券代號"), "full_delivery")

        def tpex_disposition() -> None:
            for r in _rows(self.raw("tpex", "disposition")):
                if _in_period(pick(r, "DispositionPeriod"), trade_date):
                    add(pick(r, "SecuritiesCompanyCode", "Code"), "disposition")

        def tpex_attention() -> None:
            for r in _rows(self.raw("tpex", "attention")):
                add(pick(r, "SecuritiesCompanyCode", "Code"), "attention")

        def tpex_mode() -> None:
            for r in _rows(self.raw("tpex", "trading_mode")):
                r = _strip_keys(r)
                if _is_yes(r.get("AlteredTrading")):
                    add(pick(r, "SecuritiesCompanyCode", "Code"), "full_delivery")

        safe("上市處置股", twse_disposition)
        safe("上市注意股", twse_attention)
        safe("上市全額交割股", twse_full)
        safe("上櫃處置股", tpex_disposition)
        safe("上櫃注意股", tpex_attention)
        safe("上櫃全額交割股", tpex_mode)
        if errors and not flags:
            raise SourceError("；".join(errors))
        self.last_errors = errors
        return flags

    # ---------- 第 4 階段：重大訊息、事件、休市日 ----------
    def announcements(self) -> list[dict]:
        """上市＋上櫃每日重大訊息（官方只提供最近一日）。"""
        out: list[dict] = []
        for market, code_keys in (("twse", ("公司代號", "Code")), ("tpex", ("SecuritiesCompanyCode", "公司代號"))):
            for r in _rows(self.raw(market, "announcements")):
                r = _strip_keys(r)
                code = str(pick(r, *code_keys) or "").strip()
                if not _is_common_stock(code):
                    continue
                title = " ".join(str(pick(r, "主旨", "Subject") or "").split())
                if not title:
                    continue
                out.append(
                    {
                        "stock_id": code,
                        "published_at": _tw_datetime(pick(r, "發言日期"), pick(r, "發言時間")),
                        "source": f"{market}_announcement",
                        "title": title,
                        "body": str(pick(r, "說明") or "").strip()[:4000] or None,
                        "url": None,
                    }
                )
        return out

    def mops_announcements(self, day: date) -> list[dict]:
        """公開資訊觀測站當日重大訊息（上市＋上櫃全部，含前一日 17:30 以後）。"""
        cfg = self.settings.sources["mops"]
        payload = {"year": str(day.year - 1911), "month": f"{day.month:02d}", "day": f"{day.day:02d}"}
        resp = self.http.post_json(cfg["announcements"], payload)
        if not isinstance(resp, dict) or resp.get("code") != 200:
            raise SourceError(f"公開資訊觀測站回應異常：{str(resp)[:120]}")
        out: list[dict] = []
        for row in (resp.get("result") or {}).get("data") or []:
            if not isinstance(row, list) or len(row) < 5:
                continue
            code = str(row[2]).strip()
            params = row[5].get("parameters") if len(row) > 5 and isinstance(row[5], dict) else None
            if not _is_common_stock(code) or (params and params.get("marketKind") not in ("sii", "otc")):
                continue  # 只留上市、上櫃普通股
            title = " ".join(str(row[4] or "").split())
            if not title:
                continue
            out.append({
                "stock_id": code,
                "published_at": _tw_datetime(row[0], str(row[1]).replace(":", "")),
                "source": "mops",
                "title": title,
                "body": None,
                "url": None,
                "detail_params": params,
            })
        return out

    def mops_detail(self, params: dict) -> str | None:
        """重大訊息明細的「說明」欄。"""
        resp = self.http.post_json(self.settings.sources["mops"]["announcement_detail"], params)
        result = (resp or {}).get("result") or {}
        titles = [t.get("main") for t in result.get("titles") or []]
        rows = result.get("data") or []
        if not rows or "說明" not in titles:
            return None
        text = rows[0][titles.index("說明")]
        return str(text).strip()[:4000] or None

    def ex_dividend_events(self) -> list[dict]:
        out: list[dict] = []
        for r in _rows(self.raw("twse", "ex_dividend")):
            code = str(pick(r, "Code") or "").strip()
            d = parse_tw_date(pick(r, "Date"))
            if _is_common_stock(code) and d:
                out.append({"stock_id": code, "date": d, "event_type": "除權息", "note": f"除{pick(r, 'Exdividend') or ''}"})
        for r in _rows(self.raw("tpex", "ex_dividend")):
            code = str(pick(r, "SecuritiesCompanyCode") or "").strip()
            d = parse_tw_date(pick(r, "ExRrightsExDividendDate"))
            if _is_common_stock(code) and d:
                out.append({"stock_id": code, "date": d, "event_type": "除權息", "note": pick(r, "ExRrightsExDividend")})
        return out

    def shareholder_meetings(self) -> list[dict]:
        out: list[dict] = []
        for market in ("twse", "tpex"):
            for r in _rows(self.raw(market, "shareholder_meetings")):
                r = _strip_keys(r)
                code = str(pick(r, "公司代號") or "").strip()
                d = parse_tw_date(pick(r, "開會日期"))
                if _is_common_stock(code) and d:
                    kind = pick(r, "股東常(臨時)會") or "常會"
                    out.append({"stock_id": code, "date": d, "event_type": "股東會", "note": f"股東{kind}"})
        return out

    def month_revenue(self) -> list[dict]:
        """上市＋上櫃最新一期月營收彙總（全市場一次取得）：年增率、累計年增率。"""
        out: list[dict] = []
        for market in ("twse", "tpex"):
            for r in _rows(self.raw(market, "month_revenue_all")):
                code = str(pick(r, "公司代號") or "").strip()
                if not _is_common_stock(code):
                    continue
                out.append({
                    "stock_id": code,
                    "period": str(pick(r, "資料年月") or ""),
                    "industry": pick(r, "產業別"),
                    "revenue": to_number(pick(r, "營業收入-當月營收")),
                    "month_yoy": to_number(pick(r, "營業收入-去年同月增減(%)")),
                    "cum_yoy": to_number(pick(r, "累計營業收入-前期比較增減(%)")),
                })
        return out

    def holidays(self) -> set[date]:
        """休市日（不含「開始交易日」「最後交易日」等仍有交易的日子）。"""
        out: set[date] = set()
        for r in _rows(self.raw("twse", "holidays")):
            d = parse_tw_date(pick(r, "Date"))
            text = f"{pick(r, 'Name') or ''}{pick(r, 'Description') or ''}"
            if d and "開始交易" not in text and "最後交易" not in text:
                out.add(d)
        return out

    def taiex_month(self, month: date) -> list[dict]:
        """發行量加權股價指數，一次一個月。"""
        payload = self.raw("twse", "taiex_history", date=month.strftime("%Y%m01"))
        if isinstance(payload, dict) and payload.get("stat") not in (None, "OK"):
            return []
        out = []
        for r in _table(payload):
            d = parse_tw_date(_col(r, "日期"))
            close = to_number(_col(r, "收盤指數"))
            if d and close:
                out.append({"series": "taiex", "date": d, "value": close})
        return out


def _strip_keys(row: dict) -> dict:
    """官方欄位名稱偶有前後空白（例如「主旨 」）。"""
    return {str(k).strip(): v for k, v in row.items()}


def _is_yes(value: Any) -> bool:
    return str(value or "").strip().upper() in {"Y", "Ｙ", "是"}


def _tw_datetime(day: Any, hhmmss: Any) -> datetime:
    d = parse_tw_date(day) or datetime.now(TZ).date()
    t = str(hhmmss or "0").strip().zfill(6)[-6:]
    try:
        clock = time(int(t[:2]), int(t[2:4]), int(t[4:]))
    except ValueError:
        clock = time(0)
    return datetime.combine(d, clock, tzinfo=TZ)


def _in_period(text: Any, on: date | None) -> bool:
    """處置期間如「115/11/09～115/11/13」或「1151105~1151111」；無法解析時視為有效。"""
    if on is None or not text:
        return True
    parts = re.split(r"[~～]", str(text))
    if len(parts) != 2:
        return True
    start, end = parse_tw_date(parts[0].strip()), parse_tw_date(parts[1].strip())
    if not start or not end:
        return True
    return start <= on <= end
