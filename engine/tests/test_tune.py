"""調參的選擇規則：先看回撤與交易數門檻，符合者比年化報酬。"""

from engine.backtest.tune import Trial, current_value, load_tune_config
from engine.config import load_settings

OBJ = {"max_drawdown": -0.15, "min_trades": 30}


def _t(cagr, mdd, trades=50):
    return Trial({}, {"cagr": cagr, "max_drawdown": mdd, "trades": trades}, 0.0)


def test_feasible_beats_higher_return():
    safe, risky = _t(0.20, -0.12), _t(0.60, -0.30)
    assert safe.feasible(OBJ) and not risky.feasible(OBJ)
    assert max([risky, safe], key=lambda t: t.key(OBJ)) is safe


def test_among_feasible_pick_highest_return():
    assert max([_t(0.20, -0.10), _t(0.25, -0.14)], key=lambda t: t.key(OBJ)).metrics["cagr"] == 0.25


def test_too_few_trades_not_feasible():
    assert not _t(0.5, -0.05, trades=5).feasible(OBJ)


def test_none_feasible_pick_smallest_drawdown():
    assert max([_t(0.6, -0.30), _t(0.2, -0.18)], key=lambda t: t.key(OBJ)).metrics["max_drawdown"] == -0.18


def test_tune_params_exist_in_config():
    settings = load_settings("free")
    for path in load_tune_config()["params"]:
        current_value(settings, path)  # 路徑寫錯會 KeyError
