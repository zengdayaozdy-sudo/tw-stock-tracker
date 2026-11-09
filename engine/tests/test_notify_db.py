"""每日報告與系統警告的資料庫整合測試。"""

import json
from datetime import date, datetime

import httpx
import pytest

from engine.config import TZ, load_settings
from engine.notify.alerts import collect_alerts
from engine.notify.line import LineClient
from engine.notify.runner import run_notify, send_alerts
from engine.repository import Repository

AS_OF = date(2026, 11, 6)        # 週五
PREV = date(2026, 11, 5)
AFTER_CLOSE = datetime(2026, 11, 6, 16, 0, tzinfo=TZ)


def _log(conn, job, run_date, status="success", detail=None):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO job_logs (job, run_date, status, detail) VALUES (%s, %s, %s, %s)",
                    (job, run_date, status, json.dumps(detail or {}, ensure_ascii=False)))
    conn.commit()


def _line():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})

    client = LineClient("token", "U123", load_settings("free").notify["line"], transport=httpx.MockTransport(handler),
                        sleep=lambda s: None)
    return client, sent


@pytest.fixture()
def repo(conn):
    r = Repository(conn)
    r.upsert_stocks([{"stock_id": "2330", "name": "台積電", "market": "twse", "industry": "半導體業"}])
    with conn.cursor() as cur:
        cur.execute("INSERT INTO watchlist (stock_id) VALUES ('2330')")
    conn.commit()
    _log(conn, "fetch", PREV)
    return r


def test_no_alerts_when_all_good(repo):
    _log(repo.conn, "fetch", AS_OF, detail={"quality": {"ok": True, "errors": [], "warnings": []}})
    assert collect_alerts(load_settings("free"), repo, AS_OF, AFTER_CLOSE) == []


def test_alert_scenarios(repo):
    settings = load_settings("free")
    assert collect_alerts(settings, repo, AS_OF, AFTER_CLOSE) == ["排程未執行：2026-11-06 尚未抓取資料"]

    _log(repo.conn, "fetch", AS_OF, detail={"quality": {"ok": False, "errors": ["缺少 3 檔股價：1101"]}})
    _log(repo.conn, "news", AS_OF, status="failed", detail={"error": "連線逾時"})
    with repo.conn.cursor() as cur:
        cur.execute("INSERT INTO api_usage VALUES ('finmind', %s, 540)", (datetime(2026, 11, 6, 15, 0, tzinfo=TZ),))
        cur.execute("INSERT INTO job_logs (job, status, started_at) VALUES ('monitor_heartbeat', 'heartbeat', %s)",
                    (datetime(2026, 11, 6, 10, 5, tzinfo=TZ),))
    repo.conn.commit()
    alerts = collect_alerts(settings, repo, AS_OF, AFTER_CLOSE)
    assert alerts[0] == "資料完整性檢查未通過，今日暫停訊號：缺少 3 檔股價：1101"
    assert "消息面排雷步驟失敗：連線逾時" in alerts
    assert any(a.startswith("FinMind 額度用盡：15 時已用 540 次") for a in alerts)
    assert "盤中停損監控可能已停止：最後心跳 11/06 10:05" in alerts

    # 前一個交易日完全沒跑
    assert any("前一個交易日 2026-11-09" in a for a in collect_alerts(settings, repo, date(2026, 11, 10)))


def test_alerts_deduplicated(repo):
    settings = load_settings("free")
    line, sent = _line()
    first = send_alerts(settings, repo, line, AS_OF, AFTER_CLOSE)
    assert first["alerts_sent"] and len(sent) == 1
    _log(repo.conn, "alert", AS_OF, detail=first)
    again = send_alerts(settings, repo, line, AS_OF, AFTER_CLOSE)
    assert again["alerts_sent"] == [] and len(sent) == 1


def test_report_matches_database_and_sent_once(repo):
    settings = load_settings("free")
    _log(repo.conn, "fetch", AS_OF, detail={"quality": {"ok": True, "errors": [], "warnings": []}})
    repo.upsert("market", [{"date": AS_OF, "light": "neutral", "inputs": {"total": 0}, "group_strength": [
        {"name": "半導體業", "kind": "industry", "members": ["2330"], "rank": 1, "weak": False, "avg_score": 85,
         "return_20": 0.05, "rel_return_20": 0.02}]}])
    repo.upsert("scores", [{"stock_id": "2330", "date": AS_OF, "horizon": "short", "total": 85, "fundamental": 50,
                            "technical": 70, "chips": 60, "breakdown": {}, "status": "ok"}])
    repo.replace_signals(AS_OF, "buy", [{
        "stock_id": "2330", "date": AS_OF, "horizon": "short", "side": "buy", "triggers": ["above_ma60"],
        "entry_low": 1020, "entry_high": 1065, "stop_loss": 975, "take_profit": 1200, "suggested_lots": 0,
        "reason": "短線 85.0 分",
        "detail": {"name": "台積電", "close": 1050, "score": {"total": 85, "fundamental": 50, "technical": 70,
                                                             "chips": 60},
                   "trigger_text": ["站上季線"], "sizing": {"odd_shares": 112, "amount": 119280, "notes": []},
                   "warnings": []},
    }])
    _log(repo.conn, "signals", AS_OF, detail={"blocked": []})
    repo.conn.commit()

    line, sent = _line()
    result = run_notify(settings, repo, line, AS_OF, now=AFTER_CLOSE)
    assert result["report_sent"] and result["alerts"] == []
    text = "\n".join(m["text"] for req in sent for m in req["messages"])
    # 報告內容與資料庫一致
    assert "2330 台積電｜短線 85.0 分" in text
    assert "買進 1,020–1,065｜停損 975｜停利 1,200" in text
    assert "建議零股 112 股" in text
    assert "1. 半導體業｜平均 85 分" in text

    _log(repo.conn, "notify", AS_OF, detail={"report_sent": True})
    again = run_notify(settings, repo, line, AS_OF, now=AFTER_CLOSE)
    assert not again["report_sent"] and "已送出" in again["report_skipped"]
    assert run_notify(settings, repo, line, AS_OF, resend=True, now=AFTER_CLOSE)["report_sent"]


def test_holiday_and_preferences(repo):
    settings = load_settings("free")
    _log(repo.conn, "fetch", AS_OF, status="skipped")
    line, sent = _line()
    result = run_notify(settings, repo, line, AS_OF, now=AFTER_CLOSE)
    assert result["report_skipped"] == "休市，不送每日報告" and sent == []

    with repo.conn.cursor() as cur:
        cur.execute("UPDATE settings SET value = %s WHERE key = 'notify'",
                    (json.dumps({"daily_report": False, "system_alerts": False}),))
    repo.conn.commit()
    _log(repo.conn, "fetch", AS_OF, status="failed", detail={"error": "逾時"})
    result = run_notify(settings, repo, line, AS_OF, now=AFTER_CLOSE)
    assert result["skipped"] == "系統警告通知已關閉" and result["report_skipped"] == "每日報告通知已關閉"
    assert sent == []


def test_weekend_has_no_schedule_alerts(repo):
    sat = date(2026, 11, 7)
    assert collect_alerts(load_settings("free"), repo, sat, datetime(2026, 11, 7, 12, 0, tzinfo=TZ)) == []
