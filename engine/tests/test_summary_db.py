"""每日盤勢總結的資料庫整合測試：讀全市場行情、法人、新聞、持股，寫入 daily_summaries，LINE 報告帶出總結與隔日計畫。"""

import json
from datetime import date, datetime, timedelta

import httpx
import pandas as pd
import pytest

from engine.config import TZ, load_settings
from engine.notify.line import LineClient
from engine.notify.runner import run_notify
from engine.repository import Repository
from engine.summary.runner import run_summary

AS_OF = date(2026, 11, 12)
PREV = date(2026, 11, 11)


def _log(conn, job, run_date, status="success", detail=None):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO job_logs (job, run_date, status, detail) VALUES (%s, %s, %s, %s)",
                    (job, run_date, status, json.dumps(detail or {}, ensure_ascii=False)))
    conn.commit()


@pytest.fixture()
def settings():
    s = load_settings("free")
    s.notify["daily_summary"]["min_market_rows"] = 3  # 測試只放 4 檔
    return s


@pytest.fixture()
def repo(conn):
    r = Repository(conn)
    r.upsert_stocks([
        {"stock_id": "2330", "name": "台積電", "market": "twse", "industry": "半導體業"},
        {"stock_id": "2308", "name": "台達電", "market": "twse", "industry": "電子零組件業"},
        {"stock_id": "2881", "name": "富邦金", "market": "twse", "industry": "金融保險業"},
        {"stock_id": "6488", "name": "環球晶", "market": "tpex", "industry": "半導體業"},
    ])
    with conn.cursor() as cur:
        cur.execute("UPDATE stocks SET shares = CASE stock_id WHEN '2330' THEN 25900000000 WHEN '2308' THEN 2600000000 "
                    "WHEN '2881' THEN 13700000000 ELSE 480000000 END")
        cur.execute("INSERT INTO watchlist (stock_id) VALUES ('2330'), ('2308'), ('2881')")
    # 加權指數 70 個交易日：前段 46,500，11/6 急跌、連三天反彈到 44,257.15，今天 43,091.87
    days = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=70)
    vals = [46500.0] * 64 + [41574.72, 39701.64, 41661.70, 42712.0, 44257.15, 43091.87]
    rows = [{"series": "taiex", "date": d.date(), "value": v} for d, v in zip(days, vals, strict=True)]
    rows += [{"series": "taiex_open", "date": AS_OF, "value": 44180.46},
             {"series": "taiex_high", "date": AS_OF, "value": 44525.24},
             {"series": "taiex_low", "date": AS_OF, "value": 42943.50},
             {"series": "sox", "date": PREV, "value": 10564.11},
             {"series": "sox", "date": PREV - timedelta(days=1), "value": 10244.0},
             {"series": "usd_twd", "date": AS_OF, "value": 32.889}, {"series": "usd_twd", "date": PREV, "value": 32.86}]
    r.upsert("market_series", rows)
    prices = []
    for sid, prev, close, turnover in (("2330", 2395, 2340, 6.0e11), ("2308", 1295, 1240, 1.0e11),
                                       ("2881", 90, 90.5, 2.0e10), ("6488", 400, 380, 5.0e9)):
        prices.append({"stock_id": sid, "date": PREV, "close": prev, "turnover": turnover * 0.7})
        prices.append({"stock_id": sid, "date": AS_OF, "close": close, "turnover": turnover})
    r.upsert("daily_prices", prices)
    # 外資連三天賣超台積電，投信今天買超
    r.upsert_chips([
        {"stock_id": "2330", "date": AS_OF, "foreign_net": -3_920_000, "trust_net": 5_000_000, "dealer_net": -100_000},
        {"stock_id": "2330", "date": PREV, "foreign_net": -2_000_000, "trust_net": 0, "dealer_net": 0},
        {"stock_id": "2330", "date": PREV - timedelta(days=1), "foreign_net": -1_000_000, "trust_net": 0, "dealer_net": 0},
        {"stock_id": "2881", "date": AS_OF, "foreign_net": 1_000_000, "trust_net": 0, "dealer_net": 0},
    ])
    r.upsert("market", [{"date": AS_OF, "light": "bearish", "inputs": {"total": -3},
                         "rebound": {"state": "attempt", "label": "反彈嘗試", "points": 1},
                         "group_strength": [{"name": "半導體業", "members": ["2330", "6488"], "rank": 1},
                                            {"name": "金融保險業", "members": ["2881"], "rank": 2}]}])
    r.insert_news([{"stock_id": None, "scope": "market", "topic": "台股動態", "source": "Yahoo股市／中時新聞網",
                    "title": "台股2大原因收盤慘跌1165點", "url": "https://example.com/a", "dedup_key": "a",
                    "published_at": datetime(2026, 11, 12, 13, 35, tzinfo=TZ)},
                   {"stock_id": None, "scope": "market", "topic": "台股動態", "source": "Yahoo股市",
                    "title": "上週的舊聞", "url": "https://example.com/b", "dedup_key": "b",
                    "published_at": datetime(2026, 11, 5, 9, 0, tzinfo=TZ)}])
    r.save_holding({"stock_id": "2308", "shares": 1000, "avg_cost": 1300, "opened_date": date(2026, 10, 15),
                    "stop_loss": 1250, "take_profit": None, "highest_close": 1300})
    r.replace_signals(AS_OF, "sell", [{"stock_id": "2308", "date": AS_OF, "horizon": "short", "side": "sell",
                                       "triggers": ["stop_loss"], "stop_loss": 1250, "reason": "收盤 1240 跌破停損 1250",
                                       "detail": {"name": "台達電"}}])
    conn.commit()
    _log(conn, "fetch", AS_OF, detail={"quality": {"ok": True, "errors": [], "warnings": []}})
    _log(conn, "signals", AS_OF, detail={"regime": "blocked", "blocked": [], "setups": [
        {"stock_id": "2330", "name": "台積電", "close": 2340, "trigger_price": 2400, "entry_low": 2400, "entry_high": 2425,
         "stop_loss": 2230, "take_profit": 2815, "sign_text": ["抗跌"], "target_basis": "報酬風險比 2 倍"}]})
    return r


class _Writer:
    name = "claude"

    def write(self, system, user):
        assert "台股2大原因收盤慘跌1165點" in user and "上週的舊聞" not in user
        return "台股今天開低走低。"


def test_run_summary_saves_facts_reasons_plan(settings, repo):
    result = run_summary(settings, repo, AS_OF, _Writer())
    assert result["status"] == "ok" and result["generated_by"] == "claude"
    saved = repo.daily_summary(AS_OF)
    f = saved["facts"]
    assert f["index"]["intraday_flip"] == "翻紅後收黑"
    assert f["breadth"]["advancers"] == 1 and f["breadth"]["decliners"] == 3
    # 法人估算只算上市：外資 −392 萬股 × 2,340 + 100 萬股 × 90.5
    assert f["flows"]["foreign"] == pytest.approx(-3_920_000 * 2340 + 1_000_000 * 90.5)
    # 前兩天只有台積電一檔有法人資料（不到全市場筆數的 8 成），不列入連續天數
    assert f["flows"]["foreign_streak"] == 1
    assert f["foreign_top"]["sell"][0]["stock_id"] == "2330"
    assert f["contributors"]["drags"][0]["stock_id"] == "2330"
    assert [n["title"] for n in f["news"]] == ["台股2大原因收盤慘跌1165點"]
    assert f["groups"]["count"] == 0  # 族群成分股不到 3 檔有報價，不計算當日漲跌
    keys = [r["key"] for r in saved["reasons"]]
    assert keys[0] == "profit_taking" and "heavyweights" in keys and "sox" in keys
    plan = saved["plan"]
    assert plan["posture"]["title"] == "偏空、反彈嘗試尚未確認"
    assert plan["holdings"][0]["action"].startswith("已跌破停損")
    assert plan["entries"][0]["kind"] == "setup"
    assert saved["narrative"] == "台股今天開低走低。"

    # 重跑覆寫，不會重複
    run_summary(settings, repo, AS_OF, None)
    assert repo.daily_summary(AS_OF)["generated_by"] == "rules"


def test_summary_waits_for_index(settings, repo):
    assert run_summary(settings, repo, AS_OF + timedelta(days=1), None)["status"] == "no_index"


def test_line_report_includes_summary_and_plan(settings, repo):
    run_summary(settings, repo, AS_OF, None)
    sent = []
    line = LineClient("token", "U123", settings.notify["line"], sleep=lambda s: None,
                      transport=httpx.MockTransport(lambda req: sent.append(json.loads(req.content))
                                                    or httpx.Response(200, json={})))
    result = run_notify(settings, repo, line, AS_OF, now=datetime(2026, 11, 12, 16, 0, tzinfo=TZ))
    assert result["report_sent"]
    text = "\n".join(m["text"] for req in sent for m in req["messages"])
    assert "【盤勢總結】\n加權指數開低（-0.17%），盤中一度翻紅" in text
    assert "【隔日操作計畫】\n方針：偏空、反彈嘗試尚未確認" in text
    assert "・2308 台達電 1,240（-4.6%）：已跌破停損，明日依紀律出場，不攤平" in text
    assert text.index("【盤勢總結】") < text.index("【持股提醒】") < text.index("【隔日操作計畫】")
