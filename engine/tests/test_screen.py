"""全市場潛力股篩選的純計算部分。"""

import numpy as np
import pandas as pd

from engine.screen import Candidate, diversify, load_screen_config, passes_stage1, pct_rank, technical_metrics

CFG = load_screen_config()


def test_pct_rank_missing_is_neutral():
    r = pct_rank({"a": 1.0, "b": 3.0, "c": None})
    assert r["b"] > r["a"] and r["c"] == 50.0
    inv = pct_rank({"a": 10.0, "b": 30.0}, higher_better=False)
    assert inv["a"] > inv["b"]


def test_passes_stage1():
    ok = {"market_cap": 2e10, "avg_turnover": 5e8, "avg_volume_lots": 3000, "close": 50, "cum_revenue_yoy": 20,
          "month_revenue_yoy": 5, "pe": 18}
    assert passes_stage1(ok, CFG["stage1"])
    assert not passes_stage1({**ok, "pe": None}, CFG["stage1"])           # 虧損
    assert not passes_stage1({**ok, "cum_revenue_yoy": -3}, CFG["stage1"])  # 營收衰退
    assert not passes_stage1({**ok, "market_cap": 5e9}, CFG["stage1"])      # 太小


def test_technical_metrics_needs_one_year():
    idx = pd.bdate_range("2025-01-01", periods=300)
    up = pd.DataFrame({"close": np.linspace(50, 100, 300)}, index=idx)
    taiex = pd.Series(np.linspace(20000, 22000, 300), index=idx)
    m = technical_metrics(up, taiex, CFG["stage2"])
    assert m["rs"] > 0 and m["vs_ma240"] > 0 and m["vs_ma60"] > 0
    assert technical_metrics(up.tail(100), taiex, CFG["stage2"]) is None


def _c(sid, ind):
    return Candidate(sid, sid, ind)


def test_diversify_caps_and_minimum():
    ranked = [_c("a1", "A"), _c("a2", "A"), _c("a3", "A"), _c("b1", "B"), _c("c1", "C"), _c("c2", "C"),
              _c("c3", "C"), _c("d1", "D")]
    # 現有自選股：B 已有 2 檔、D 沒有；同產業上限 2、每產業至少 3
    picks = diversify(ranked, {"B": 2}, target=5, max_per=3, min_per=3)
    ids = [p.stock_id for p in picks]
    assert "b1" in ids                     # B：現有 2 + 新增 1 = 3
    assert "d1" not in ids                 # D 只有 1 檔候選，湊不到 3 檔，不選
    for ind in ("A", "C"):
        n = sum(1 for p in picks if p.industry == ind)
        assert n in (0, 3)                 # 選了就要湊滿 3 檔
    assert len(ids) <= 7
