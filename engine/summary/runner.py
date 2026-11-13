"""每日盤勢總結步驟：讀資料庫 → 計算事實 → 歸納原因 → 隔日計畫 → 文字敘述 → 寫入 daily_summaries。

排在持股檢查之後、LINE 推播之前（要用到當天的燈號、訊號與賣出提醒）；晚間補抓後重算一次。
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Any

from engine.config import TZ, Settings
from engine.repository import Repository
from engine.summary import facts as F
from engine.summary.narrative import write_narrative
from engine.summary.plan import build_plan
from engine.summary.reasons import build_reasons


def _jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, ensure_ascii=False, default=str))


def collect_facts(settings: Settings, repo: Repository, as_of: date) -> dict[str, Any]:
    cfg = settings.notify["daily_summary"]
    start = as_of - timedelta(days=150)
    closes = F.to_series(repo.series("taiex", start, as_of))
    ohlc = {}
    for key in ("open", "high", "low"):
        pts = [v for d, v in repo.series(f"taiex_{key}", as_of, as_of)]
        ohlc[key] = pts[0] if pts else None
    out: dict[str, Any] = {"date": as_of.isoformat()}
    index = F.index_move(closes, ohlc, as_of, cfg)
    out["index"] = index
    if index is None:
        return out
    out["headline"] = F.describe_move(index)
    out["ma"] = F.ma_context(closes, as_of, index.get("high"), index.get("low"), cfg)
    out["prior_run"] = F.prior_run(closes, as_of, cfg["run_days"])

    prev = repo.previous_trade_date(as_of)
    rows = repo.market_snapshot(as_of, prev) if prev else []
    out["breadth"] = F.breadth(rows, cfg)
    if len(rows) >= cfg["min_market_rows"]:
        out["contributors"] = F.contributors(rows, index["prev_close"], cfg)
    # 成交值：優先用證交所公布的集中市場成交金額（含 ETF）；沒有時用普通股加總並標示
    out["turnover"] = F.turnover_series(F.to_series(repo.series("twse_turnover", start, as_of)), as_of, cfg)
    if out["turnover"] is None:
        t = F.turnover(repo.turnover_totals(as_of - timedelta(days=45), as_of), as_of, cfg)
        out["turnover"] = {**t, "source": "stocks"} if t else None
    if out["turnover"] and out["turnover"]["avg_days"] < cfg["volume_min_days"]:
        out["turnover"]["ratio"] = None  # 比較基準天數不足，不判斷爆量或量縮
    # 三大法人：優先用證交所三大法人買賣金額表；沒有時以個股股數 × 收盤價估算並標示
    flow_start = as_of - timedelta(days=cfg["flow_days"] * 2)
    official: dict[date, dict[str, float]] = {}
    for key in ("foreign", "trust", "dealer"):
        for d, v in repo.series(f"inst_{key}", flow_start, as_of):
            official.setdefault(d, {})[key] = v
    official = {d: v for d, v in official.items() if len(v) == 3}
    if as_of in official:
        out["flows"] = {**F.flows(official, as_of), "source": "twse"}
    else:
        est = F.flows(repo.flow_totals(flow_start, as_of), as_of)
        out["flows"] = {**est, "source": "estimated"} if est else None
    if out["flows"]:
        out["foreign_top"] = repo.foreign_top(as_of, 5)

    market = repo.market_row(as_of) or {}
    moves = {r["stock_id"]: r["close"] / r["prev_close"] - 1 for r in rows if r["close"] and r["prev_close"]}
    out["groups"] = F.group_moves(market.get("group_strength") or [], moves, cfg)

    series = {k: F.to_series(repo.series(k, start, as_of)) for k in ("sox", "tsm_adr", "usd_twd", "foreign_tx_oi")}
    out["overnight"] = F.overnight(series, as_of)

    until = datetime.combine(as_of, time(23, 59, 59), tzinfo=TZ)
    news = repo.market_news(until - timedelta(hours=cfg["news_hours"]), until, cfg["news_max"] * 4)
    items = [{"title": n["title"], "source": n["source"], "url": n["url"], "scope": n["scope"],
              "topic": n["topic"], "published_at": n["published_at"].isoformat(),
              "is_major_negative": bool(n["is_major_negative"]), "summary": n["summary"]} for n in news]
    # 盤前、盤中與收盤報導是今天漲跌的原因；晚間消息（美股盤中、國際事件）發生在收盤後，放到隔日計畫的觀察重點
    pre, post = F.split_news(items, as_of, cfg["news_cutoff"])
    out["news"], out["news_after_close"] = pre[: cfg["news_max"]], post[: cfg["news_max"]]
    return _jsonable(out)


def plan_context(settings: Settings, repo: Repository, as_of: date) -> dict[str, Any]:
    from engine.notify.report import report_data

    d = report_data(settings, repo, as_of)
    market = d["market"] or {}
    ids = {h["stock_id"] for h in d["holdings"]} | {s["stock_id"] for s in d["buy_signals"]}
    ids |= {x["stock_id"] for x in d["setups"]} | {x["stock_id"] for x in d["blocked"]}
    return {
        "light": market.get("light"), "regime": d["regime"], "rebound": market.get("rebound"),
        "holdings": d["holdings"], "sells": d["sell_signals"], "buys": d["buy_signals"],
        "blocked": d["blocked"], "setups": d["setups"], "flags": repo.flags(as_of),
        "names": repo.stock_names(sorted(ids)),
    }


def run_summary(settings: Settings, repo: Repository, as_of: date, writer=None) -> dict[str, Any]:
    cfg = settings.notify["daily_summary"]
    facts = collect_facts(settings, repo, as_of)
    if facts.get("index") is None:
        return {"status": "no_index", "reason": "加權指數當日資料尚未更新"}
    reasons = build_reasons(facts, cfg)
    plan = _jsonable(build_plan(facts, plan_context(settings, repo, as_of), cfg))
    narrative, by, ai_error = write_narrative(writer, facts.get("headline"), reasons, facts.get("news") or [],
                                              plan["posture"], cfg)
    repo.save_summary(as_of, facts, reasons, plan, narrative, by)
    repo.conn.commit()
    return {"status": "ok", "headline": facts["headline"], "reasons": reasons, "plan": plan,
            "narrative": narrative, "generated_by": by, "ai_error": ai_error}
