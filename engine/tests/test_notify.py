"""LINE 推播與每日報告文字的單元測試。"""

import json
from datetime import date

import httpx
import pytest

from engine.config import load_settings
from engine.notify.line import LineClient, LineError, pack_blocks, split_text
from engine.notify.report import render_blocks, render_messages

SETTINGS = load_settings("free")
LINE_CFG = SETTINGS.notify["line"]
AS_OF = date(2026, 11, 6)


def test_split_text():
    assert split_text("abc", 10) == ["abc"]
    parts = split_text("aaaa\nbbbb\ncccc", 9)
    assert parts == ["aaaa\nbbbb", "cccc"]
    assert split_text("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5]
    assert all(len(p) <= 10 for p in split_text("短\n" + "y" * 23 + "\n尾", 10))


def test_pack_blocks():
    assert pack_blocks(["甲", "乙"], 100) == ["甲\n\n乙"]
    msgs = pack_blocks(["a" * 6, "b" * 6, "c" * 6], 14)
    assert msgs == ["a" * 6 + "\n\n" + "b" * 6, "c" * 6]


class Recorder:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(self.statuses.pop(0), json={})


def _client(statuses):
    rec = Recorder(statuses)
    client = LineClient("token", "U123", LINE_CFG, transport=httpx.MockTransport(rec), sleep=lambda s: None)
    return client, rec


def test_push_batches_of_five():
    client, rec = _client([200, 200])
    assert client.push_texts([f"訊息{i}" for i in range(7)]) == 7
    assert len(rec.requests) == 2
    body = json.loads(rec.requests[0].content)
    assert body["to"] == "U123" and len(body["messages"]) == 5
    assert body["messages"][0] == {"type": "text", "text": "訊息0"}
    assert rec.requests[0].headers["Authorization"] == "Bearer token"
    assert rec.requests[0].headers["X-Line-Retry-Key"]


def test_retry_keeps_same_key_and_409_is_success():
    client, rec = _client([429, 500, 409])
    client.push_texts(["hi"])
    keys = {r.headers["X-Line-Retry-Key"] for r in rec.requests}
    assert len(rec.requests) == 3 and len(keys) == 1


def test_client_error_not_retried():
    client, rec = _client([400])
    with pytest.raises(LineError):
        client.push_texts(["hi"])
    assert len(rec.requests) == 1


def test_unconfigured_does_not_send():
    client = LineClient(None, None, LINE_CFG, transport=httpx.MockTransport(lambda r: pytest.fail("不應送出")))
    assert client.push_texts(["hi"]) == 0


def _signal(sid, total, horizon="short"):
    return {
        "stock_id": sid, "horizon": horizon, "triggers": ["volume_breakout_20d"],
        "entry_low": 100.0, "entry_high": 107.5, "stop_loss": 92.0, "take_profit": 120.0, "suggested_lots": 1,
        "reason": "",
        "detail": {"name": f"股{sid}", "close": 106.0, "score": {"total": total, "fundamental": 50, "technical": 70,
                                                                 "chips": 60},
                   "trigger_text": ["帶量突破 20 日高點（量 2.5 倍）"], "high_score_reasons": ["突破 20 日區間 95 分"],
                   "sizing": {"amount": 107500, "risk_amount": 15500, "notes": []},
                   "warnings": ["除權息 11/10（2 個交易日後）"]},
    }


def _data(**kw):
    d = {
        "date": AS_OF, "plan": "免費模式",
        "market": {"light": "bearish", "group_strength": [],
                   "inputs": {"total": -3, "taiex_vs_ma60": {"label": "加權指數相對季線", "reason": "低於季線 14.5%",
                                                             "vote": -1}}},
        "groups": [{"name": "半導體業", "rank": 1, "avg_score": 72.4, "return_20": 0.052, "rel_return_20": 0.031}],
        "weak_groups": ["水泥工業"],
        "group_leaders": [{"name": "半導體業", "stocks": [{"stock_id": "2330", "name": "台積電", "score": 85.0}]}],
        "buy_signals": [_signal("2330", 85.0)], "sell_signals": [], "signal_status": "success", "signal_reason": None,
        "blocked": [{"stock_id": "1101", "name": "台泥", "reasons": ["現金增資：公告辦理現增"]}],
        "holdings": [{"stock_id": "2317", "name": "鴻海", "shares": 2000, "avg_cost": 200.0, "stop_loss": 184.0,
                      "take_profit": None, "close": 210.0}],
        "quality_ok": True, "quality_warnings": 2,
    }
    d.update(kw)
    return d


def test_render_blocks_contents():
    text = "\n\n".join(render_blocks(_data(), site_url="https://example.com"))
    assert "【台股每日報告】2026/11/06（五）" in text
    assert "大盤燈號：偏空（-3）" in text and "大盤偏空：不發新買進訊號" in text
    assert "1. 半導體業｜平均 72 分｜近 1 月 +5.2%（相對大盤 +3.1%）" in text
    assert "弱勢族群（不發買進）：水泥工業" in text
    assert "2330 股2330｜短線 85.0 分（基 50／技 70／籌 60）" in text
    assert "買進 100–107.5｜停損 92｜停利 120" in text
    assert "建議 1 張（約 107,500 元，最大虧損約 15,500 元）" in text
    assert "提示：除權息 11/10（2 個交易日後）" in text
    assert "・1101 台泥：現金增資：公告辦理現增" in text
    assert "2317 鴻海 2 張｜成本 200 現價 210（+5.0%）｜停損 184" in text
    assert "資料完整（2 則提醒）" in text and "網站：https://example.com" in text


def test_render_halted_and_empty():
    halted = "\n".join(render_blocks(_data(buy_signals=[], blocked=[], signal_status="skipped",
                                           signal_reason="資料完整性檢查未通過：缺少 3 檔股價")))
    assert "【買進訊號】今日暫停：資料完整性檢查未通過：缺少 3 檔股價" in halted
    empty = "\n".join(render_blocks(_data(buy_signals=[], blocked=[], market=None, groups=[], holdings=[])))
    assert "大盤燈號：尚未計算" in empty and "今日沒有符合條件的個股" in empty


def test_render_messages_compact_and_truncate():
    many = [_signal(str(1000 + i), 90 - i * 0.1) for i in range(60)]
    cfg = {**SETTINGS.notify, "line": {**LINE_CFG, "max_text_chars": 1500, "max_messages_per_report": 3}}
    msgs = render_messages(_data(buy_signals=many), cfg, "https://example.com")
    assert len(msgs) <= 3 and all(len(m) <= 1500 for m in msgs)
    joined = "\n".join(msgs)
    assert "精簡版" in joined
    small = {**cfg, "line": {**cfg["line"], "max_text_chars": 400}}
    msgs = render_messages(_data(buy_signals=many), small, "https://example.com")
    assert len(msgs) == 3 and "完整內容請見網站：https://example.com" in msgs[-1]
    assert all(len(m) <= 400 for m in msgs)
