"""第 16 節驗收條件中可自動驗證的部分（其餘見各模組測試與 docs/runbook.md 的驗收清單）。

1. PLAN=free、30 檔自選股跑完一天（最壞情況：週一、財報都要更新、10 檔新股回補），FinMind 用量在免費額度內
2. 切換 PLAN=sponsor 後功能解鎖且不需改程式
"""

from datetime import date

from engine.config import load_settings
from engine.filters.runner import FINMIND_SERIES
from engine.pipeline import backfill, refresh_adjustments, refresh_financials, refresh_shareholding

MONDAY = date(2026, 11, 9)


class CountingFinMind:
    def __init__(self):
        self.calls = 0

    def fetch(self, key, sid, start, end=None):
        self.calls += 1
        return []


class StubRepo:
    """所有股票都沒有任何財報（最壞情況），也沒有除權息事件。"""

    def __init__(self):
        self.conn = self

    def latest_period(self, sid, period_type):
        return None

    def upsert(self, table, rows):
        return len(list(rows))

    def upsert_chips(self, rows):
        return len(list(rows))

    def holding_history_days(self, ids):
        return {}  # 最壞情況：都沒有外資持股歷史

    def commit(self):
        pass


def test_free_plan_daily_finmind_usage_within_quota():
    settings = load_settings("free")
    fm, repo = CountingFinMind(), StubRepo()
    ids = [str(1101 + i) for i in range(30)]
    refresh_financials(repo, fm, ids, MONDAY)              # 月營收＋季報（損益、資產負債）
    refresh_shareholding(repo, fm, ids, MONDAY)            # 外資持股（歷史不足者補一年）
    refresh_adjustments(repo, fm, ids, MONDAY)             # 週一：除權息、分割、減資參考價
    for _ in FINMIND_SERIES:                               # 大盤燈號序列與 0050
        fm.fetch("series", None, MONDAY)
    backfill(repo, fm, ids[:10], 3, MONDAY)                # backfill-new 每次最多 10 檔（每檔 11 次）
    budget = int(settings.sources["finmind"]["hourly_limit"]["free"] * settings.sources["finmind"]["safety_margin"])
    assert fm.calls <= budget, f"FinMind 呼叫 {fm.calls} 次，超過每小時預算 {budget}"
    assert fm.calls == 30 * 3 + 30 + 30 * 3 + len(FINMIND_SERIES) + 10 * 11


def test_plan_switch_unlocks_without_code_change():
    free, sponsor = load_settings("free"), load_settings("sponsor")
    for feature in ("full_market_scan", "market_percentile", "top_percent_list", "market_wide_groups", "full_backtest"):
        assert not free.enabled(feature) and sponsor.enabled(feature)
    assert free.locked_features() and len(sponsor.locked_features()) < len(free.locked_features())
