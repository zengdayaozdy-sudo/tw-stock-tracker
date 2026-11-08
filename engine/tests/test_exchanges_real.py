"""以 2026-11-08 probe 實際取得的欄位格式驗證解析（2026-11-06 交易日資料）。"""

from datetime import date

import httpx

from engine.config import load_settings
from engine.sources.common import HttpClient
from engine.sources.exchanges import ExchangeClient, industry_name, shares_outstanding

D = date(2026, 11, 6)

PAYLOADS = {
    "STOCK_DAY_ALL": [
        {"Date": "1151106", "Code": "00400A", "Name": "主動國泰動能高息", "TradeVolume": "92784561",
         "TradeValue": "1038797013", "OpeningPrice": "10.78", "HighestPrice": "11.48", "LowestPrice": "10.78",
         "ClosingPrice": "11.18", "Change": "0.0000", "Transaction": "20248"},
        {"Date": "1151106", "Code": "2330", "Name": "台積電", "TradeVolume": "30000000", "TradeValue": "45000000000",
         "OpeningPrice": "1,500.00", "HighestPrice": "1,520.00", "LowestPrice": "1,490.00",
         "ClosingPrice": "1,510.00", "Change": "10.0000", "Transaction": "50000"},
    ],
    "tpex_mainboard_daily_close_quotes": [
        {"Date": "1151106", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "Close": "400.50",
         "Change": "-0.53 ", "Open": "398.00", "High": "405.00", "Low": "397.00", "TradingShares": "1994048",
         "TransactionAmount": "792374158"},
    ],
    "BWIBBU_ALL": [{"Date": "1151106", "Code": "1101", "Name": "台泥", "PEratio": "", "DividendYield": "2.99",
                    "PBratio": "0.87"}],
    "tpex_mainboard_peratio_analysis": [
        {"Date": "1151106", "SecuritiesCompanyCode": "1240", "CompanyName": "茂生農經", "PriceEarningRatio": "9.93",
         "DividendPerShare": "0.50000000", "YieldRatio": "0.94", "PriceBookRatio": "1.57"}],
    "MI_MARGN": [{"股票代號": "2330", "股票名稱": "台積電", "融資今日餘額": "15,932", "融券今日餘額": "120"}],
    "tpex_mainboard_margin_balance": [
        {"Date": "1151106", "SecuritiesCompanyCode": "6488", "MarginPurchaseBalance": "18608",
         "ShortSaleBalance": "103"}],
    "T86": {"stat": "OK", "fields": ["證券代號", "證券名稱", "外陸資買賣超股數(不含外資自營商)",
                                    "外資自營商買賣超股數", "投信買賣超股數", "自營商買賣超股數",
                                    "自營商買賣超股數(自行買賣)", "自營商買賣超股數(避險)", "三大法人買賣超股數"],
            "data": [["2330", "台積電", "1,000,000", "0", "50,000", "-20,000", "-5,000", "-15,000", "1,030,000"]]},
    "tpex_3insti_daily_trading": [
        {"Date": "1151106", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶",
         "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference": "603000",
         "ForeignDealers-Difference": "0",
         "SecuritiesInvestmentTrustCompanies-Difference": "12000",
         "Dealers (Proprietary)-Difference": "1000", "Dealers (Hedge)-Difference": "-500",
         "Dealers-Difference": "500", "TotalDifference": "615500"}],
}


def _client() -> ExchangeClient:
    def handler(request: httpx.Request) -> httpx.Response:
        for key, payload in PAYLOADS.items():
            if key in str(request.url):
                return httpx.Response(200, json=payload)
        return httpx.Response(404)

    return ExchangeClient(load_settings("free"), HttpClient(transport=httpx.MockTransport(handler), max_retries=0))


def test_daily_prices_real_format():
    rows = {r["stock_id"]: r for r in _client().daily_prices(D)}
    assert set(rows) == {"2330", "6488"}  # ETF 排除
    assert rows["2330"]["date"] == D  # 民國 115 年
    assert rows["2330"]["close"] == 1510.0
    assert rows["6488"]["volume"] == 1994048


def test_valuations_real_format():
    rows = {r["stock_id"]: r for r in _client().valuations(D)}
    assert rows["1101"]["pe"] is None and rows["1101"]["pb"] == 0.87
    assert rows["1240"]["pe"] == 9.93 and rows["1240"]["dividend_yield"] == 0.94


def test_margin_real_format():
    rows = {r["stock_id"]: r for r in _client().margin(D)}
    assert rows["2330"]["margin_balance"] == 15932
    assert rows["6488"]["margin_balance"] == 18608 and rows["6488"]["short_balance"] == 103


def test_institutional_real_format():
    rows = {r["stock_id"]: r for r in _client().institutional(D)}
    assert rows["2330"] == {"stock_id": "2330", "date": D, "foreign_net": 1_000_000, "trust_net": 50_000,
                            "dealer_net": -20_000}
    assert rows["6488"]["foreign_net"] == 603000
    assert rows["6488"]["trust_net"] == 12000
    assert rows["6488"]["dealer_net"] == 500


def test_industry_and_shares():
    assert industry_name("24") == "半導體業"
    assert industry_name("1") == "水泥工業"
    assert industry_name("99") == "99"
    assert shares_outstanding("77231817420", "新台幣                 10.0000元") == 7_723_181_742
    assert shares_outstanding("1000", "新台幣 1.0000元") == 1000
