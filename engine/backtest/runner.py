"""回測執行（第 13 節）：資料準備、調參／保留驗證區間、參數覆寫、可重現性。

- 調參區間（tune）：起日到保留區間前一天，可重複執行、調整參數（--set）
- 保留驗證區間（holdout）：最後 holdout_months 個月，只允許執行一次（紀錄在 backtest_runs）
- 相同設定與相同資料 → 相同指紋 → 相同結果
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd
import yaml

from engine.adjust import adjust_points
from engine.backtest.plan import Planner
from engine.backtest.report import build_metrics, render_markdown
from engine.backtest.sim import simulate
from engine.config import ConfigError, Settings, _validate_scoring
from engine.repository import Repository
from engine.scoring.loader import load_frames, load_groups

log = logging.getLogger(__name__)
OVERRIDABLE = ("scoring", "risk", "filters", "backtest")


class BacktestError(RuntimeError):
    """回測無法執行（例如保留區間已驗證過）。"""


# ---------- 參數覆寫 ----------

def apply_overrides(settings: Settings, overrides: dict[str, str]) -> Settings:
    """例如 {"risk.stop_loss.percent": "0.06", "scoring.weights.short.technical": "0.45"}。"""
    changed = {k: copy.deepcopy(getattr(settings, k)) for k in OVERRIDABLE}
    for path, raw in overrides.items():
        parts = path.split(".")
        if parts[0] not in OVERRIDABLE or len(parts) < 2:
            raise ConfigError(f"只能覆寫 {'、'.join(OVERRIDABLE)} 底下的設定：{path}")
        node = changed[parts[0]]
        for key in parts[1:-1]:
            if not isinstance(node, dict) or key not in node:
                raise ConfigError(f"找不到設定：{path}")
            node = node[key]
        if not isinstance(node, dict) or parts[-1] not in node:
            raise ConfigError(f"找不到設定：{path}")
        node[parts[-1]] = yaml.safe_load(raw)
    _validate_scoring(changed["scoring"])
    return dataclasses.replace(settings, **changed)


def parse_sets(items: list[str] | None) -> dict[str, str]:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise ConfigError(f"--set 格式為 路徑=值：{item}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


# ---------- 期間 ----------

def periods(settings: Settings, end: date) -> dict[str, tuple[date, date]]:
    cfg = settings.backtest
    start = date.fromisoformat(str(cfg["start"]))
    holdout_start = (pd.Timestamp(end) - pd.DateOffset(months=cfg["holdout_months"])).date() + timedelta(days=1)
    if holdout_start <= start:
        raise BacktestError("保留驗證區間早於回測起日，請調整 backtest.yaml")
    return {"tune": (start, holdout_start - timedelta(days=1)), "holdout": (holdout_start, end), "full": (start, end)}


# ---------- 資料 ----------

def universe_for_backtest(settings: Settings, repo: Repository) -> tuple[list[str], list[str]]:
    """回傳 (股票清單, 注意事項)。"""
    warnings = ["消息面無法可靠回測，回測未包含排雷濾網",
                "處置／注意／全額交割沒有歷史資料，流動性只依日均量過濾",
                "市值以目前股數 × 當日收盤估算"]
    if settings.enabled("full_backtest"):
        with repo.conn.cursor() as cur:
            cur.execute("SELECT stock_id FROM stocks ORDER BY stock_id")  # 含已下市，避免倖存者偏差
            ids = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT count(*) FROM stocks WHERE delisted_date IS NOT NULL")
            if cur.fetchone()[0] == 0:
                warnings.append("資料庫尚無已下市股票，結果可能有倖存者偏差（docs/open-questions.md #20）")
        return ids, warnings
    ids = repo.watchlist()
    warnings.insert(0, "免費模式只回測自選股：自選股是事後挑選的，結果有選擇偏差，不能代表整體模型績效")
    return ids, warnings


def trading_calendar(repo: Repository, start: date, end: date, ids: list[str]) -> pd.DatetimeIndex:
    days = [d for d, _ in repo.series("taiex", start, end)]
    if not days:
        with repo.conn.cursor() as cur:
            cur.execute("SELECT DISTINCT date FROM daily_prices WHERE stock_id = ANY(%s) AND date BETWEEN %s AND %s "
                        "ORDER BY date", (ids, start, end))
            days = [r[0] for r in cur.fetchall()]
    return pd.DatetimeIndex(pd.to_datetime(days))


def _ensure_connection(repo: Repository) -> None:
    """長時間模擬後連線可能已被資料庫關閉：測試一次，失敗就重新連線。"""
    import psycopg

    from engine.db import connect

    try:
        with repo.conn.cursor() as cur:
            cur.execute("SELECT 1")
        repo.conn.rollback()
    except psycopg.Error:
        repo.conn = connect()


def fingerprint(settings: Settings, ids: list[str], repo: Repository, start: date, end: date) -> str:
    cfg = {k: getattr(settings, k) for k in OVERRIDABLE}
    with repo.conn.cursor() as cur:
        cur.execute("SELECT count(*), max(date), sum(close) FROM daily_prices WHERE stock_id = ANY(%s) AND date <= %s",
                    (ids, end))
        prices = cur.fetchone()
        cur.execute("SELECT count(*), max(published_date) FROM financials WHERE stock_id = ANY(%s)", (ids,))
        fins = cur.fetchone()
        cur.execute("SELECT series, count(*), max(date) FROM market_series WHERE date <= %s GROUP BY 1 ORDER BY 1",
                    (end,))
        series = cur.fetchall()
    payload = json.dumps({"plan": settings.plan.name, "cfg": cfg, "ids": ids, "start": start, "end": end,
                          "prices": prices, "fins": fins, "series": series}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _groups_of(groups: dict[str, tuple[str, set[str]]]) -> dict[str, list[str]]:
    custom: dict[str, list[str]] = {}
    industry: dict[str, list[str]] = {}
    for name, (kind, members) in groups.items():
        for sid in members:
            (custom if kind == "custom" else industry).setdefault(sid, []).append(name)
    return {sid: custom.get(sid) or industry.get(sid) or [] for sid in set(custom) | set(industry)}


# ---------- 執行 ----------

@dataclasses.dataclass
class Context:
    """回測需要的資料（讀一次，調參時重複使用）。"""
    start: date
    stop: date
    frames: dict
    groups: dict
    series: dict
    shares: dict
    names: dict
    calendar: pd.DatetimeIndex
    adj_0050: Any
    warnings: list[str]
    planner: Planner | None = None


def load_context(settings: Settings, repo: Repository, start: date, stop: date) -> Context:
    ids, warnings = universe_for_backtest(settings, repo)
    if not ids:
        raise BacktestError("沒有可回測的股票（免費模式請先加入自選股並回補歷史）")
    lookback = settings.scoring["lookback_years"] + (stop - start).days / 365.25 + 1
    frames = load_frames(repo, ids, stop, int(lookback + 1))
    frames = {sid: f for sid, f in frames.items() if not f.prices.empty}
    groups = load_groups(repo)
    series_start = start - timedelta(days=settings.filters["market_light"]["history_days"] + 400)
    series = {name: repo.series(name, series_start, stop)
              for name in ("taiex", "sox", "tsm_adr", "usd_twd", "foreign_tx_oi", "etf_0050")}
    series["tw2330"] = [(d, c) for _s, d, c in repo.closes(["2330"], series_start, stop)]
    with repo.conn.cursor() as cur:
        cur.execute("SELECT stock_id, shares FROM stocks WHERE stock_id = ANY(%s)", (list(frames),))
        shares = {s: float(n) if n else None for s, n in cur.fetchall()}
        cur.execute("SELECT stock_id, name FROM stocks WHERE stock_id = ANY(%s)", (list(frames),))
        names = dict(cur.fetchall())
    calendar = trading_calendar(repo, start, stop, list(frames))
    if len(calendar) < 20:
        raise BacktestError(f"{start} 至 {stop} 只有 {len(calendar)} 個交易日，資料不足")
    if not series["taiex"]:
        warnings.append("沒有加權指數歷史，大盤燈號與族群相對報酬不完整（執行 backtest-data）")
    adj_0050 = repo.adjustments(["0050"]).get("0050")
    return Context(start, stop, frames, groups, series, shares, names, calendar, adj_0050, warnings)


def evaluate(ctx: Context, settings: Settings, reuse_scores: bool = False) -> tuple[dict, dict, dict]:
    """逐日訊號計畫＋交易模擬。reuse_scores：沿用上一次的評分快取（只改風控、濾網時用）。"""
    if reuse_scores and ctx.planner is not None:
        ctx.planner.retarget(settings)
    else:
        ctx.planner = Planner(settings, ctx.frames, ctx.groups, ctx.series, ctx.shares)
    planner = ctx.planner
    plans = {}
    for i, ts in enumerate(ctx.calendar):
        plans[ts.date()] = planner.day(ts.date())
        if i % 100 == 0:
            log.info("訊號計畫 %s（%d/%d）", ts.date(), i + 1, len(ctx.calendar))
    result = simulate(plans, {sid: f.prices for sid, f in ctx.frames.items()}, ctx.calendar, settings,
                      _groups_of(ctx.groups), ctx.names)
    benchmarks = {k: ctx.series[k] for k in settings.backtest.get("benchmarks", []) if ctx.series.get(k)}
    if "etf_0050" in benchmarks:  # 0050 還原除息與分割＝含息報酬
        benchmarks["etf_0050"] = adjust_points(benchmarks["etf_0050"], ctx.adj_0050)
    metrics = build_metrics(result, benchmarks, ctx.calendar[0].date(), ctx.calendar[-1].date())
    metrics["universe"] = len(ctx.frames)
    metrics["bearish_days"] = sum(1 for p in plans.values() if p.light == "bearish")
    metrics["candidate_days"] = sum(1 for p in plans.values() if p.candidates)
    rebound_days: dict[str, int] = {}
    for p in plans.values():
        if p.regime != "normal":
            rebound_days[p.regime] = rebound_days.get(p.regime, 0) + 1
    metrics["rebound_days"] = rebound_days
    return metrics, result, plans


def run_backtest(settings: Settings, repo: Repository, period: str = "tune", end: date | None = None,
                 overrides: dict[str, str] | None = None, force: bool = False, save: bool = True) -> dict[str, Any]:
    if overrides:
        settings = apply_overrides(settings, overrides)
    if end is None:
        latest = repo.latest_series_date("taiex")
        if latest is None:
            raise BacktestError("沒有加權指數資料，請先執行 python -m engine.cli backtest-data")
        end = latest
    start, stop = periods(settings, end)[period]
    if period in ("holdout", "full") and not force:
        with repo.conn.cursor() as cur:
            cur.execute("SELECT run_id, created_at FROM backtest_runs WHERE period IN ('holdout', 'full') LIMIT 1")
            done = cur.fetchone()
        if done:
            raise BacktestError(f"保留驗證區間已在 {done[1]:%Y-%m-%d} 驗證過（run {done[0]}），只能驗證一次；"
                                "若確定要重跑請加 --force（結果將不再是未經調參的驗證）")

    ctx = load_context(settings, repo, start, stop)
    warnings = ctx.warnings
    fp = fingerprint(settings, list(ctx.frames), repo, start, stop)
    # 資料都已讀進記憶體：先結束讀取交易。模擬可能跑十幾分鐘，期間不再碰資料庫，
    # 連線可能被資料庫閒置斷線，存檔前再確認並重連
    repo.conn.rollback()
    metrics, result, _plans = evaluate(ctx, settings)
    calendar = ctx.calendar
    params = {"overrides": overrides or {}}
    report = render_markdown(metrics, period, settings.plan.label, warnings, params) + f"\n指紋：`{fp}`\n"
    run_id = None
    if save:
        _ensure_connection(repo)
        equity = [(d.isoformat(), round(v)) for d, v in result["equity"]]
        with repo.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO backtest_runs (period, start_date, end_date, plan, params, fingerprint, metrics, trades, "
                "equity) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING run_id",
                (period, calendar[0].date(), calendar[-1].date(), settings.plan.name,
                 json.dumps(params, ensure_ascii=False), fp, json.dumps(metrics, ensure_ascii=False, default=str),
                 json.dumps(result["trades"], ensure_ascii=False), json.dumps(equity)),
            )
            run_id = cur.fetchone()[0]
        repo.conn.commit()
    return {"run_id": run_id, "period": period, "metrics": metrics, "trades": result["trades"],
            "open_positions": result["open_positions"], "warnings": warnings, "fingerprint": fp, "report": report,
            "equity": result["equity"]}


# ---------- 回測資料 ----------

def refresh_history(settings: Settings, repo: Repository, exchange, finmind, years: int, end: date) -> dict[str, int]:
    """回測需要較長的大盤時間序列：加權指數（證交所，每月一次）與 FinMind 序列（各一次）。"""
    from engine.filters.runner import FINMIND_SERIES
    from engine.pipeline import refresh_adjustments
    from engine.sources import finmind as fm

    start = end.replace(year=end.year - years, day=1)
    counts: dict[str, int] = {}
    rows: list[dict] = []
    month = start
    while month <= end:
        rows += exchange.taiex_month(month)
        month = (month + timedelta(days=32)).replace(day=1)
    counts["taiex"] = repo.upsert("market_series", rows)
    for series, dataset, data_id, parse in FINMIND_SERIES:
        counts[series] = repo.upsert("market_series", parse(finmind.fetch(dataset, data_id, start, end)))
    # 0050 的除息與分割，用來計算含息報酬
    for kind, key in fm.ADJUSTMENT_DATASETS.items():
        counts[f"0050_{kind}"] = repo.upsert("price_adjustments",
                                             fm.parse_adjustments(finmind.fetch(key, "0050", start, end), kind))
    # 自選股（或全市場）缺少的除權息參考價
    ids, _w = universe_for_backtest(settings, repo)
    counts["adjustments"] = refresh_adjustments(repo, finmind, ids, end, force=True)
    repo.conn.commit()
    return counts
