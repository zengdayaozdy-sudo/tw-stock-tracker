from datetime import date

from engine.pipeline import expected_quarter_period, expected_revenue_period
from engine.quality import check_chips, check_price_jumps, check_prices


def _price(sid, d=date(2026, 11, 9), **kw):
    row = {"stock_id": sid, "date": d, "open": 100, "high": 105, "low": 99, "close": 103, "volume": 1000}
    row.update(kw)
    return row


def test_check_prices_ok():
    report = check_prices([_price("2330"), _price("2317")], date(2026, 11, 9), ["2330", "2317"])
    assert report.ok


def test_missing_watchlist_stock_is_error():
    report = check_prices([_price("2330")], date(2026, 11, 9), ["2330", "2317"])
    assert not report.ok
    assert "2317" in report.errors[0]


def test_wrong_date_is_error():
    report = check_prices([_price("2330", d=date(2026, 11, 6))], date(2026, 11, 9), ["2330"])
    assert not report.ok


def test_bad_values():
    report = check_prices([_price("2330", close=-1), _price("2317", high=90)], date(2026, 11, 9), [])
    assert len(report.errors) == 2


def test_too_few_market_rows():
    report = check_prices([_price("2330")], date(2026, 11, 9), [], min_market_rows=1500)
    assert not report.ok


def test_no_trade_is_not_error():
    report = check_prices([_price("2330", open=None, high=None, low=None, close=None, volume=0)],
                          date(2026, 11, 9), [])
    assert report.ok


def test_price_jump_warning():
    assert check_price_jumps({"2330": 120}, {"2330": 100})
    assert not check_price_jumps({"2330": 109}, {"2330": 100})


def test_chips_tolerance():
    ids = [str(1000 + i) for i in range(30)]
    rows = [{"stock_id": s} for s in ids[:28]]
    report = check_chips(rows, ids, "法人")
    assert report.ok and report.warnings
    report = check_chips(rows[:10], ids, "法人")
    assert not report.ok


def test_expected_periods():
    assert expected_revenue_period(date(2026, 11, 9)) == "2026-10"
    assert expected_revenue_period(date(2026, 1, 5)) == "2025-12"
    assert expected_quarter_period(date(2026, 11, 9)) == "2026Q2"
    assert expected_quarter_period(date(2026, 11, 14)) == "2026Q3"
    assert expected_quarter_period(date(2026, 2, 1)) == "2025Q3"
    assert expected_quarter_period(date(2026, 4, 1)) == "2025Q4"


def test_odd_lot_only_is_not_error():
    rows = [_price("1538", open=None, high=None, low=None, close=None, volume=386)]
    assert check_prices(rows, date(2026, 11, 9), []).ok
    rows = [_price("2330", open=None, high=None, low=None, close=None, volume=5000)]
    assert not check_prices(rows, date(2026, 11, 9), []).ok
