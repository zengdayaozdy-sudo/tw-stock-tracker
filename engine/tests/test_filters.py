"""濾網單元測試：固定假資料，不需資料庫與網路。"""

import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import httpx
import numpy as np
import pandas as pd
import pytest

from engine.ai import ClaudeJudge, FallbackJudge, JudgeError, OllamaJudge, RuleJudge, create_judge
from engine.config import TZ, load_settings
from engine.filters.events import TradingCalendar, event_flags, statutory_events
from engine.filters.groups import group_strength, period_return, weak_group_flags
from engine.filters.liquidity import liquidity_flags
from engine.filters.market_light import (
    compute_light,
    foreign_futures_oi,
    sox_trend,
    taiex_vs_ma60,
    tsmc_adr_premium,
    twd_trend,
)
from engine.filters.news import dedup_key, news_flags, parse_rss
from engine.sources import finmind as fm
from engine.sources.common import HttpClient
from engine.sources.exchanges import ExchangeClient, _in_period

AS_OF = date(2026, 11, 6)  # 週五
SETTINGS = load_settings("free")
ML = SETTINGS.filters["market_light"]


def bseries(values, end=AS_OF) -> pd.Series:
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=len(values))
    return pd.Series(np.asarray(values, dtype=float), index=idx)


# ---------- 流動性 ----------

def test_liquidity_flags():
    cfg = {**SETTINGS.risk["liquidity"], "lot_size": 1000}
    flags = liquidity_flags(
        ["1111", "2222", "3333", "4444"],
        {"1111": (900_000, 20), "2222": (100_000, 20), "3333": (2_000_000, 20)},
        {"3333": ["disposition", "attention"]},
        cfg,
    )
    by = {}
    for f in flags:
        by.setdefault(f["stock_id"], []).append(f["reason"])
    assert "1111" not in by
    assert "低於 500 張" in by["2222"][0]
    assert set(by["3333"]) == {"處置股", "注意股"}
    assert by["4444"] == ["沒有成交量資料"]
    assert all(f["kind"] == "exclude" for f in flags)


# ---------- 大盤燈號 ----------

def test_taiex_vs_ma60():
    up = bseries(np.linspace(100, 130, 80))
    assert taiex_vs_ma60(up, AS_OF, ML["taiex_vs_ma60"])["vote"] == 1
    down = bseries(np.linspace(130, 100, 80))
    assert taiex_vs_ma60(down, AS_OF, ML["taiex_vs_ma60"])["vote"] == -1
    flat = bseries([100.0] * 80)
    assert taiex_vs_ma60(flat, AS_OF, ML["taiex_vs_ma60"])["vote"] == 0
    short = taiex_vs_ma60(bseries([100.0] * 10), AS_OF, ML["taiex_vs_ma60"])
    assert short["vote"] == 0 and short["missing"]


def test_sox_uses_previous_us_day_only():
    # as_of 當天的美股收盤不能用（台股收盤時美股尚未開盤）
    s = bseries(np.linspace(100, 120, 40))
    s.iloc[-1] = 10.0  # 當天的暴跌資料不應被看到
    assert sox_trend(s, AS_OF, ML["sox_trend"])["vote"] == 1
    assert sox_trend(bseries(np.linspace(120, 100, 40)), AS_OF, ML["sox_trend"])["vote"] == -1


def test_tsmc_adr_premium():
    cfg = ML["tsmc_adr_premium"]
    n = 30
    tw = bseries([1000.0] * n)
    fx = bseries([32.0] * n)
    adr_vals = [180.0] * n  # 溢價 180×32/5/1000−1 = 15.2%
    adr = bseries(adr_vals)
    flat = tsmc_adr_premium(adr, fx, tw, AS_OF, cfg)
    assert flat["vote"] == 0 and flat["value"] == pytest.approx(0.152, abs=1e-4)
    adr_vals[-2] = 190.0  # 前一個美國交易日溢價拉高到 21.6%
    assert tsmc_adr_premium(bseries(adr_vals), fx, tw, AS_OF, cfg)["vote"] == 1
    adr_vals[-2] = 170.0
    assert tsmc_adr_premium(bseries(adr_vals), fx, tw, AS_OF, cfg)["vote"] == -1
    assert tsmc_adr_premium(adr, fx, pd.Series(dtype=float), AS_OF, cfg)["missing"]


def test_foreign_futures_oi():
    cfg = ML["foreign_futures_oi"]
    assert foreign_futures_oi(bseries([5000.0]), AS_OF, cfg)["vote"] == 1
    assert foreign_futures_oi(bseries([-99930.0]), AS_OF, cfg)["vote"] == -1
    rising = bseries([-30000, -28000, -26000, -25000, -24000, -20000])
    assert foreign_futures_oi(rising, AS_OF, cfg)["vote"] == 1
    steady = bseries([-20000] * 6)
    assert foreign_futures_oi(steady, AS_OF, cfg)["vote"] == 0


def test_twd_trend():
    cfg = ML["twd_trend"]
    assert twd_trend(bseries(np.linspace(33, 32, 25)), AS_OF, cfg)["vote"] == 1   # 台幣升值
    assert twd_trend(bseries(np.linspace(32, 33, 25)), AS_OF, cfg)["vote"] == -1
    assert twd_trend(bseries([32.5] * 5), AS_OF, cfg)["missing"]


def test_compute_light_states():
    up = {"taiex": bseries(np.linspace(100, 130, 80)), "sox": bseries(np.linspace(100, 120, 40)),
          "usd_twd": bseries(np.linspace(33, 32, 25)), "foreign_tx_oi": bseries([1000.0])}
    light = compute_light(up, AS_OF, ML)
    assert light.light == "bullish" and light.total == 4
    assert light.inputs["tsmc_adr_premium"]["missing"]  # 缺資料以 0 計

    down = {"taiex": bseries(np.linspace(130, 100, 80)), "sox": bseries(np.linspace(120, 100, 40)),
            "usd_twd": bseries(np.linspace(32, 33, 25)), "foreign_tx_oi": bseries([-50000.0])}
    assert compute_light(down, AS_OF, ML).light == "bearish"
    assert compute_light({}, AS_OF, ML).light == "neutral"


def test_light_ignores_future_data():
    taiex = bseries(np.linspace(100, 130, 80), end=AS_OF + timedelta(days=30))
    # 截至 AS_OF 的資料只到中段，結果不應受之後的漲勢影響
    s = taiex[taiex.index <= pd.Timestamp(AS_OF)]
    assert taiex_vs_ma60(taiex, AS_OF, ML["taiex_vs_ma60"]) == taiex_vs_ma60(s, AS_OF, ML["taiex_vs_ma60"])


# ---------- 族群強弱 ----------

GS = SETTINGS.filters["group_strength"]


def _closes(daily_ret: float, n: int = 80) -> pd.Series:
    return bseries(100 * np.cumprod(np.full(n, 1 + daily_ret)))


def test_period_return():
    s = _closes(0.01, 30)
    assert period_return(s, AS_OF, 20) == pytest.approx(1.01 ** 20 - 1)
    assert period_return(s, AS_OF + timedelta(days=3), 20) is None  # 當天沒有收盤不計算
    assert period_return(s, AS_OF, 40) is None


def test_group_strength_ranking():
    closes = {"A1": _closes(0.01), "A2": _closes(0.008), "B1": _closes(0.0), "C1": _closes(-0.01),
              "D1": _closes(0.003)}
    groups = {
        "強族群": ("custom", {"A1", "A2"}),
        "持平族群": ("industry", {"B1"}),
        "弱族群": ("industry", {"C1"}),
        "中間族群": ("industry", {"D1"}),
        "範圍外": ("industry", {"Z9"}),
    }
    taiex = _closes(0.002)
    scores = {"A1": 80, "A2": 70, "B1": 50, "C1": 30, "D1": 60}
    result = group_strength(groups, set(closes), closes, taiex, scores, AS_OF, GS)
    names = [g["name"] for g in result]
    assert names[0] == "強族群" and names[-1] == "弱族群"
    assert "範圍外" not in names
    weak = [g["name"] for g in result if g["weak"]]
    assert weak == ["持平族群", "弱族群"]  # 4 個族群 × 30% → 後 2 名
    strong = result[0]
    assert strong["rel_return_20"] == pytest.approx(strong["return_20"] - (1.002 ** 20 - 1), abs=1e-4)
    assert strong["avg_score"] == 75


def test_too_few_groups_no_weak():
    closes = {"A1": _closes(0.01), "C1": _closes(-0.01)}
    groups = {"甲": ("industry", {"A1"}), "乙": ("industry", {"C1"})}
    result = group_strength(groups, set(closes), closes, pd.Series(dtype=float), {}, AS_OF, GS)
    assert not any(g["weak"] for g in result)
    assert all(g["rel_return_20"] is None for g in result)  # 沒有加權指數時改用絕對報酬排名
    assert result[0]["name"] == "甲"


def test_weak_group_flags_custom_precedence():
    strength = [
        {"name": "AI", "kind": "custom", "members": ["1111"], "rank": 1, "weak": False},
        {"name": "鋼鐵", "kind": "industry", "members": ["1111", "2222"], "rank": 5, "weak": True},
        {"name": "航運", "kind": "custom", "members": ["3333"], "rank": 6, "weak": True},
        {"name": "未排名", "kind": "industry", "members": ["4444"], "rank": None, "weak": False},
    ]
    flags = weak_group_flags(strength, {"1111", "2222", "3333", "4444"})
    blocked = {f["stock_id"]: f["reason"] for f in flags}
    assert "1111" not in blocked          # 自訂族群強勢，不看產業
    assert blocked["2222"] == "弱勢族群：鋼鐵"
    assert blocked["3333"] == "弱勢族群：航運"
    assert "4444" not in blocked
    assert all(f["kind"] == "block" for f in flags)


# ---------- 事件行事曆 ----------

def test_trading_calendar():
    cal = TradingCalendar({date(2026, 11, 9)})  # 假設週一休市
    assert cal.offset(AS_OF, date(2026, 11, 10)) == 1
    assert cal.offset(AS_OF, date(2026, 11, 8)) == 1   # 週日事件以下一個交易日（週二）計
    assert cal.offset(AS_OF, date(2026, 11, 4)) == -2
    assert cal.add(AS_OF, 2) == date(2026, 11, 11)
    assert cal.add(AS_OF, -1) == date(2026, 11, 5)


def test_statutory_events_only_when_unpublished():
    latest = {("1111", "month"): "2026-10", ("2222", "month"): "2026-09", ("1111", "quarter"): "2026Q3"}
    events = statutory_events(["1111", "2222"], latest, AS_OF)
    keys = {(e["stock_id"], e["event_type"], e["date"]) for e in events}
    assert ("2222", "月營收", date(2026, 11, 10)) in keys
    assert ("1111", "月營收", date(2026, 11, 10)) not in keys
    assert ("2222", "財報", date(2026, 11, 14)) in keys
    assert ("1111", "財報", date(2026, 11, 14)) not in keys


def test_event_flags_window():
    cal = TradingCalendar()
    events = [
        {"stock_id": "1111", "date": date(2026, 11, 10), "event_type": "除權息", "note": "除息"},  # 2 個交易日後
        {"stock_id": "1111", "date": date(2026, 11, 11), "event_type": "股東會", "note": None},   # 3 個交易日後
        {"stock_id": "2222", "date": date(2026, 11, 5), "event_type": "財報", "note": None},     # 1 個交易日前
        {"stock_id": "3333", "date": date(2026, 11, 4), "event_type": "財報", "note": None},     # 2 個交易日前
    ]
    flags = event_flags(events, AS_OF, cal, SETTINGS.filters["events"])
    reasons = {f["stock_id"]: f["reason"] for f in flags}
    assert reasons == {"1111": "除權息 11/10（2 個交易日後）", "2222": "財報 11/05（1 個交易日前）"}
    assert all(f["kind"] == "warn" for f in flags)


# ---------- 消息面 ----------

def _news(**kw):
    base = {"stock_id": "1111", "source": "twse_announcement", "title": "公告本公司辦理現金增資",
            "published_at": datetime(2026, 11, 6, 17, 0, tzinfo=TZ), "url": None, "body": None}
    base.update(kw)
    return base


def test_dedup_key():
    assert dedup_key(_news()) == dedup_key(_news())
    assert dedup_key(_news()) != dedup_key(_news(title="其他"))
    assert dedup_key(_news(url="https://x/1")) == dedup_key(_news(url="https://x/1", title="改標題"))


def test_parse_rss():
    xml = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>台積電法說會</title><link>https://example.com/a</link>
      <pubDate>Fri, 06 Nov 2026 08:00:00 GMT</pubDate><description>內容</description></item>
      <item><title></title></item></channel></rss>"""
    items = parse_rss(xml, "2330", "rss")
    assert len(items) == 1
    assert items[0]["published_at"] == datetime(2026, 11, 6, 16, 0, tzinfo=TZ)
    assert parse_rss("not xml", "2330", "rss") == []


def test_news_flags_threshold():
    rows = [
        {"news_id": 1, "stock_id": "1111", "title": "現金增資", "event_type": "現金增資", "confidence": 0.9,
         "summary": "辦理現增", "url": None, "judged_by": "ollama", "published_at": _news()["published_at"]},
        {"news_id": 2, "stock_id": "2222", "title": "疑似訴訟", "event_type": "重大訴訟", "confidence": 0.4,
         "summary": "", "url": None, "judged_by": "ollama", "published_at": _news()["published_at"]},
    ]
    flags = news_flags(rows, SETTINGS.filters["news"])
    assert [f["stock_id"] for f in flags] == ["1111"]
    assert flags[0]["kind"] == "exclude" and flags[0]["detail"]["confidence"] == 0.9


# ---------- AI 判讀 ----------

EVENT_TYPES = SETTINGS.filters["news"]["negative_event_types"]


def test_rule_judge():
    judge = RuleJudge(SETTINGS.filters["news"])
    j = judge.judge(_news())
    assert j.is_major_negative and j.event_type == "現金增資" and j.confidence >= 0.7
    j = judge.judge(_news(title="公告本公司董事會通過第三季財報"))
    assert not j.is_major_negative
    j = judge.judge(_news(title="重大訊息說明", body="本公司工廠發生火災"))
    assert j.is_major_negative and j.confidence < judge.confidence  # 只在內文命中，信心度較低


def test_ollama_judge():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.update(body)
        content = {"is_major_negative": True, "event_type": "財測下修", "confidence": 0.85, "summary": "下修財測"}
        return httpx.Response(200, json={"message": {"content": json.dumps(content, ensure_ascii=False)}})

    judge = OllamaJudge("http://ollama:11434/", "qwen", EVENT_TYPES, transport=httpx.MockTransport(handler))
    j = judge.judge(_news(title="下修全年財測"))
    assert j.is_major_negative and j.event_type == "財測下修" and j.judged_by == "ollama"
    assert seen["model"] == "qwen" and seen["stream"] is False
    assert seen["format"]["properties"]["event_type"]["enum"][-1] == "無"


def test_ollama_error_raises():
    judge = OllamaJudge("http://ollama", "qwen", EVENT_TYPES,
                        transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(JudgeError):
        judge.judge(_news())


class FakeClaude:
    def __init__(self, text=None, stop_reason="end_turn"):
        self.calls = []
        block = SimpleNamespace(type="text", text=text)
        self.response = SimpleNamespace(stop_reason=stop_reason, content=[block] if text else [])
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_claude_judge():
    payload = {"is_major_negative": False, "event_type": "無", "confidence": 0.9, "summary": "例行公告"}
    fake = FakeClaude(json.dumps(payload, ensure_ascii=False))
    j = ClaudeJudge("claude-opus-5-5", EVENT_TYPES, client=fake).judge(_news(title="董事會通過財報"))
    assert not j.is_major_negative and j.event_type is None and j.judged_by == "claude"
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default"


def test_claude_refusal_raises():
    with pytest.raises(JudgeError):
        ClaudeJudge("claude-opus-5-5", EVENT_TYPES, client=FakeClaude(None, "refusal")).judge(_news())


def test_negative_without_known_type_is_downgraded():
    payload = {"is_major_negative": True, "event_type": "無", "confidence": 0.95, "summary": "其他利空"}
    j = ClaudeJudge("m", EVENT_TYPES, client=FakeClaude(json.dumps(payload))).judge(_news())
    assert j.confidence <= 0.5  # 不在清單內的利空類型不會被排除


def test_fallback_judge_switches_to_rules():
    class Broken:
        name = "ollama"

        def judge(self, item):
            raise JudgeError("連不上")

    judge = FallbackJudge(Broken(), RuleJudge(SETTINGS.filters["news"]))
    results = [judge.judge(_news()) for _ in range(5)]
    assert all(r.judged_by == "rules" for r in results)
    assert len(judge.errors) == 3  # 連續失敗 3 次後不再嘗試


def test_create_judge_without_model_falls_back(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    judge = create_judge(SETTINGS)
    assert judge.primary is None and judge.name == "rules"
    monkeypatch.setenv("AI_PROVIDER", "claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert create_judge(SETTINGS).primary is None


# ---------- 資料來源解析 ----------

def _exchange(routes: dict[str, object]) -> ExchangeClient:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key, payload in routes.items():
            if key in url:
                return httpx.Response(200, json=payload)
        return httpx.Response(404)

    http = HttpClient(transport=httpx.MockTransport(handler), max_retries=0, sleep=lambda s: None)
    return ExchangeClient(SETTINGS, http)


def test_status_flags_all_sources():
    ex = _exchange({
        "announcement/punish": [{"Code": "2330", "DispositionPeriod": "115/11/05～115/11/11"},
                                {"Code": "2317", "DispositionPeriod": "115/10/01～115/10/07"}],
        "announcement/notice": [{"Code": "1101"}],
        "TWT85U": [{"Code": "1213", "Name": "大飲"}],
        "tpex_disposal_information": [{"SecuritiesCompanyCode": "8099", "DispositionPeriod": "1151105~1151111"}],
        "tpex_trading_warning_information": [{"SecuritiesCompanyCode": "1569"}],
        "tpex_cmode": [{"SecuritiesCompanyCode": "2067", "AlteredTrading": "Ｙ", " FinancialAnnouncements": "Ｙ"},
                       {"SecuritiesCompanyCode": "3064", "AlteredTrading": ""}],
    })
    flags = ex.status_flags(AS_OF)
    assert flags == {"2330": {"disposition"}, "1101": {"attention"}, "1213": {"full_delivery"},
                     "8099": {"disposition"}, "1569": {"attention"}, "2067": {"full_delivery"}}
    assert ex.last_errors == []


def test_status_flags_partial_failure():
    ex = _exchange({"announcement/notice": [{"Code": "1101"}]})
    flags = ex.status_flags(AS_OF)
    assert flags == {"1101": {"attention"}}
    assert ex.last_errors  # 其他來源失敗記錄下來，但不中斷


def test_in_period():
    assert _in_period("115/11/05～115/11/11", date(2026, 11, 6))
    assert not _in_period("1151001~1151007", date(2026, 11, 6))
    assert _in_period("看不懂", date(2026, 11, 6))


def test_announcements_parsing():
    ex = _exchange({
        "t187ap04_L": [{"發言日期": "1151106", "發言時間": "70003", "公司代號": "2072", "主旨 ": "公告\r\n更名",
                        "說明": "內容"}, {"公司代號": "0050", "主旨 ": "ETF"}],
        "mopsfin_t187ap04_O": [{"發言日期": "1151106", "發言時間": "164607", "SecuritiesCompanyCode": "5347",
                                "主旨": "董事會通過財報", "說明": ""}],
    })
    items = ex.announcements()
    assert [i["stock_id"] for i in items] == ["2072", "5347"]
    assert items[0]["title"] == "公告 更名"
    assert items[0]["published_at"] == datetime(2026, 11, 6, 7, 0, 3, tzinfo=TZ)
    assert items[1]["published_at"].hour == 16 and items[1]["body"] is None


def test_events_and_holidays_parsing():
    ex = _exchange({
        "TWT48U_ALL": [{"Date": "1151119", "Code": "6924", "Exdividend": "權"},
                       {"Date": "1151106", "Code": "00400A", "Exdividend": "息"}],
        "tpex_exright_prepost": [{"ExRrightsExDividendDate": "1151229", "SecuritiesCompanyCode": "6488",
                                  "ExRrightsExDividend": "除息"}],
        "t187ap41_L": [{"公司代號": "1101", "開會日期": "1151013", "股東常(臨時)會": "臨時會"}],
        "t187ap41_O": [],
        "holidaySchedule": [{"Name": "國曆新年開始交易日", "Date": "1150102", "Description": "開始交易"},
                            {"Name": "中華民國開國紀念日", "Date": "1150101", "Description": "依規定放假1日。"}],
    })
    ex_div = ex.ex_dividend_events()
    assert [(e["stock_id"], e["date"]) for e in ex_div] == [("6924", date(2026, 11, 19)), ("6488", date(2026, 12, 29))]
    assert ex.shareholder_meetings()[0]["note"] == "股東臨時會"
    assert ex.holidays() == {date(2026, 1, 1)}


def test_taiex_month_parsing():
    payload = {"stat": "OK", "fields": ["日期", "開盤指數", "最高指數", "最低指數", "收盤指數"],
               "data": [["115/11/02", "45,034.53", "45,156.17", "44,399.10", "44,920.14"]]}
    ex = _exchange({"MI_5MINS_HIST": payload})
    assert ex.taiex_month(date(2026, 11, 1)) == [{"series": "taiex", "date": date(2026, 11, 2), "value": 44920.14}]


def test_finmind_market_parsers():
    us = fm.parse_us_close([{"date": "2026-11-06", "stock_id": "^SOX", "Close": 9390.25}], "sox")
    assert us == [{"series": "sox", "date": date(2026, 11, 6), "value": 9390.25}]
    fx = fm.parse_exchange_rate([{"date": "2026-11-06", "spot_buy": 32.72, "spot_sell": 32.82},
                                 {"date": "2026-11-07", "spot_buy": "-", "spot_sell": 0}])
    assert len(fx) == 1 and fx[0]["value"] == pytest.approx(32.77)
    oi = fm.parse_foreign_futures_oi([
        {"date": "2026-11-06", "institutional_investors": "外資", "long_open_interest_balance_volume": 15873,
         "short_open_interest_balance_volume": 115803},
        {"date": "2026-11-06", "institutional_investors": "投信", "long_open_interest_balance_volume": 1,
         "short_open_interest_balance_volume": 2},
    ])
    assert oi == [{"series": "foreign_tx_oi", "date": date(2026, 11, 6), "value": -99930}]


MOPS_LIST = {"code": 200, "message": "查詢成功", "result": {"titles": [], "data": [
    ["115/11/09", "11:14:25", "2344", "華邦電", "澄清媒體報導",
     {"parameters": {"serialNumber": 1, "companyId": "2344", "marketKind": "sii", "enterDate": "1151109"},
      "apiName": "t05st02_detail"}],
    ["115/11/09", "13:32:16", "4551", "智伸科", "公告資金貸與\r\n第二十五條",
     {"parameters": {"serialNumber": 1, "companyId": "4551", "marketKind": "otc", "enterDate": "1151109"}}],
    ["115/11/09", "09:00:00", "7777", "興櫃公司", "興櫃不收",
     {"parameters": {"serialNumber": 1, "companyId": "7777", "marketKind": "rotc", "enterDate": "1151109"}}],
    ["115/11/09", "09:00:00", "00878", "ETF", "ETF 不收", {"parameters": {"marketKind": "sii"}}],
]}}
MOPS_DETAIL = {"code": 200, "result": {
    "titles": [{"main": "序號"}, {"main": "發言日期"}, {"main": "主旨"}, {"main": "說明"}],
    "data": [[1, "115/11/09", "澄清媒體報導", "1.事實發生日:115/11/09\n2.報導內容:營收"]]}}


def test_mops_announcements_parsing():
    ex = _exchange({"t05st02_detail": MOPS_DETAIL, "t05st02": MOPS_LIST})
    items = ex.mops_announcements(date(2026, 11, 9))
    assert [i["stock_id"] for i in items] == ["2344", "4551"]  # 興櫃、ETF 排除
    assert items[0]["published_at"] == datetime(2026, 11, 9, 11, 14, 25, tzinfo=TZ)
    assert items[1]["title"] == "公告資金貸與 第二十五條"
    assert items[0]["source"] == "mops"
    assert ex.mops_detail(items[0]["detail_params"]).startswith("1.事實發生日")


def test_mops_error_response_raises():
    ex = _exchange({"t05st02": {"code": 500, "message": "傳入參數異常", "result": None}})
    try:
        ex.mops_announcements(date(2026, 11, 9))
    except Exception as e:
        assert "異常" in str(e)
    else:
        raise AssertionError("應該丟出錯誤")
