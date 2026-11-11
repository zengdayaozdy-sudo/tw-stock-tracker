"""醞釀名單：判斷、價位與事件研究。固定假資料，不需資料庫。"""

from datetime import date

import numpy as np
import pandas as pd

from engine.backtest.setup_study import _simulate, run_study
from engine.config import load_settings
from engine.scoring.indicators import StockFrames
from engine.signals.levels import round_tick
from engine.signals.setup import setup_levels, setup_signs, setup_text

AS_OF = date(2026, 11, 11)
RISK = load_settings("free").risk
CFG = RISK["setup_watch"]


def _prices(closes, end=AS_OF) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=len(c))
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6}, index=idx)


DEEP = [100.0] * 40 + [96, 92, 88, 85, 83, 82, 81.5]


def test_not_deep_returns_none():
    assert setup_signs(_prices([100.0] * 50), None, None, AS_OF, CFG) is None


def test_signs_rs_institutional_margin():
    p = _prices(DEEP)
    bench = pd.Series(np.concatenate([np.full(40, 20000.0), np.linspace(20000, 15000, 7)]), index=p.index)
    chips = pd.DataFrame({"foreign_net": 1000.0, "trust_net": 500.0,
                          "margin_balance": np.concatenate([np.full(41, 10000.0), np.linspace(10000, 8500, 6)])},
                         index=p.index)
    signs = setup_signs(p, chips, bench, AS_OF, CFG)
    assert set(signs) == {"rs_up", "institutional_buy", "margin_drop"}
    assert setup_text(signs)[0].startswith("抗跌")
    # 大盤沒跌、法人賣、融資增 → 跌深但沒有強勢跡象
    flat = pd.Series(20000.0, index=p.index)
    weak = pd.DataFrame({"foreign_net": -1000.0, "trust_net": 0.0, "margin_balance": 10000.0}, index=p.index)
    assert setup_signs(p, weak, flat, AS_OF, CFG) == {}


def test_setup_levels_order():
    lv = setup_levels(_prices(DEEP), AS_OF, CFG, RISK)
    assert lv["stop_loss"] < lv["entry_low"] == lv["trigger_price"] <= lv["entry_high"] < lv["take_profit"]
    assert lv["trigger_price"] > round_tick(81.5 * 1.01)          # 高於今天最高價
    assert lv["entry_high"] * (1 - CFG["max_stop"]) <= lv["stop_loss"] + 0.05
    assert lv["target_basis"] == "20 日線（均值回歸）"


def test_simulate_fill_and_exits():
    p = _prices(DEEP + [83.5, 84.5, 90])
    i = len(DEEP) - 1
    lv = setup_levels(p.iloc[: i + 1], AS_OF, CFG, RISK)
    t = _simulate(p, i, lv, CFG, RISK["costs"])
    assert t is not None and t["reason"] in ("達目標", "到期", "停損")
    # 之後一路破底：沒觸及轉強價就不進場
    down = _prices(DEEP + [80, 79, 78, 77, 76, 75])
    assert _simulate(down, i, lv, CFG, RISK["costs"]) is None


def test_run_study_counts_episode_once():
    p = _prices(DEEP + [81, 80.5, 80, 85, 90, 92] + [92] * 25, end=date(2026, 12, 31))
    f = StockFrames("1111", p, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), benchmark=None)
    res = run_study({"1111": f}, p.index[0].date(), p.index[-1].date(), RISK)
    g = res["groups"]["對照組：跌深、沒有強勢跡象"]
    assert g["events"] == 1
