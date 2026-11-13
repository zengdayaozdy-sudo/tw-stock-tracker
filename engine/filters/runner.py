"""濾網步驟與資料庫串接（第 4 節步驟 3、4、6）。每個步驟可單獨執行、重跑結果一致。"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd

from engine.config import TZ, Settings
from engine.filters.events import TradingCalendar, event_flags, index_review_events, statutory_events
from engine.filters.groups import group_strength, weak_group_flags
from engine.filters.liquidity import AVG_DAYS, liquidity_flags
from engine.filters.market_light import MarketLight, compute_light
from engine.filters.news import assign_feed_items, dedup_key, news_flags, parse_rss, yahoo_feeds
from engine.filters.rebound import Rebound, breadth_frame, compute_rebound, evidence_text
from engine.pipeline import universe
from engine.repository import Repository
from engine.scoring.loader import load_groups
from engine.signals.regime import market_regime
from engine.sources import finmind as fm

log = logging.getLogger(__name__)

# (series, FinMind 資料集鍵, data_id, 解析函式)
FINMIND_SERIES = [
    ("sox", "us_price", "^SOX", lambda rows: fm.parse_us_close(rows, "sox")),
    ("tsm_adr", "us_price", "TSM", lambda rows: fm.parse_us_close(rows, "tsm_adr")),
    ("usd_twd", "exchange_rate", "USD", fm.parse_exchange_rate),
    ("foreign_tx_oi", "futures_institutional", "TX", fm.parse_foreign_futures_oi),
    ("etf_0050", "price", "0050", lambda rows: fm.parse_close_series(rows, "etf_0050")),  # 交易日誌績效比較基準
]


def _day_end(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59), tzinfo=TZ)


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time(0), tzinfo=TZ)


# ---------- 步驟 3：流動性 ----------

def run_liquidity(settings: Settings, repo: Repository, as_of: date) -> dict[str, Any]:
    ids = universe(settings, repo)
    cfg = {**settings.risk["liquidity"], "lot_size": settings.risk["position"]["lot_size"]}
    flags = liquidity_flags(ids, repo.avg_volume(ids, as_of, AVG_DAYS), repo.stock_status(ids), cfg)
    repo.replace_flags(as_of, "liquidity", flags)
    repo.conn.commit()
    excluded = sorted({f["stock_id"] for f in flags})
    return {"universe": len(ids), "excluded": len(excluded), "flags": flags}


# ---------- 步驟 4：消息面排雷 ----------

def collect_news(settings: Settings, repo: Repository, exchange, http, ids: list[str],
                 as_of: date | None = None) -> dict[str, Any]:
    wanted = set(ids)
    items: list[dict] = []
    errors: list[str] = []
    source = "mops"
    try:
        # 公開資訊觀測站：今天與前一天（15:30 之後到 17:30 前的訊息只會出現在當天清單，隔天才抓得到）
        day = as_of or date.today()
        for d in (day - timedelta(days=1), day):
            items += [a for a in exchange.mops_announcements(d) if a["stock_id"] in wanted]
        limit = settings.sources.get("mops", {}).get("max_details_per_run", 80)
        known = repo.existing_news_keys([dedup_key(it) for it in items])
        fetched = 0
        for it in items:
            if fetched >= limit or not it.get("detail_params") or dedup_key(it) in known:
                continue
            try:
                it["body"] = exchange.mops_detail(it["detail_params"])
            except Exception as e:
                errors.append(f"重大訊息明細抓取失敗 {it['stock_id']}：{e}")
            fetched += 1
    except Exception as e:
        errors.append(f"公開資訊觀測站重大訊息抓取失敗，改用 OpenAPI：{e}")
        source = "openapi"
        items = []
        try:
            items += [a for a in exchange.announcements() if a["stock_id"] in wanted]
        except Exception as e2:
            errors.append(f"重大訊息抓取失敗：{e2}")
    for template in settings.filters["news"].get("rss_feeds") or []:
        for sid in ids:
            url = template.format(stock_id=sid)
            try:
                items += parse_rss(http.get_text(url), sid, "rss")
            except Exception as e:
                errors.append(f"RSS 抓取失敗 {url}：{e}")
    yahoo_cfg = settings.filters["news"].get("yahoo_rss") or {}
    yahoo_count = 0
    if yahoo_cfg.get("enabled") and http is not None:
        yahoo_items = _collect_yahoo(yahoo_cfg, repo, http, ids, as_of or date.today(),
                                     settings.filters["news"]["lookback_days"], errors)
        yahoo_count = len(yahoo_items)
        items += yahoo_items
    for it in items:
        it["dedup_key"] = dedup_key(it)
    repo.insert_news(items)
    repo.conn.commit()
    return {"collected": len(items), "yahoo_rss": yahoo_count, "announcement_source": source, "errors": errors}


def _news_groups(repo: Repository, kinds: list[str] | None = None) -> dict[str, set[str]]:
    """族群名稱 → 成分股（消息面用：族群關鍵字新聞、族群利空警示）。"""
    from engine.scoring.loader import load_groups

    return {name: members for name, (kind, members) in load_groups(repo).items()
            if kinds is None or kind in kinds}


def _collect_yahoo(cfg: dict, repo: Repository, http, ids: list[str], as_of: date, lookback_days: int,
                   errors: list[str]) -> list[dict]:
    """Yahoo股市 RSS：個股新聞＋族群關鍵字＋盤勢分類，只留 lookback_days 內的新聞。"""
    groups = _news_groups(repo, cfg.get("group_kinds"))
    names = repo.stock_names(ids)
    cutoff = _day_start(as_of - timedelta(days=lookback_days))
    per_feed = int(cfg.get("max_items_per_feed", 30))
    out: list[dict] = []
    failed = 0
    for feed in yahoo_feeds(cfg, ids, groups):
        try:
            raw = parse_rss(http.get_text(feed["url"]), feed["stock_id"], cfg.get("attribution", "Yahoo股市"))
        except Exception as e:
            failed += 1
            if failed <= 5:
                errors.append(f"Yahoo股市 RSS 抓取失敗（{feed['topic'] or feed['stock_id']}）：{e}")
            continue
        recent = [it for it in raw if it["published_at"] >= cutoff][:per_feed]
        out += assign_feed_items(feed, recent, names)
    if failed > 5:
        errors.append(f"Yahoo股市 RSS 共 {failed} 個來源抓取失敗")
    return out


def judge_news(settings: Settings, repo: Repository, judge, ids: list[str], as_of: date) -> dict[str, Any]:
    cfg = settings.filters["news"]
    since = _day_start(as_of - timedelta(days=cfg["lookback_days"]))
    pending = repo.unjudged_news(ids, since, cfg["max_judgments_per_run"])
    by: dict[str, int] = {}
    for i, item in enumerate(pending, start=1):
        j = judge.judge(item)
        repo.save_judgment(item["news_id"], j.to_dict())
        by[j.judged_by] = by.get(j.judged_by, 0) + 1
        if i % 20 == 0:
            repo.conn.commit()
    repo.conn.commit()
    return {"judged": len(pending), "judged_by": by, "ai_errors": judge.errors[:5]}


def run_news(settings: Settings, repo: Repository, exchange, http, judge, as_of: date,
             collect: bool = True) -> dict[str, Any]:
    ids = universe(settings, repo)
    cfg = settings.filters["news"]
    out: dict[str, Any] = {"universe": len(ids)}
    if collect:
        out.update(collect_news(settings, repo, exchange, http, ids, as_of))
    out.update(judge_news(settings, repo, judge, ids, as_of))
    since = _day_start(as_of - timedelta(days=cfg["lookback_days"]))
    groups = _news_groups(repo)
    flags = news_flags(repo.negative_news(ids, since, _day_end(as_of)), cfg, groups, ids)
    repo.replace_flags(as_of, "news", flags)
    repo.conn.commit()
    out["excluded"] = len({f["stock_id"] for f in flags if f["kind"] == "exclude"})
    out["warned"] = len({f["stock_id"] for f in flags if f["kind"] == "warn"})
    out["flags"] = flags
    return out


# ---------- 步驟 6：大盤燈號 ----------

def refresh_market_series(settings: Settings, repo: Repository, exchange, finmind, as_of: date) -> dict[str, Any]:
    history = settings.filters["market_light"]["history_days"]
    counts: dict[str, int] = {}
    errors: list[str] = []

    def start_for(series: str) -> date:
        latest = repo.latest_series_date(series)
        floor = as_of - timedelta(days=history)
        if latest is None or latest < floor:
            return floor
        return min(latest - timedelta(days=7), as_of - timedelta(days=7))

    # 加權指數：證交所，一次一個月
    try:
        start = start_for("taiex")
        month = start.replace(day=1)
        rows: list[dict] = []
        while month <= as_of:
            rows += [r for r in exchange.taiex_month(month) if r["date"] <= as_of]
            month = (month + timedelta(days=32)).replace(day=1)
        counts["taiex"] = repo.upsert("market_series", rows)
    except Exception as e:
        errors.append(f"加權指數抓取失敗：{e}")

    # 盤勢總結用：集中市場成交金額（一次一個月）與三大法人買賣金額（逐日，只補缺的交易日）
    try:
        start = start_for("twse_turnover")
        month = start.replace(day=1)
        rows = []
        while month <= as_of:
            rows += [r for r in exchange.market_turnover_month(month) if r["date"] <= as_of]
            month = (month + timedelta(days=32)).replace(day=1)
        counts["twse_turnover"] = repo.upsert("market_series", rows)
    except Exception as e:
        errors.append(f"集中市場成交金額抓取失敗：{e}")
    try:
        days = settings.notify.get("daily_summary", {}).get("flow_backfill_days", 45)
        have = {d for d, _v in repo.series("inst_foreign", as_of - timedelta(days=days), as_of)}
        trade_days = [d for d, _v in repo.series("taiex", as_of - timedelta(days=days), as_of) if d not in have]
        rows = []
        for d in trade_days:
            rows += exchange.institutional_amount(d)
        counts["inst_amount"] = repo.upsert("market_series", rows) if rows else 0
    except Exception as e:
        errors.append(f"三大法人買賣金額抓取失敗：{e}")

    for series, dataset, data_id, parse in FINMIND_SERIES:
        try:
            rows = parse(finmind.fetch(dataset, data_id, start_for(series), as_of))
            counts[series] = repo.upsert("market_series", [r for r in rows if r["date"] <= as_of])
        except Exception as e:
            errors.append(f"{series} 抓取失敗：{e}")
    repo.conn.commit()
    return {"counts": counts, "errors": errors}


def compute_market_light(settings: Settings, repo: Repository, as_of: date) -> MarketLight:
    cfg = settings.filters["market_light"]
    start = as_of - timedelta(days=cfg["history_days"])
    series: dict[str, Any] = {
        name: repo.series(name, start, as_of) for name in ("taiex", "sox", "tsm_adr", "usd_twd", "foreign_tx_oi")
    }
    series["tw2330"] = [(d, c) for _s, d, c in repo.closes(["2330"], start, as_of)]
    return compute_light(series, as_of, cfg)


# ---------- 步驟 6：大盤反彈偵測 ----------

def compute_market_rebound(settings: Settings, repo: Repository, ids: list[str], as_of: date) -> Rebound | None:
    cfg = settings.filters.get("rebound") or {}
    if not cfg.get("enabled"):
        return None
    days = max(cfg["correction"]["lookback_days"] + cfg["rally"]["max_days"], 80)
    start = as_of - timedelta(days=int(days * 1.6) + 30)
    panel = repo.price_panel(ids, start, as_of)
    closes = pd.DataFrame(columns=ids, dtype=float)
    volume = pd.Series(dtype=float)
    if panel:
        df = pd.DataFrame(panel, columns=["stock_id", "date", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        closes = df.pivot(index="date", columns="stock_id", values="close").sort_index()
        volume = df.groupby("date")["volume"].sum().sort_index()
    breadth = breadth_frame(closes, cfg["breadth_recovery"]["ma_days"])
    taiex = pd.Series({pd.Timestamp(d): v for d, v in repo.series("taiex", start, as_of)}, dtype=float).sort_index()
    oi = pd.Series({pd.Timestamp(d): v for d, v in repo.series("foreign_tx_oi", start, as_of)}, dtype=float)
    return compute_rebound(taiex, breadth, volume, oi.sort_index(), as_of, cfg)


# ---------- 步驟 6：族群強弱 ----------

def compute_group_strength(settings: Settings, repo: Repository, as_of: date) -> tuple[list[dict], set[str]]:
    cfg = settings.filters["group_strength"]
    scope = set(repo.all_stock_ids() if settings.enabled("market_wide_groups") else universe(settings, repo))
    days = max(cfg["lookback_days"])
    start = as_of - timedelta(days=int(days * 1.6) + 20)
    raw = repo.closes(sorted(scope), start, as_of)
    closes: dict[str, pd.Series] = {}
    if raw:
        df = pd.DataFrame(raw, columns=["stock_id", "date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        closes = {sid: g.set_index("date")["close"].sort_index() for sid, g in df.groupby("stock_id")}
    taiex_pts = repo.series("taiex", start, as_of)
    taiex = pd.Series({pd.Timestamp(d): v for d, v in taiex_pts}, dtype=float).sort_index()
    scores = repo.score_totals(as_of, cfg["score_horizon"])
    groups = load_groups(repo)
    return group_strength(groups, scope, closes, taiex, scores, as_of, cfg), scope


# ---------- 事件行事曆 ----------

def refresh_events(settings: Settings, repo: Repository, exchange, as_of: date) -> dict[str, Any]:
    keep = as_of - timedelta(days=settings.filters["events"]["keep_past_days"])
    known = repo.known_stock_ids()
    rows: list[dict] = []
    errors: list[str] = []
    for label, fn in (("除權息", exchange.ex_dividend_events), ("股東會", exchange.shareholder_meetings)):
        try:
            rows += [{**r, "source": "auto"} for r in fn() if r["stock_id"] in known and r["date"] >= keep]
        except Exception as e:
            errors.append(f"{label}抓取失敗：{e}")
    # 同一天同類事件（例如同時除權與除息）合併成一筆
    merged: dict[tuple, dict] = {}
    for r in rows:
        merged.setdefault((r["stock_id"], r["date"], r["event_type"]), r)
    n = repo.upsert("events", merged.values())
    repo.conn.commit()
    return {"events": n, "errors": errors}


def trading_calendar(exchange) -> tuple[TradingCalendar, str | None]:
    try:
        return TradingCalendar(exchange.holidays()), None
    except Exception as e:
        return TradingCalendar(), f"休市日抓取失敗，只排除週末：{e}"


def compute_event_flags(settings: Settings, repo: Repository, ids: list[str], as_of: date,
                        cal: TradingCalendar) -> list[dict]:
    cfg = settings.filters["events"]
    stored = repo.events_between(ids, as_of - timedelta(days=15), as_of + timedelta(days=20))
    implied = statutory_events(ids, repo.financial_periods(ids, as_of), as_of)
    reviews = index_review_events(ids, repo.market_caps(ids), cfg, cal)
    return event_flags(stored + implied + reviews, as_of, cal, cfg)


# ---------- 步驟 6 整合 ----------

def run_filters(settings: Settings, repo: Repository, exchange, finmind, as_of: date,
                refresh: bool = True) -> dict[str, Any]:
    """大盤燈號、族群強弱、事件行事曆，寫入 market 與 screen_flags。"""
    out: dict[str, Any] = {"warnings": []}
    ids = universe(settings, repo)

    if refresh:
        series = refresh_market_series(settings, repo, exchange, finmind, as_of)
        out["series"] = series["counts"]
        out["warnings"] += series["errors"]
        ev = refresh_events(settings, repo, exchange, as_of)
        out["events_saved"] = ev["events"]
        out["warnings"] += ev["errors"]
        cal, err = trading_calendar(exchange)
        if err:
            out["warnings"].append(err)
    else:
        cal = TradingCalendar()

    light = compute_market_light(settings, repo, as_of)
    rebound = compute_market_rebound(settings, repo, ids, as_of)
    strength, scope = compute_group_strength(settings, repo, as_of)
    repo.upsert("market", [{
        "date": as_of, "light": light.light, "inputs": light.to_inputs_json(), "group_strength": strength,
        "rebound": rebound.to_json() if rebound else None,
    }])

    regime = market_regime(light.light, rebound.state if rebound else None, settings.risk)
    market_rows = []
    if regime["mode"] == "blocked":
        reason = f"大盤燈號偏空，{rebound.label}中待確認（列入反彈觀察）" if regime.get("watch") else "大盤燈號偏空"
        market_rows = [{"stock_id": sid, "kind": "block", "reason": reason,
                        "detail": {"total": light.total}} for sid in ids]
    elif regime["mode"] != "normal":
        reason = f"大盤偏空，{rebound.label}：以{regime['label']}處理（部位 ×{regime['size_factor']:g}、停損收緊）"
        market_rows = [{"stock_id": sid, "kind": "warn", "reason": reason,
                        "detail": {"total": light.total, "rebound": rebound.state}} for sid in ids]
    repo.replace_flags(as_of, "market", market_rows)

    group_rows = [f for f in weak_group_flags(strength, scope) if f["stock_id"] in set(ids)]
    repo.replace_flags(as_of, "group", group_rows)

    event_rows = compute_event_flags(settings, repo, ids, as_of, cal)
    repo.replace_flags(as_of, "event", event_rows)

    excluded = {sid for sid, fl in repo.flags(as_of).items() if any(f["kind"] == "exclude" for f in fl)}
    out["scores_marked_excluded"] = repo.mark_excluded_scores(as_of, excluded)
    repo.conn.commit()

    out.update({
        "light": light.light,
        "light_label": light.label,
        "light_total": light.total,
        "light_inputs": light.inputs,
        "rebound": rebound.state if rebound else None,
        "rebound_points": rebound.points if rebound else None,
        "rebound_evidence": evidence_text(rebound.to_json()) if rebound else [],
        "regime": regime["mode"],
        "groups_ranked": sum(1 for g in strength if g["rank"] is not None),
        "weak_groups": [g["name"] for g in strength if g["weak"]],
        "blocked_by_group": len(group_rows),
        "event_warnings": len(event_rows),
        "excluded_total": len(excluded),
    })
    return out


def screen_summary(repo: Repository, as_of: date) -> dict[str, dict[str, list[str]]]:
    """每檔股票當日的排除、擋買進、提示理由（第 5 階段訊號使用）。"""
    out: dict[str, dict[str, list[str]]] = {}
    for sid, flags in repo.flags(as_of).items():
        entry = out.setdefault(sid, {"exclude": [], "block": [], "warn": []})
        for f in flags:
            entry[f["kind"]].append(f["reason"])
    return out
