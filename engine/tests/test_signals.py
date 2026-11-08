"""訊號、參考價位、風控張數單元測試（固定假資料）。"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine.config import load_settings
from engine.signals.entry import entry_triggers, trigger_text
from engine.signals.levels import compute_levels, pe_river, round_tick, stop_price, tick_size
from engine.signals.selection import group_top_list, select, top_percent_list
from engine.signals.sizing import position_size

AS_OF = date(2026, 11, 6)
SETTINGS = load_settings("free")
RISK = SETTINGS.risk


def frame(closes, volumes=None, end=AS_OF) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=len(closes))
    vol = np.full(len(closes), 1_000_000.0) if volumes is None else np.asarray(volumes, dtype=float)
    return pd.DataFrame({"open": closes, "high": closes * 1.01, "low": closes * 0.99, "close": closes,
                         "volume": vol}, index=idx)


def chips(nets, end=AS_OF) -> pd.DataFrame:
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=len(nets))
    return pd.DataFrame({"foreign_net": nets, "trust_net": [0] * len(nets)}, index=idx)


EMPTY_CHIPS = pd.DataFrame(columns=["foreign_net", "trust_net"], index=pd.DatetimeIndex([]))


# ---------- 名單 ----------

def test_group_top_list():
    totals = {"A": 90, "B": 80, "C": 75, "D": 70, "E": 55, "F": 65}
    groups = {"甲": ("industry", {"A", "B", "C", "D"}), "乙": ("custom", {"D", "E"})}
    out = group_top_list(totals, groups, top_n=3, min_total=60)
    assert set(out) == {"A", "B", "C", "D", "F"}
    assert out["D"] == ["乙"]           # 在甲排第 4，在乙排第 1
    assert "E" not in out               # 族群內前 3 但低於門檻
    assert out["F"] == ["未分類"]


def test_top_percent_list():
    totals = {str(i): float(i) for i in range(100)}
    out = top_percent_list(totals, 0.05)
    assert sorted(out, key=int) == ["95", "96", "97", "98", "99"]
    assert select(totals, {}, True, SETTINGS.scoring).keys() == {"95", "96", "97", "98", "99"}


# ---------- 進場條件 ----------

def test_above_ma60_cross():
    closes = [100.0] * 70 + [95.0] * 9 + [110.0]  # 前一天在季線下，今天站上
    t = entry_triggers(frame(closes), EMPTY_CHIPS, AS_OF, RISK)
    assert "above_ma60" in t
    assert "above_ma60" not in entry_triggers(frame([110.0] * 80), EMPTY_CHIPS, AS_OF, RISK)  # 早已在季線上


def test_golden_cross():
    closes = list(np.linspace(120, 100, 40)) + [100, 100, 100, 100, 120]
    t = entry_triggers(frame(closes), EMPTY_CHIPS, AS_OF, RISK)
    assert "ma_golden_cross" in t


def test_volume_breakout_requires_volume():
    closes = [100.0] * 30 + [105.0]
    vols = [1_000_000] * 30 + [2_000_000]
    assert "volume_breakout_20d" in entry_triggers(frame(closes, vols), EMPTY_CHIPS, AS_OF, RISK)
    vols[-1] = 1_200_000  # 量能不足
    assert "volume_breakout_20d" not in entry_triggers(frame(closes, vols), EMPTY_CHIPS, AS_OF, RISK)


def test_institutional_streak():
    p = frame([100.0] * 30)
    t = entry_triggers(p, chips([-5, 10, 20, 30]), AS_OF, RISK)
    assert t["institutional_buy_streak"]["days"] == 3
    assert "institutional_buy_streak" not in entry_triggers(p, chips([10, 20, -1]), AS_OF, RISK)
    # 當日沒有法人資料不判斷
    stale = chips([10, 20, 30], end=date(2026, 11, 5))
    assert "institutional_buy_streak" not in entry_triggers(p, stale, AS_OF, RISK)
    assert trigger_text(t) == ["法人連 3 日買超"]


def test_no_trigger_without_today_price():
    closes = [100.0] * 30 + [105.0]
    vols = [1_000_000] * 30 + [2_000_000]
    assert entry_triggers(frame(closes, vols, end=date(2026, 11, 5)), EMPTY_CHIPS, AS_OF, RISK) == {}


def test_triggers_ignore_future_rows():
    closes = [100.0] * 30 + [105.0] + [50.0] * 3
    vols = [1_000_000] * 30 + [2_000_000] + [1_000_000] * 3
    p = frame(closes, vols, end=date(2026, 11, 11))
    assert "volume_breakout_20d" in entry_triggers(p, EMPTY_CHIPS, AS_OF, RISK)


# ---------- 價位 ----------

@pytest.mark.parametrize("price,tick", [(9.99, 0.01), (10, 0.05), (55, 0.1), (100, 0.5), (999, 1), (1000, 5)])
def test_tick_size(price, tick):
    assert tick_size(price) == tick


def test_round_tick():
    assert round_tick(123.3, "down") == 123.0
    assert round_tick(123.3, "up") == 123.5
    assert round_tick(1003, "down") == 1000
    assert round_tick(47.33, "up") == 47.35


def test_stop_price_methods():
    cfg = dict(RISK["stop_loss"])
    assert stop_price(100, 95, cfg) == (92.0, "進場價 −8%")
    assert stop_price(100, 95, cfg, bearish=True)[0] == 95.0
    cfg["method"] = "ma60"
    stop, basis = stop_price(100, 95, cfg)
    assert basis == "跌破季線" and stop == 94.0  # 95 × 0.99 = 94.05 → 往下取整
    stop, basis = stop_price(100, 80, cfg)        # 季線太遠（20%）改用百分比
    assert basis.startswith("進場價")


def test_pe_river():
    idx = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=300)
    val = pd.DataFrame({"pe": np.linspace(10, 20, 300)}, index=idx)
    r = pe_river(200, val, AS_OF, RISK["pe_river"])
    assert r["pe"] == 20 and r["low_price"] == pytest.approx(200 / 20 * r["low_pe"], rel=1e-3)
    assert r["low_price"] < 200 < r["high_price"] or r["high_price"] <= 200
    assert pe_river(200, val.iloc[:50], AS_OF, RISK["pe_river"]) is None  # 歷史不足


def test_compute_levels():
    closes = np.concatenate([np.linspace(80, 100, 120), np.full(10, 100.0)])
    closes[-1] = 102.0
    p = frame(closes)
    p.loc[p.index[-60], "high"] = 120.0  # 前波壓力
    lv = compute_levels(p, None, AS_OF, RISK)
    assert lv.close == 102
    assert lv.entry_high == 103.5                      # 102 × 1.01 = 103.02 → 往上取整
    assert lv.entry_low < lv.close
    assert lv.stop_loss < lv.entry_low
    assert lv.stop_loss == 93.8                        # 102 × 0.92 = 93.84
    assert lv.take_profit == 120 and lv.detail["target_basis"] == "前波壓力"


def test_levels_fallbacks():
    p = frame([100.0] * 70)
    lv = compute_levels(p, None, AS_OF, RISK)
    assert lv.detail["entry_basis"] == "近 20 日低點"   # 低點 99 是最近的支撐
    # 沒有壓力位：目標 = 收盤 + 2 × 停損距離
    assert lv.take_profit == round_tick(100 + 2 * (100 - lv.stop_loss), "down")
    assert compute_levels(frame([100.0] * 70, end=date(2026, 11, 5)), None, AS_OF, RISK) is None


# ---------- 風控張數 ----------

POS = RISK["position"]


def test_position_size_by_risk():
    # 100 萬 × 1.5% = 15,000 風險預算；每股風險 15 元 → 1,000 股（單檔上限 1,200 股）→ 1 張
    s = position_size(1_000_000, 100, 85, POS)
    assert s["lots"] == 1 and s["limited_by"] == "單筆風險"
    assert s["risk_amount"] == 15000
    # 每股風險 5 元 → 3,000 股，但單檔上限 12% 只能買 1,200 股
    assert position_size(1_000_000, 100, 95, POS)["limited_by"] == "單檔上限"


def test_position_size_capped_by_single_position():
    # 停損很近時改受單檔 12% 上限限制：120,000 / 50 = 2,400 股 → 2 張
    s = position_size(1_000_000, 50, 49.5, POS)
    assert s["lots"] == 2 and s["limited_by"] == "單檔上限"


def test_position_size_odd_lot_and_limits():
    s = position_size(1_000_000, 1000, 920, POS)
    assert s["lots"] == 0 and s["odd_shares"] == 120  # 120,000 / 1,000 = 120 股
    assert any("零股" in n for n in s["notes"])
    full = position_size(1_000_000, 100, 92, POS, holdings_count=8)
    assert full["lots"] == 0 and full["limited_by"] == "持股檔數"
    grp = position_size(1_000_000, 100, 92, POS, group_exposure=290_000)
    assert grp["lots"] == 0 and grp["limited_by"] == "族群上限"  # 只剩 10,000 額度
    assert position_size(0, 100, 92, POS)["lots"] == 0
