"""買進訊號步驟與資料庫整合測試。"""

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine.config import load_settings
from engine.repository import Repository
from engine.signals.runner import quality_gate, run_signals

AS_OF = date(2026, 11, 6)
DATES = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=130)
# 代號: (產業, 今日是否帶量突破, 短線分數)
STOCKS = {
    "2330": ("半導體業", True, 85),
    "2303": ("半導體業", True, 75),
    "2454": ("半導體業", False, 80),   # 高分但沒有進場條件
    "1101": ("水泥工業", True, 70),    # 會被消息面排除
    "1102": ("水泥工業", True, 50),    # 分數不到門檻
}


def _job(conn, status="success", ok=True):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO job_logs (job, run_date, status, detail) VALUES ('fetch', %s, %s, %s)",
                    (AS_OF, status, json.dumps({"quality": {"ok": ok, "errors": [] if ok else ["缺少 3 檔股價"]}})))
    conn.commit()


@pytest.fixture()
def repo(conn):
    r = Repository(conn)
    r.upsert_stocks([{"stock_id": s, "name": f"股{s}", "market": "twse", "industry": v[0]} for s, v in STOCKS.items()])
    rows = []
    for sid, (_ind, breakout, _score) in STOCKS.items():
        closes = np.full(len(DATES), 100.0)
        vols = np.full(len(DATES), 2_000_000.0)
        if breakout:
            closes[-1], vols[-1] = 106.0, 5_000_000.0
        rows += [{"stock_id": sid, "date": d.date(), "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                  "volume": v, "turnover": None} for d, c, v in zip(DATES, closes, vols, strict=True)]
    r.upsert("daily_prices", rows)
    r.upsert("scores", [
        {"stock_id": s, "date": AS_OF, "horizon": h, "total": v[2] if h == "short" else 55, "fundamental": 50,
         "technical": 70, "chips": 60, "status": "ok",
         "breakdown": {"indicators": [{"key": "breakout", "label": "突破 20 日區間", "score": 95, "weight": 0.2,
                                       "dimension_weight": 0.5, "note": None}]}}
        for s, v in STOCKS.items() for h in ("short", "long")
    ])
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO watchlist (stock_id) VALUES (%s)", [(s,) for s in STOCKS])
        for ind in {v[0] for v in STOCKS.values()}:
            cur.execute("INSERT INTO groups (name, kind) VALUES (%s, 'industry') RETURNING group_id", (ind,))
            gid = cur.fetchone()[0]
            cur.executemany("INSERT INTO group_members VALUES (%s, %s)",
                            [(gid, s) for s, v in STOCKS.items() if v[0] == ind])
    r.replace_flags(AS_OF, "news", [{"stock_id": "1101", "kind": "exclude", "reason": "現金增資：公告辦理現增"}])
    r.replace_flags(AS_OF, "event", [{"stock_id": "2303", "kind": "warn", "reason": "除權息 11/10（2 個交易日後）"}])
    r.upsert("market", [{"date": AS_OF, "light": "neutral", "inputs": {}, "group_strength": []}])
    conn.commit()
    return r


def test_quality_gate(repo):
    assert quality_gate(repo, AS_OF) == (False, "當日尚未執行資料抓取")
    _job(repo.conn, ok=False)
    ok, reason = quality_gate(repo, AS_OF)
    assert not ok and "缺少 3 檔股價" in reason
    _job(repo.conn)  # 補跑後通過（取最新一筆）
    assert quality_gate(repo, AS_OF)[0]


def test_halted_when_quality_fails(repo):
    _job(repo.conn, ok=False)
    result = run_signals(load_settings("free"), repo, AS_OF)
    assert result["status"] == "halted"
    assert repo.count_rows("signals", AS_OF) == 0
    forced = run_signals(load_settings("free"), repo, AS_OF, force=True)
    assert forced["status"] == "forced" and forced["signals"]


def test_signals_end_to_end(repo):
    _job(repo.conn)
    result = run_signals(load_settings("free"), repo, AS_OF)
    assert result["status"] == "ok"
    got = {(s["stock_id"], s["horizon"]) for s in result["signals"]}
    assert got == {("2330", "short"), ("2303", "short")}  # 中長線分數 55 未達門檻
    assert [b["stock_id"] for b in result["blocked"]] == ["1101"]

    with repo.conn.cursor() as cur:
        cur.execute("SELECT stock_id, triggers, entry_low, entry_high, stop_loss, take_profit, suggested_lots, "
                    "reason, detail FROM signals WHERE date = %s ORDER BY stock_id", (AS_OF,))
        rows = cur.fetchall()
    sid, triggers, lo, hi, stop, tp, lots, reason, detail = rows[1]
    assert sid == "2330" and set(triggers) == {"above_ma60", "ma_golden_cross", "volume_breakout_20d"}
    assert float(hi) == 107.5 and float(stop) < float(lo) < 106 < float(tp)
    assert lots == 1                                      # 100 萬資金：單檔上限 120,000 / 107.5 → 1 張
    assert "帶量突破 20 日高點" in reason and "高分理由" in reason
    assert detail["name"] == "股2330" and detail["sizing"]["limited_by"] == "單檔上限"
    assert rows[0][8]["warnings"] == ["除權息 11/10（2 個交易日後）"]

    # 重跑同一天不重複
    run_signals(load_settings("free"), repo, AS_OF)
    assert repo.count_rows("signals", AS_OF) == 2


def test_blocked_by_market_and_group_cap(repo):
    _job(repo.conn)
    repo.replace_flags(AS_OF, "market", [{"stock_id": s, "kind": "block", "reason": "大盤燈號偏空"} for s in STOCKS])
    repo.conn.commit()
    result = run_signals(load_settings("free"), repo, AS_OF)
    assert result["signals"] == []
    assert {b["stock_id"] for b in result["blocked"]} == {"2330", "2303", "1101"}

    # 解除偏空；半導體已持有 29 萬（上限 30%），新訊號只剩 1 萬額度 → 0 張
    repo.replace_flags(AS_OF, "market", [])
    with repo.conn.cursor() as cur:
        cur.execute("INSERT INTO holdings (stock_id, shares, avg_cost, opened_date) VALUES ('2454', 2900, 100, %s)",
                    (AS_OF,))
    repo.conn.commit()
    result = run_signals(load_settings("free"), repo, AS_OF)
    by = {s["stock_id"]: s for s in result["signals"]}
    assert by["2330"]["suggested_lots"] == 0
    assert by["2330"]["detail"]["sizing"]["limited_by"] == "族群上限"
