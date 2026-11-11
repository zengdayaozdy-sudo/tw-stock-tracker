"""2026-11-11：晚間補抓、凌晨延遲排程的日期、融資融券網站版。"""

from datetime import date, datetime

import httpx

from engine.cli import _parse_date
from engine.config import TZ, load_settings
from engine.notify.runner import evening_message
from engine.sources.common import HttpClient
from engine.sources.exchanges import ExchangeClient

D = date(2026, 11, 11)


def _buy(sid, horizon="short", total=80):
    return {"stock_id": sid, "horizon": horizon, "entry_low": 100.0, "entry_high": 102.0, "stop_loss": 94.0,
            "take_profit": 120.0, "suggested_lots": 1, "reason": None,
            "detail": {"name": f"股{sid}", "score": {"total": total}}}


def test_evening_message_only_when_signals_change():
    reported = {"buys": ["2330:short"], "sells": []}
    assert evening_message(D, reported, [_buy("2330")], [], {}, {}) is None
    msg = evening_message(D, reported, [_buy("2317")], [], {"2330": {"reasons": ["重大利空：現金增資"]}},
                          {"2330": "台積電"})
    assert "【晚間更新】2026/11/11" in msg
    assert "新增買進訊號" in msg and "2317" in msg
    assert "取消買進訊號" in msg and "2330 台積電" in msg and "現金增資" in msg


def test_evening_message_new_holding_alert():
    reported = {"buys": [], "sells": []}
    sell = {**_buy("1503"), "reason": "跌破停損"}
    msg = evening_message(D, reported, [], [sell], {}, {})
    assert "新增持股提醒" in msg and "跌破停損" in msg


def test_late_night_run_processes_previous_day():
    assert _parse_date(None, datetime(2026, 11, 12, 1, 30, tzinfo=TZ)) == date(2026, 11, 11)
    assert _parse_date(None, datetime(2026, 11, 11, 16, 5, tzinfo=TZ)) == date(2026, 11, 11)
    assert _parse_date("2026-11-05") == date(2026, 11, 5)


def test_twse_margin_site_same_day():
    payload = {"stat": "OK", "date": "20261111", "tables": [
        {"title": "115年11月11日 信用交易統計", "fields": ["項目", "今日餘額"], "data": [["融資(交易單位)", "1"]]},
        {"title": "115年11月11日 融資融券彙總 (全部)",
         "fields": ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額",
                    "買進", "賣出", "現券償還", "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"],
         "data": [["00400A", "ETF", "1", "1", "0", "1", "1", "1", "0", "0", "0", "0", "0", "1", "0", " "],
                  ["2330", "台積電", "500", "400", "0", "15,000", "15,100", "1", "10", "5", "0", "120", "125",
                   "1", "3", " "]]}]}

    def handler(request: httpx.Request) -> httpx.Response:
        if "MI_MARGN" in str(request.url):
            return httpx.Response(200, json=payload)
        if "tpex_mainboard_margin_balance" in str(request.url):
            return httpx.Response(200, json=[{"Date": "1151111", "SecuritiesCompanyCode": "6488",
                                              "MarginPurchaseBalance": "100", "ShortSaleBalance": "2"}])
        return httpx.Response(404)

    ex = ExchangeClient(load_settings("free"), HttpClient(transport=httpx.MockTransport(handler), max_retries=0))
    rows = {r["stock_id"]: r for r in ex.margin(D)}
    assert rows["2330"] == {"stock_id": "2330", "date": D, "margin_balance": 15_100, "short_balance": 125}
    assert rows["6488"]["date"] == D and "00400A" not in rows
