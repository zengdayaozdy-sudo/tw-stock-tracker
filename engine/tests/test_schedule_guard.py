"""排程延遲到白天才執行時略過，不在 Actions 裡空等（2026-11-24 晚間備援延遲到 09:08 才跑，空等 100 分鐘）。"""
from datetime import datetime

from engine.cli import _too_early
from engine.config import TZ

CFG = {"main_earliest": "16:00", "evening_earliest": "21:00"}


def at(h: int, m: int = 0) -> datetime:
    return datetime(2026, 11, 24, h, m, tzinfo=TZ)


def test_evening_delayed_to_morning_is_skipped():
    assert _too_early(CFG, "evening", at(9, 8))
    assert _too_early(CFG, "evening", at(20, 59))
    assert not _too_early(CFG, "evening", at(23, 10))


def test_main_before_close_data_is_skipped():
    assert _too_early(CFG, "main", at(10))
    assert not _too_early(CFG, "main", at(17, 5))


def test_after_midnight_runs_previous_day():
    # 凌晨 08:00 以前處理的是前一個交易日，資料早已公布，不略過
    assert not _too_early(CFG, "evening", at(1))
    assert not _too_early(CFG, "main", at(7, 59))


def test_missing_config_never_skips():
    assert not _too_early({}, "main", at(10))
