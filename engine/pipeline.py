"""收盤後資料抓取流程（第 4 節步驟 1–2）。

免費模式：證交所／櫃買公開資料抓全市場行情、估值、法人、融資券（不耗 FinMind 額度），
FinMind 只用來補自選股的財報、月營收與歷史資料。
付費模式：以 feature flag 決定是否改用 FinMind 全市場資料。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from engine.config import Settings
from engine.quality import QualityReport, check_chips, check_price_jumps, check_prices
from engine.repository import Repository
from engine.sources import finmind as fm
from engine.sources.exchanges import ExchangeClient
from engine.sources.finmind import FinMindClient

log = logging.getLogger(__name__)

MIN_MARKET_ROWS = 1500  # 上市＋上櫃普通股約 1,800 檔，低於此數視為資料不完整


@dataclass
class FetchResult:
    trade_date: date
    market_open: bool = True
    counts: dict[str, int] = field(default_factory=dict)
    quality: QualityReport = field(default_factory=QualityReport)
    finmind_calls: int = 0

    def to_dict(self) -> dict:
        return {
            "trade_date": self.trade_date.isoformat(),
            "market_open": self.market_open,
            "counts": self.counts,
            "quality": self.quality.to_dict(),
            "finmind_calls": self.finmind_calls,
        }


def universe(settings: Settings, repo: Repository) -> list[str]:
    if settings.enabled("full_market_scan"):
        return repo.all_stock_ids()
    ids = repo.watchlist()
    limit = settings.plan.watchlist_limit
    if limit and len(ids) > limit:
        log.warning("自選股 %d 檔超過免費模式上限 %d，只處理前 %d 檔", len(ids), limit, limit)
        ids = ids[:limit]
    # 持股一定要追蹤（評分、濾網、賣出提醒），即使已從自選股移除
    held = repo.holding_ids()
    return sorted(set(ids) | set(held)) if held else ids


def fetch_daily(
    settings: Settings, repo: Repository, exchange: ExchangeClient, finmind: FinMindClient, trade_date: date
) -> FetchResult:
    result = FetchResult(trade_date)
    if trade_date.weekday() >= 5:
        result.market_open = False
        return result

    # 1. 全市場行情（免費），同時更新股票清單
    prices = exchange.daily_prices(trade_date)
    # 上市、上櫃各自判斷：兩邊更新時間不同（2026-11-09 櫃買已更新、證交所尚未更新）
    latest_by_market: dict[str, date] = {}
    for r in prices:
        m = r["market"]
        if m not in latest_by_market or r["date"] > latest_by_market[m]:
            latest_by_market[m] = r["date"]
    stale = [m for m in ("twse", "tpex") if latest_by_market.get(m, date.min) < trade_date]
    if stale:
        # 官方最新資料不是今天：休市日，或其中一個市場尚未更新
        result.market_open = False
        result.counts["latest_available"] = {m: d.isoformat() for m, d in latest_by_market.items()}
        return result
    prices = [r for r in prices if r["date"] == trade_date]

    known = repo.known_stock_ids()
    new_stocks = [
        {"stock_id": r["stock_id"], "name": r["name"], "market": r["market"]}
        for r in prices
        if r["stock_id"] not in known
    ]
    if new_stocks:
        repo.upsert_stocks(new_stocks)
    result.counts["prices"] = repo.upsert("daily_prices", prices)
    result.counts["market_caps"] = repo.update_market_caps(trade_date)

    ids = universe(settings, repo)
    result.quality = check_prices(prices, trade_date, ids, MIN_MARKET_ROWS)

    # 2. 估值、法人、融資券（免費，全市場）
    known = repo.known_stock_ids()
    valuations = [r for r in exchange.valuations(trade_date) if r["stock_id"] in known]
    result.counts["valuations"] = repo.upsert("valuations", valuations)

    inst = [r for r in exchange.institutional(trade_date) if r["stock_id"] in known]
    result.counts["institutional"] = repo.upsert_chips(inst)
    _merge(result.quality, check_chips(inst, ids, "三大法人"))

    margin = [r for r in exchange.margin(trade_date) if r["stock_id"] in known]
    result.counts["margin"] = repo.upsert_chips(margin)
    # 當日融資融券依市場分開計數：櫃買比證交所晚公布（2026-11-11 證交所 21:09 已有、櫃買要到 22 點後）
    for m in ("twse", "tpex"):
        result.counts[f"margin_today_{m}"] = sum(1 for r in margin if r["date"] == trade_date and r.get("market") == m)
    result.counts["margin_today"] = result.counts["margin_today_twse"] + result.counts["margin_today_tpex"]
    # 證交所融資券晚間才公布、OpenAPI 晚一個交易日，當天缺資料屬正常：只列警告，不暫停訊號
    margin_check = check_chips([r for r in margin if r["date"] == trade_date], ids, "當日融資融券")
    result.quality.warnings.extend(margin_check.errors + margin_check.warnings)

    # 借券賣出餘額（外資避險、放空的主要管道）；缺資料只列警告
    try:
        sbl = [r for r in exchange.securities_lending(trade_date) if r["stock_id"] in known]
        result.counts["sbl"] = repo.upsert_chips(sbl)
    except Exception as e:
        result.quality.warnings.append(f"借券賣出餘額抓取失敗：{e}")

    # 董監質押比（約每月更新一次的彙總表）
    try:
        ratios, as_of = exchange.pledge_ratios()
        if as_of:
            result.counts["pledge"] = repo.set_pledge_ratios({k: v for k, v in ratios.items() if k in known}, as_of)
    except Exception as e:
        result.quality.warnings.append(f"董監質押比抓取失敗：{e}")

    # 3. 交易狀態（處置、注意、全額交割）
    try:
        repo.set_status_flags(exchange.status_flags(trade_date))
        result.quality.warnings.extend(f"交易狀態部分抓取失敗：{e}" for e in exchange.last_errors)
    except Exception as e:  # 公告格式異動不影響主流程
        result.quality.warnings.append(f"處置／注意／全額交割股抓取失敗：{e}")

    # 4. FinMind：自選股財報與月營收（只在可能有新資料時抓）
    before = finmind.quota.calls_this_hour
    result.counts["financials"] = refresh_financials(repo, finmind, ids, trade_date)
    result.counts["shareholding"] = refresh_shareholding(repo, finmind, ids, trade_date)
    result.counts["sbl_history"] = refresh_sbl_history(repo, finmind, ids, trade_date)
    result.finmind_calls = finmind.quota.calls_this_hour - before

    # 5. 除權息、分割、減資參考價（只抓最近有除權息事件的股票）
    result.counts["adjustments"] = refresh_adjustments(repo, finmind, ids, trade_date)
    result.finmind_calls = finmind.quota.calls_this_hour - before

    # 6. 跳空檢查（警告）
    yesterday = _previous_closes(repo, ids, trade_date)
    today = {r["stock_id"]: r["close"] for r in prices if r["stock_id"] in set(ids)}
    result.quality.warnings.extend(check_price_jumps(today, yesterday))
    return result


def _merge(target: QualityReport, other: QualityReport) -> None:
    target.errors.extend(other.errors)
    target.warnings.extend(other.warnings)


def _previous_closes(repo: Repository, ids: list[str], trade_date: date) -> dict[str, float]:
    if not ids:
        return {}
    with repo.conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (stock_id) stock_id, close FROM daily_prices "
            "WHERE stock_id = ANY(%s) AND date < %s ORDER BY stock_id, date DESC",
            (ids, trade_date),
        )
        return {s: float(c) for s, c in cur.fetchall() if c is not None}


def expected_revenue_period(today: date) -> str:
    """今天應該已經能取得的最新月營收期別（上個月）。"""
    first = today.replace(day=1)
    prev = first - timedelta(days=1)
    return f"{prev.year:04d}-{prev.month:02d}"


def expected_quarter_period(today: date) -> str:
    """依法定期限推算今天應已公布的最新季別。"""
    y = today.year
    deadlines = [(date(y, 3, 31), f"{y - 1}Q4"), (date(y, 5, 15), f"{y}Q1"),
                 (date(y, 8, 14), f"{y}Q2"), (date(y, 11, 14), f"{y}Q3")]
    latest = f"{y - 1}Q3"
    for deadline, period in deadlines:
        if today >= deadline:
            latest = period
    return latest


def refresh_financials(repo: Repository, finmind: FinMindClient, ids: list[str], today: date) -> int:
    count = 0
    want_month = expected_revenue_period(today)
    want_quarter = expected_quarter_period(today)
    for sid in ids:
        if (repo.latest_period(sid, "month") or "") < want_month:
            rows = finmind.fetch("month_revenue", sid, today - timedelta(days=120))
            count += repo.upsert("financials", fm.parse_month_revenue(rows))
        if (repo.latest_period(sid, "quarter") or "") < want_quarter:
            rows = finmind.fetch("income_statement", sid, today - timedelta(days=400))
            rows += finmind.fetch("balance_sheet", sid, today - timedelta(days=400))
            count += repo.upsert("financials", fm.parse_statements(rows))
    return count


def refresh_adjustments(repo: Repository, finmind: FinMindClient, ids: list[str], today: date,
                        force: bool = False) -> int:
    """最近 5 天內有除權息事件，或每週一，才抓參考價（每檔 3 次 FinMind 呼叫）。"""
    if not ids:
        return 0
    if not force and today.weekday() != 0:
        with repo.conn.cursor() as cur:
            cur.execute("SELECT DISTINCT stock_id FROM events WHERE stock_id = ANY(%s) AND event_type = '除權息' "
                        "AND date BETWEEN %s AND %s", (ids, today - timedelta(days=5), today))
            ids = [r[0] for r in cur.fetchall()]
    count = 0
    start = today - timedelta(days=30 if not force else 365 * 6)
    for sid in ids:
        for kind, key in fm.ADJUSTMENT_DATASETS.items():
            count += repo.upsert("price_adjustments", fm.parse_adjustments(finmind.fetch(key, sid, start), kind))
    return count


SHAREHOLDING_MIN_DAYS = 30     # 外資持股變化需要至少 20 個交易日的歷史
SHAREHOLDING_HISTORY_DAYS = 400


def refresh_shareholding(repo: Repository, finmind: FinMindClient, ids: list[str], today: date) -> int:
    """外資持股比例：歷史不足的股票一次補一年（任何一天都會補），其餘每週一補最近 10 天。

    原本只在週一抓最近 10 天，新加入的股票要一個多月才有足夠歷史，外資持股變化指標一直以中性分計。
    """
    have = repo.holding_history_days(ids)
    count = 0
    for sid in ids:
        if have.get(sid, 0) < SHAREHOLDING_MIN_DAYS:
            start = today - timedelta(days=SHAREHOLDING_HISTORY_DAYS)
        elif today.weekday() == 0:
            start = today - timedelta(days=10)
        else:
            continue
        rows = finmind.fetch("shareholding", sid, start)
        count += repo.upsert_chips(fm.parse_shareholding(rows))
    return count


# 每檔回補的 FinMind 呼叫數：股價、估值、法人、融資券、外資持股、月營收、損益表、資產負債表＋除權息／分割／減資
BACKFILL_CALLS_PER_STOCK = 9 + len(fm.ADJUSTMENT_DATASETS)  # 含借券賣出餘額


def backfill_capacity(remaining_calls: int, cap: int, floor: int = 0) -> int:
    """依本小時剩餘額度決定這次回補幾檔，不讓排程卡在等下一個整點。"""
    return max(floor, min(cap, remaining_calls // BACKFILL_CALLS_PER_STOCK))


SBL_MIN_DAYS = 30


def refresh_sbl_history(repo: Repository, finmind: FinMindClient, ids: list[str], today: date) -> int:
    """借券賣出餘額歷史不足的股票，從 FinMind 補一年（之後每天由交易所資料累積）。"""
    have = repo.sbl_history_days(ids)
    count = 0
    for sid in ids:
        if have.get(sid, 0) >= SBL_MIN_DAYS:
            continue
        rows = finmind.fetch("short_sale_balances", sid, today - timedelta(days=SHAREHOLDING_HISTORY_DAYS))
        count += repo.upsert_chips(fm.parse_sbl(rows))
    return count


def backfill(repo: Repository, finmind: FinMindClient, ids: list[str], years: int, today: date) -> dict[str, int]:
    """新加入的自選股回補歷史資料（股價、估值、法人、融資券、財報、月營收）。"""
    start = today.replace(year=today.year - years)
    counts = {k: 0 for k in ("prices", "valuations", "institutional", "margin", "financials")}
    for sid in ids:
        counts["prices"] += repo.upsert("daily_prices", fm.parse_prices(finmind.fetch("price", sid, start)))
        counts["valuations"] += repo.upsert("valuations", fm.parse_per(finmind.fetch("per", sid, start)))
        counts["institutional"] += repo.upsert_chips(
            fm.parse_institutional(finmind.fetch("institutional", sid, start))
        )
        counts["margin"] += repo.upsert_chips(fm.parse_margin(finmind.fetch("margin", sid, start)))
        counts["shareholding"] = counts.get("shareholding", 0) + repo.upsert_chips(
            fm.parse_shareholding(finmind.fetch("shareholding", sid, start)))
        counts["sbl"] = counts.get("sbl", 0) + repo.upsert_chips(
            fm.parse_sbl(finmind.fetch("short_sale_balances", sid, start)))
        counts["financials"] += repo.upsert(
            "financials", fm.parse_month_revenue(finmind.fetch("month_revenue", sid, start))
        )
        statements = finmind.fetch("income_statement", sid, start) + finmind.fetch("balance_sheet", sid, start)
        counts["financials"] += repo.upsert("financials", fm.parse_statements(statements))
        for kind, key in fm.ADJUSTMENT_DATASETS.items():
            counts["adjustments"] = counts.get("adjustments", 0) + repo.upsert(
                "price_adjustments", fm.parse_adjustments(finmind.fetch(key, sid, start), kind))
        repo.conn.commit()
        log.info("已回補 %s", sid)
    return counts
