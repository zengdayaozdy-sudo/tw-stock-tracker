"""每日盤勢總結：盤型、均線、寬度、法人、原因歸納、隔日計畫、文字敘述（純函式）。

指數數字取自 2026-11-12 實際行情：昨收 44,257.15、開 44,180.46、高 44,525.24、低 42,943.50、收 43,091.87。
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from engine.config import load_settings
from engine.summary import facts as F
from engine.summary.narrative import WriterError, clean, rule_narrative, write_narrative
from engine.summary.plan import build_plan, holding_actions, key_levels, posture
from engine.summary.reasons import build_reasons
from engine.summary.render import plan_block, summary_block

CFG = load_settings("free").notify["daily_summary"]
AS_OF = date(2026, 11, 12)
OHLC = {"open": 44180.46, "high": 44525.24, "low": 42943.50}


def _closes(values: list[float], end: date = AS_OF) -> pd.Series:
    days = pd.bdate_range(end=pd.Timestamp(end), periods=len(values))
    return pd.Series(values, index=days, dtype=float)


def _nov12() -> pd.Series:
    # 60 天：前段 46,000 附近、11/6 急跌到 39,700，連三天反彈到 44,257，今天收 43,091.87
    base = [46500.0] * 40 + [46000, 45500, 45000, 44500, 44000, 43500, 43000, 42500, 42000, 41574.72,
                             39701.64, 41661.70, 42712.0, 44257.15]
    base = base[-59:]
    return _closes([*base, 43091.87])


def test_index_move_nov12_pattern():
    m = F.index_move(_nov12(), OHLC, AS_OF, CFG)
    assert m["change"] == pytest.approx(-1165.28)
    assert m["pct"] == pytest.approx(-0.0263, abs=1e-4)
    assert m["pattern"] == "low_down" and m["pattern_label"] == "開低走低"
    assert m["intraday_flip"] == "翻紅後收黑"
    assert m["high_vs_prev"] == pytest.approx(268.09)
    assert m["range"] == pytest.approx(1581.74)
    assert m["close_zone"] == "low"
    text = F.describe_move(m)
    assert "盤中一度翻紅、最多漲 268 點，之後一路走低" in text
    assert "終場跌 1,165 點（-2.63%）" in text and text.endswith("收在當日最低點附近")


def test_index_move_open_high_turn_black():
    s = _closes([100.0, 100.0])
    m = F.index_move(s * 100, {"open": 10080, "high": 10100, "low": 9950}, AS_OF, CFG)
    # 收盤 10000 = 昨收：開高走低但沒翻黑
    assert m["pattern_label"] == "開高走低" and "intraday_flip" not in m
    m2 = F.index_move(_closes([10000, 9960]), {"open": 10080, "high": 10100, "low": 9950}, AS_OF, CFG)
    assert m2["intraday_flip"] == "翻黑"
    assert "之後走低、終場翻黑" in F.describe_move(m2)


def test_index_move_without_ohlc_and_stale_date():
    m = F.index_move(_closes([100, 101]), {}, AS_OF, CFG)
    assert m["pattern_label"] == "上漲" and "open" not in m
    assert F.describe_move(m).startswith("加權指數終場漲 1 點")
    assert F.index_move(_closes([100, 101], end=AS_OF - timedelta(days=1)), {}, AS_OF, CFG) is None


def test_ma_context_resistance_and_cross():
    # 20 日線約 44,400：盤中高點 44,525 碰到後收在下方
    vals = [44400.0] * 19 + [44600.0, 43091.87]
    s = _closes(vals)
    mas = {m["days"]: m for m in F.ma_context(s, AS_OF, 44525.24, 42943.5, {**CFG, "ma_days": [5, 20]})}
    assert mas[20]["touched"] == "resistance" and not mas[20]["above"]
    assert mas[5]["crossed"] == "down"


def test_prior_run():
    run = F.prior_run(_nov12(), AS_OF, 3)
    assert run["pct"] == pytest.approx(44257.15 / 39701.64 - 1, abs=1e-4)


def test_breadth_and_limits():
    rows = ([{"close": 110, "prev_close": 100}] * 10 + [{"close": 95, "prev_close": 100}] * 1490
            + [{"close": 100, "prev_close": 100}] * 5)
    b = F.breadth(rows, CFG)
    assert (b["advancers"], b["decliners"], b["unchanged"], b["limit_up"]) == (10, 1490, 5, 10)
    assert F.breadth(rows[:100], CFG) is None  # 資料不完整不算


def test_contributors_points():
    rows = [
        {"stock_id": "2330", "name": "台積電", "market": "twse", "shares": 100, "close": 90, "prev_close": 100},
        {"stock_id": "2317", "name": "鴻海", "market": "twse", "shares": 100, "close": 105, "prev_close": 100},
        {"stock_id": "6488", "name": "環球晶", "market": "tpex", "shares": 100, "close": 50, "prev_close": 100},
    ]
    c = F.contributors(rows, 10000, {**CFG, "top_contributors": 2})
    # 總市值 20,000；台積電 −1,000 → −500 點；鴻海 +500 → +250 點；上櫃不算
    assert c["drags"] == [{"stock_id": "2330", "name": "台積電", "points": -500.0, "pct": -0.1}]
    assert c["boosts"][0]["points"] == 250.0 and c["estimated_change"] == -250.0


def test_flows_streak():
    d = [AS_OF - timedelta(days=i) for i in range(5)]
    by = {d[0]: {"foreign": -6.8e8, "trust": 1.2e10, "dealer": -6.5e9}, d[1]: {"foreign": -5e9, "trust": 1, "dealer": 1},
          d[2]: {"foreign": -9e9, "trust": 1, "dealer": 1}, d[3]: {"foreign": 2e9, "trust": 1, "dealer": 1}}
    fl = F.flows(by, AS_OF)
    assert fl["foreign_streak"] == 3 and fl["trust_streak"] == 4
    assert fl["total"] == round(-6.8e8 + 1.2e10 - 6.5e9)


def test_group_moves_and_overnight():
    groups = [{"name": "金融", "members": ["a", "b", "c"]}, {"name": "PCB", "members": ["d", "e", "f"]},
              {"name": "小族群", "members": ["g"]}]
    moves = {"a": 0.01, "b": 0.02, "c": 0.0, "d": -0.05, "e": -0.1, "f": -0.06, "g": 0.09}
    g = F.group_moves(groups, moves, CFG)
    assert [x["name"] for x in g["strong"]] == ["金融"] and [x["name"] for x in g["weak"]] == ["PCB"]
    sox = _closes([10000, 10500], end=AS_OF - timedelta(days=1))
    on = F.overnight({"sox": sox, "usd_twd": _closes([32.86, 32.889])}, AS_OF)
    assert on["sox"]["pct"] == pytest.approx(0.05) and on["usd_twd"]["change"] == pytest.approx(0.029)


def _facts() -> dict:
    move = F.index_move(_nov12(), OHLC, AS_OF, CFG)
    return {
        "index": move, "headline": F.describe_move(move),
        "ma": [{"days": 20, "value": 44480.0, "above": False, "gap": -0.031, "touched": "resistance"},
               {"days": 10, "value": 42900.0, "above": True, "gap": 0.004},
               {"days": 60, "value": 46250.79, "above": False, "gap": -0.068}],
        "prior_run": {"days": 3, "pct": 0.1147, "points": 4555.51},
        "breadth": {"advancers": 286, "decliners": 741, "unchanged": 82, "limit_up": 3, "limit_down": 12,
                    "total": 1600, "adv_ratio": 0.278},
        "contributors": {"estimated_change": -1150.0, "drags": [
            {"stock_id": "2330", "name": "台積電", "points": -520.0, "pct": -0.023},
            {"stock_id": "2308", "name": "台達電", "points": -60.0, "pct": -0.0425},
            {"stock_id": "2454", "name": "聯發科", "points": -55.0, "pct": -0.0351}], "boosts": []},
        "flows": {"foreign": -6.845e8, "trust": 1.2385e10, "dealer": -6.54e9, "total": 5.16e9, "foreign_streak": 11},
        "turnover": {"value": 1.0133e12, "avg": 7.5e11, "ratio": 1.351, "avg_days": 20},
        "overnight": {"sox": {"pct": 0.031, "change": 320, "value": 10564, "prev": 10244, "date": "2026-11-11"},
                      "usd_twd": {"value": 32.889, "prev": 32.86, "change": 0.029, "pct": 0.00088, "date": "2026-11-12"}},
        "groups": {"strong": [{"name": "金融保險業", "pct": 0.004}], "weak": [{"name": "PCB 與 CCL", "pct": -0.061}]},
        "news": [{"title": "台股2大原因收盤慘跌1165點", "source": "Yahoo股市／中時新聞網", "is_major_negative": False}],
    }


def test_reasons_nov12_ranked():
    reasons = build_reasons(_facts(), CFG)
    keys = [r["key"] for r in reasons]
    assert keys[0] == "profit_taking"
    assert keys[1] == "heavyweights"  # 前三檔權值股佔跌點 55%
    assert "ma20_resistance" in keys and "close_low" in keys and "breadth" in keys
    texts = {r["key"]: r["text"] for r in reasons}
    assert "台積電 -520 點" in texts["heavyweights"]
    assert texts["foreign"] == "外資今日賣超約 6.8 億元，金額不大，但已連續 11 日賣超"
    assert "三大法人合計買超約 51.6 億元" in texts["flows_divergence"]
    assert texts["sox"] == "費城半導體指數前一交易日 +3.1%，但台股走勢相反，利多沒有延續"
    assert "爆量下跌" in texts["volume"]
    assert "fx" not in keys  # 0.09% 未達門檻
    assert texts["news"].startswith("消息面：「台股2大原因收盤慘跌1165點」")


def test_reasons_up_day_mirror():
    f = _facts()
    f["index"] = {**f["index"], "change": 500.0, "close_zone": "high"}
    f["prior_run"] = {"days": 3, "pct": -0.08, "points": -3800}
    f["ma"] = [{"days": 20, "value": 43000, "above": True, "gap": 0.01, "crossed": "up"}]
    f["breadth"] = {**f["breadth"], "advancers": 900, "decliners": 200, "adv_ratio": 0.818}
    keys = [r["key"] for r in build_reasons(f, CFG)]
    assert keys[0] == "oversold_bounce"
    assert {"ma20_reclaim", "close_high", "breadth"} <= set(keys)
    assert "profit_taking" not in keys and "close_low" not in keys


def test_posture_by_regime():
    rb = {"state": "attempt", "label": "反彈嘗試"}
    assert posture("bearish", {"mode": "blocked", "watch": True}, rb)["title"] == "偏空、反彈嘗試尚未確認"
    assert "不開新倉" in posture("bearish", {"mode": "blocked"}, None)["text"]
    p = posture("bearish", {"mode": "rebound_confirmed", "size_factor": 0.5}, {"state": "confirmed", "label": "反彈確認"})
    assert p["text"].startswith("只做反彈單，部位 ×0.5")
    assert posture("bullish", {"mode": "normal"}, None)["title"] == "偏多"


def test_key_levels_nearest_and_merge():
    f = _facts()
    lv = key_levels(f["index"], f["ma"], 2)
    assert [x["label"] for x in lv["resistance"]] == ["20 日線／今日高點", "60 日線"]
    # 今日低點 42,943.5 與 10 日線 42,900 相距 0.1%，合併成一個支撐
    assert [x["label"] for x in lv["support"]] == ["今日低點／10 日線"]
    assert lv["support"][0]["value"] == 42943.5


def test_holding_actions_priority():
    holds = [
        {"stock_id": "2327", "name": "國巨*", "shares": 1000, "avg_cost": 588.8, "close": 407, "stop_loss": 540},
        {"stock_id": "2330", "name": "台積電", "shares": 1000, "avg_cost": 2000, "close": 2340, "stop_loss": 2290},
        {"stock_id": "2317", "name": "鴻海", "shares": 1000, "avg_cost": 200, "close": 240, "stop_loss": 200},
    ]
    sells = [{"stock_id": "2327", "triggers": ["score_drop", "stop_loss"], "stop_loss": 559, "reason": "跌破停損"}]
    out = holding_actions(holds, sells, CFG)
    assert [h["stock_id"] for h in out] == ["2327", "2330", "2317"]
    assert out[0]["action"].startswith("已跌破停損") and out[0]["stop_loss"] == 559
    assert out[1]["level"] == "watch" and "接近停損 2,290" in out[1]["action"]
    assert out[2]["level"] == "hold"


def test_plan_and_render():
    ctx = {"light": "bearish", "regime": {"mode": "blocked", "watch": True},
           "rebound": {"state": "attempt", "label": "反彈嘗試"},
           "holdings": [{"stock_id": "2313", "name": "華通", "shares": 2000, "avg_cost": 227.3, "close": 196,
                         "stop_loss": 208.5}],
           "sells": [{"stock_id": "2313", "triggers": ["stop_loss"], "stop_loss": 215.5, "reason": "跌破停損"}],
           "buys": [], "blocked": [{"stock_id": "3443", "name": "創意", "triggers": ["站上季線"], "reasons": ["大盤偏空"],
                                    "entry_low": 7440, "entry_high": 7940, "stop_loss": 7435, "take_profit": 8750}],
           "setups": [{"stock_id": "2330", "name": "台積電", "trigger_price": 2400, "entry_low": 2400, "entry_high": 2425,
                       "stop_loss": 2230, "take_profit": 2815}],
           "flags": {"2313": [{"filter": "event", "kind": "warn", "reason": "月營收 11/10（2 天後）"}]},
           "names": {"2313": "華通"}}
    f = _facts()
    plan = build_plan(f, ctx, CFG)
    assert plan["posture"]["tone"] == "bearish"
    assert [s["case"] for s in plan["scenarios"]] == ["強", "盤整", "弱"]
    assert "只看觀察名單是否站上轉強價" in plan["scenarios"][0]["action"]
    assert [e["kind"] for e in plan["entries"]] == ["setup", "watch"]
    assert plan["events"] == ["2313 華通：月營收 11/10（2 天後）"]
    assert any("連續賣超（目前 11 日）" in w for w in plan["watch"])

    reasons = build_reasons(f, CFG)
    narrative = rule_narrative(f["headline"], reasons, plan["posture"])
    s = {"facts": f, "reasons": reasons, "narrative": narrative}
    text = summary_block(s)
    assert text.startswith("【盤勢總結】\n加權指數開低")
    assert "法人（上市估算）：外資 -6.8 億（連 11 賣）" in text
    p = plan_block(plan)
    assert "方針：偏空、反彈嘗試尚未確認" in p
    assert "・2313 華通 196（-13.8%）：已跌破停損，明日依紀律出場，不攤平" in p
    assert "[醞釀] 2330 台積電：收盤站上轉強價 2,400 才成立" in p
    compact = plan_block(plan, compact=True)
    assert "・強｜" not in compact and "觀察重點" not in compact


class _Writer:
    name = "claude"

    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, []

    def write(self, system, user):
        self.calls.append((system, user))
        if self.error:
            raise WriterError(self.error)
        return self.text


def test_narrative_ai_and_fallback():
    f = _facts()
    reasons = build_reasons(f, CFG)
    pos = {"title": "偏空", "text": "不開新倉"}
    w = _Writer("## 總結\n- 台股今天開低走低。\n主因是漲多。")
    text, by, err = write_narrative(w, f["headline"], reasons, f["news"], pos, CFG)
    assert (text, by, err) == ("總結台股今天開低走低。主因是漲多。", "claude", None)
    system, user = w.calls[0]
    assert "不可加入任何未提供的數字" in system and "台股2大原因收盤慘跌1165點（Yahoo股市／中時新聞網）" in user

    text, by, err = write_narrative(_Writer(error="逾時"), f["headline"], reasons, [], pos, CFG)
    assert by == "rules" and err == "逾時" and text.startswith("加權指數開低")
    assert write_narrative(None, f["headline"], reasons, [], pos, CFG)[1] == "rules"
    assert rule_narrative(None, [], None).startswith("今日加權指數資料尚未更新")
    assert clean("一。" * 400, 10).endswith("。") and len(clean("一。" * 400, 10)) <= 13
