"""大盤反彈偵測、燈號修正、盤勢模式與領先型進場條件：固定假資料，不需資料庫。"""

import copy
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from engine.config import load_settings
from engine.filters.market_light import foreign_futures_oi, taiex_vs_ma60, tsmc_adr_premium
from engine.filters.rebound import (
    breadth_frame,
    breadth_recovery,
    breadth_thrust,
    compute_rebound,
    evidence_text,
    rally_state,
    short_covering,
)
from engine.signals.entry import entry_triggers, oversold_reversal, reclaim_ma20, rs_new_high, trigger_text
from engine.signals.regime import market_regime, scaled_position_cfg

AS_OF = date(2026, 11, 11)
SETTINGS = load_settings("free")
RB = SETTINGS.filters["rebound"]
ML = SETTINGS.filters["market_light"]
RISK = SETTINGS.risk


def bseries(values, end=AS_OF) -> pd.Series:
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=len(values))
    return pd.Series(np.asarray(values, dtype=float), index=idx)


def v_shape(rally: list[float], flat=60, fall=15, depth=0.15) -> pd.Series:
    """平盤 → 下跌 depth → 依 rally 的每日漲幅反彈。"""
    vals = [20000.0] * flat
    for i in range(1, fall + 1):
        vals.append(20000 * (1 - depth * i / fall))
    for r in rally:
        vals.append(vals[-1] * (1 + r))
    return bseries(vals)


EMPTY_BREADTH = breadth_frame(pd.DataFrame())


# ---------- 指數反彈 ----------

def test_no_correction_is_none():
    taiex = bseries(np.linspace(20000, 21000, 80))
    rb = compute_rebound(taiex, EMPTY_BREADTH, None, None, AS_OF, RB)
    assert rb.state == "none" and "未達修正門檻" in rb.rally["reason"]


def test_new_low_today_is_none():
    rb = compute_rebound(v_shape([]), EMPTY_BREADTH, None, None, AS_OF, RB)
    assert rb.state == "none" and rb.rally["reason"] == "今天創波段新低"


def test_power_rally_is_attempt_before_day_four():
    # 跌 15% 後三天急漲約 9%：還不到追蹤日（第 4 天），只有急漲反彈 1 點 → 反彈嘗試
    rb = compute_rebound(v_shape([0.03, 0.03, 0.03]), EMPTY_BREADTH, None, None, AS_OF, RB)
    assert rb.rally["day"] == 3 and rb.rally["active"]
    assert set(rb.evidence) == {"power_rally"}
    assert rb.state == "attempt" and rb.points == 1


def test_follow_through_day_confirms():
    taiex = v_shape([0.005, 0.004, 0.003, 0.02])
    vol = pd.Series(1.0, index=taiex.index)
    vol.iloc[-1] = 2.0  # 第 4 天量增
    rb = compute_rebound(taiex, EMPTY_BREADTH, vol, None, AS_OF, RB)
    assert rb.evidence["ftd"]["day"] == 4 and rb.evidence["ftd"]["volume_up"] is True
    assert rb.state == "confirmed"
    assert any(t.startswith("追蹤日") for t in evidence_text(rb.to_json()))


def test_follow_through_needs_volume_up():
    taiex = v_shape([0.005, 0.004, 0.003, 0.02])
    vol = pd.Series(1.0, index=taiex.index)
    vol.iloc[-1] = 0.5
    rb = compute_rebound(taiex, EMPTY_BREADTH, vol, None, AS_OF, RB)
    assert "ftd" not in rb.evidence and rb.state == "attempt"


def test_follow_through_too_early_does_not_count():
    taiex = v_shape([0.005, 0.03, 0.002, 0.001])  # 大漲在第 2 天
    rb = compute_rebound(taiex, EMPTY_BREADTH, None, None, AS_OF, RB)
    assert "ftd" not in rb.evidence


def test_rally_expires():
    cfg = copy.deepcopy(RB)
    rs = rally_state(v_shape([0.002] * 30), AS_OF, cfg)
    assert not rs["active"] and "超過反彈有效天數" in rs["reason"]


def test_rebound_ignores_future_data():
    full = v_shape([0.03, 0.03, 0.03, 0.03, 0.03])
    past_end = full.index[-3].date()
    a = compute_rebound(full, EMPTY_BREADTH, None, None, past_end, RB).to_json()
    b = compute_rebound(full[full.index <= pd.Timestamp(past_end)], EMPTY_BREADTH, None, None, past_end, RB).to_json()
    assert a == b and a["rally"]["day"] == 3


# ---------- 寬度 ----------

def _panel(n_stocks: int, down_days: int, up_days: int) -> pd.DataFrame:
    days = 40 + down_days + up_days
    idx = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=days)
    data = {}
    for k in range(n_stocks):
        vals = [100.0] * 40
        for i in range(down_days):
            vals.append(vals[-1] * (0.98 if (i + k) % 5 else 1.01))  # 八成下跌
        for _ in range(up_days):
            vals.append(vals[-1] * 1.03)
        data[f"S{k:02d}"] = vals
    return pd.DataFrame(data, index=idx)


def test_breadth_frame_counts():
    b = breadth_frame(_panel(10, 12, 0))
    assert b["count"].iloc[-1] == 10
    assert b["adv_ratio"].iloc[-1] == pytest.approx(0.2)
    assert b["pct_above_ma"].iloc[-1] < 0.2


def test_breadth_thrust_and_recovery():
    b = breadth_frame(_panel(20, 15, 8))
    thrust = breadth_thrust(b, RB["breadth_thrust"])
    assert thrust is not None and thrust["ema"] > 0.615
    rec = breadth_recovery(b, RB["breadth_recovery"])
    assert rec is not None and rec["low"] <= 0.2 and rec["now"] >= 0.5


def test_no_thrust_without_washout():
    b = breadth_frame(_panel(20, 0, 10))
    assert breadth_thrust(b, RB["breadth_thrust"]) is None


def test_breadth_and_short_covering_add_points():
    taiex = v_shape([0.01, 0.01, 0.01])
    panel = _panel(20, 15, 3)
    panel.index = taiex.index[-len(panel):]
    oi = bseries([-90000] * 5 + [-105000, -100000, -95000, -92000])
    rb = compute_rebound(taiex, breadth_frame(panel), None, oi, AS_OF, RB)
    assert "short_covering" in rb.evidence and rb.evidence["short_covering"]["covered"] == 13000
    assert rb.points >= 1


def test_short_covering_requires_cover():
    assert short_covering(bseries([-90000, -95000, -100000, -104271]), AS_OF, RB["short_covering"]) is None


def test_too_few_stocks_ignores_breadth():
    taiex = v_shape([0.01, 0.01, 0.01])
    panel = _panel(5, 15, 3)
    panel.index = taiex.index[-len(panel):]
    rb = compute_rebound(taiex, breadth_frame(panel), None, None, AS_OF, RB)
    assert rb.breadth == {} and "breadth_thrust" not in rb.evidence


# ---------- 燈號修正 ----------

def test_taiex_below_ma60_but_above_ma20_is_neutral():
    s = v_shape([0.02, 0.02, 0.02, 0.02, 0.02, 0.02])
    r = taiex_vs_ma60(s, AS_OF, ML["taiex_vs_ma60"])
    assert r["vote"] == 0 and "站回 20 日線" in r["reason"]
    r_off = taiex_vs_ma60(s, AS_OF, {**ML["taiex_vs_ma60"], "recovery_ma": None})
    assert r_off["vote"] == -1


def test_adr_premium_shrinking_from_local_rally_is_neutral():
    cfg = ML["tsmc_adr_premium"]
    n = 30
    fx = bseries([32.0] * n)
    adr = bseries([180.0] * n)
    tw_vals = [1000.0] * n
    tw_vals[-1] = 1060.0  # 台積電本地大漲，ADR 不動 → 溢價縮小
    r = tsmc_adr_premium(adr, fx, bseries(tw_vals), AS_OF, cfg)
    assert r["vote"] == 0 and "台股補漲" in r["reason"]
    adr_vals = [180.0] * n
    adr_vals[-2] = 170.0  # ADR 下跌造成溢價縮小 → 仍偏空
    r = tsmc_adr_premium(bseries(adr_vals), fx, bseries([1000.0] * n), AS_OF, cfg)
    assert r["vote"] == -1 and "ADR 5 日下跌" in r["reason"]


def test_foreign_oi_heavy_short_but_covering_is_neutral():
    cfg = ML["foreign_futures_oi"]
    assert foreign_futures_oi(bseries([-60000, -62000, -65000, -63000, -58000, -55000]), AS_OF, cfg)["vote"] == 0
    assert foreign_futures_oi(bseries([-100000, -101000, -102000, -103000, -104000, -104271]), AS_OF,
                              cfg)["vote"] == -1


# ---------- 盤勢模式 ----------

@pytest.mark.parametrize("light,state,mode,factor", [
    ("bullish", None, "normal", 1.0),
    ("neutral", "attempt", "normal", 1.0),
    ("bearish", None, "blocked", 0.0),
    ("bearish", "none", "blocked", 0.0),
    ("bearish", "attempt", "blocked", 0.0),     # 試單預設關閉：只列入反彈觀察
    ("bearish", "confirmed", "rebound_confirmed", 0.5),
])
def test_market_regime(light, state, mode, factor):
    r = market_regime(light, state, RISK)
    assert r["mode"] == mode and r["size_factor"] == factor
    leading = {"oversold_reversal", "rs_new_high"}
    assert leading.issubset(r["entry_conditions"]) == (state in ("attempt", "confirmed"))
    assert "reclaim_ma20" not in r["entry_conditions"]


def test_regime_attempt_allowed_and_watch():
    risk = copy.deepcopy(RISK)
    assert market_regime("bearish", "attempt", risk)["watch"] is True
    risk["rebound_mode"]["attempt"]["allow"] = True
    r = market_regime("bearish", "attempt", risk)
    assert r["mode"] == "rebound_attempt" and r["size_factor"] == 0.25


def test_regime_attempt_can_be_disabled():
    risk = copy.deepcopy(RISK)
    risk["rebound_mode"]["enabled"] = False
    assert market_regime("bearish", "attempt", risk)["watch"] is False
    assert market_regime("bearish", "confirmed", risk)["mode"] == "blocked"


def test_scaled_position_cfg():
    pos = RISK["position"]
    half = scaled_position_cfg(pos, 0.5)
    assert half["risk_per_trade"] == pos["risk_per_trade"] / 2
    assert half["max_position_pct"] == pos["max_position_pct"] / 2
    assert scaled_position_cfg(pos, 1.0) is pos


# ---------- 領先型進場條件 ----------

def _prices(closes, vols=None, end=AS_OF) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    v = np.asarray(vols if vols is not None else [1_000_000] * len(c), dtype=float)
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=len(c))
    return pd.DataFrame({"open": c, "high": c * 1.005, "low": c * 0.995, "close": c, "volume": v}, index=idx)


def test_oversold_reversal():
    closes = [100.0] * 40 + [96, 92, 88, 85, 83, 82] + [86.5]
    vols = [1_000_000] * (len(closes) - 1) + [1_500_000]
    hit = oversold_reversal(_prices(closes, vols), RISK["oversold_reversal"])
    assert hit is not None and hit["min_bias"] <= -0.10 and hit["volume_ratio"] == 1.5
    vols[-1] = 500_000  # 量縮反彈不算
    assert oversold_reversal(_prices(closes, vols), RISK["oversold_reversal"]) is None
    closes[-1] = 82.2  # 沒有站上前一日高點
    assert oversold_reversal(_prices(closes), RISK["oversold_reversal"]) is None


def test_rs_new_high_before_price():
    n = 130
    bench = bseries(np.linspace(20000, 16000, n))           # 大盤跌 20%
    stock = np.concatenate([np.linspace(100, 110, 60), np.linspace(110, 100, n - 60)])  # 股價跌約 9%
    p = _prices(stock)
    hit = rs_new_high(p["close"], bench, RISK["rs_new_high"])
    assert hit is not None and hit["price_gap"] <= -0.05
    # 股價自己也在高點（不是「先於股價」）→ 不算
    p2 = _prices(np.linspace(100, 130, n))
    assert rs_new_high(p2["close"], bench, RISK["rs_new_high"]) is None


def test_reclaim_ma20():
    closes = [100.0] * 30 + list(np.linspace(100, 97, 10)) + [96.8, 97.2, 99.5]
    hit = reclaim_ma20(pd.Series(closes, dtype=float), 3, RISK["reclaim_ma20"])
    assert hit is not None
    steep = [100.0] * 30 + list(np.linspace(100, 70, 15)) + [80]  # 20 日線下彎太快
    assert reclaim_ma20(pd.Series(steep, dtype=float), 3, RISK["reclaim_ma20"]) is None


def test_entry_triggers_uses_given_conditions():
    closes = [100.0] * 40 + [96, 92, 88, 85, 83, 82] + [86.5]
    vols = [1_000_000] * (len(closes) - 1) + [1_500_000]
    p = _prices(closes, vols)
    chips = pd.DataFrame(columns=["foreign_net", "trust_net"])
    assert "oversold_reversal" not in entry_triggers(p, chips, AS_OF, RISK)
    conds = market_regime("bearish", "attempt", RISK)["entry_conditions"]
    got = entry_triggers(p, chips, AS_OF, RISK, None, conds)
    assert "oversold_reversal" in got
    assert trigger_text({"oversold_reversal": got["oversold_reversal"]})[0].startswith("跌深反轉")


def test_leading_triggers_ignore_future_rows():
    closes = [100.0] * 40 + [96, 92, 88, 85, 83, 82] + [86.5] + [50, 40]
    p = _prices(closes, end=AS_OF + timedelta(days=3))
    conds = market_regime("bearish", "attempt", RISK)["entry_conditions"]
    chips = pd.DataFrame(columns=["foreign_net", "trust_net"])
    as_of = p.index[-3].date()
    assert entry_triggers(p, chips, as_of, RISK, None, conds) == \
        entry_triggers(p[p.index <= pd.Timestamp(as_of)], chips, as_of, RISK, None, conds)
