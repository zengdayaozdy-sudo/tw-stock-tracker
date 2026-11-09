"""新股回補檔數依剩餘額度決定。"""

from engine.config import load_settings
from engine.pipeline import BACKFILL_CALLS_PER_STOCK, backfill_capacity


def test_calls_per_stock():
    assert BACKFILL_CALLS_PER_STOCK == 12


def test_capacity_uses_remaining_budget():
    assert backfill_capacity(540, 40) == 40          # 額度充足時以上限為準
    assert backfill_capacity(240, 40) == 20
    assert backfill_capacity(10, 40) == 0            # 不夠一檔就不補，避免等到下一個整點
    assert backfill_capacity(-5, 40, floor=1) == 1


def test_config_present():
    cfg = load_settings("free").sources["backfill_new"]
    assert cfg["max_per_run"] >= 10 and cfg["min_per_run"] == 0
