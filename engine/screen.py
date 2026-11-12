"""全市場潛力股篩選（screen 指令）：替自選股補新股票。

第一關：全市場免費資料（證交所／櫃買月營收彙總、估值、法人、成交金額）篩掉太小、太冷門、虧損或營收衰退的公司，
        依營收成長、法人買超、本益比排名，取前 N 檔。
第二關：候選股用 FinMind 抓一年股價，算中期相對強弱與年線、季線位置。
最後：兩關分數各半，依產業分散（同產業上限、每個產業加上現有自選股至少 3 檔）選出目標檔數。

這是依固定規則的量化初選，不是買進建議；入選後仍由每日評分、濾網與訊號決定是否進場。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from engine.config import CONFIG_DIR, _load_yaml


def load_screen_config() -> dict[str, Any]:
    return _load_yaml("screen.yaml", CONFIG_DIR)


@dataclass
class Candidate:
    stock_id: str
    name: str
    industry: str
    metrics: dict[str, float | None] = field(default_factory=dict)
    stage1: float | None = None
    stage2: float | None = None
    final: float | None = None


def pct_rank(values: dict[str, float | None], higher_better: bool = True) -> dict[str, float]:
    """0–100 百分位；缺值給中性 50（與評分模型的缺值規則一致）。"""
    s = pd.Series({k: v for k, v in values.items() if v is not None}, dtype=float)
    ranks = (s.rank(pct=True, ascending=higher_better) * 100).to_dict() if not s.empty else {}
    return {k: float(ranks.get(k, 50.0)) for k in values}


def passes_stage1(m: dict[str, Any], cfg: dict[str, Any]) -> bool:
    def ge(key: str, limit: float) -> bool:
        v = m.get(key)
        return v is not None and v >= limit

    pe = m.get("pe")
    return (ge("market_cap", cfg["min_market_cap"]) and ge("avg_turnover", cfg["min_avg_turnover"])
            and ge("avg_volume_lots", cfg["min_avg_volume_lots"]) and ge("close", cfg["min_price"])
            and ge("cum_revenue_yoy", cfg["min_cum_revenue_yoy"])
            and ge("month_revenue_yoy", cfg["min_month_revenue_yoy"])
            and pe is not None and 0 < pe <= cfg["max_pe"])


def weighted(ranks: dict[str, dict[str, float]], weights: dict[str, float], ids: list[str]) -> dict[str, float]:
    total = sum(weights.values())
    return {sid: sum(ranks[k][sid] * w for k, w in weights.items()) / total for sid in ids}


def stage1_scores(cands: dict[str, Candidate], cfg: dict[str, Any]) -> dict[str, float]:
    ids = list(cands)
    m = {sid: c.metrics for sid, c in cands.items()}
    ranks = {
        "cum_revenue_yoy": pct_rank({s: m[s].get("cum_revenue_yoy") for s in ids}),
        "month_revenue_yoy": pct_rank({s: m[s].get("month_revenue_yoy") for s in ids}),
        "institutional_ratio": pct_rank({s: m[s].get("institutional_ratio") for s in ids}),
        "pe_inverse": pct_rank({s: m[s].get("pe") for s in ids}, higher_better=False),
    }
    return weighted(ranks, cfg["weights"], ids)


def technical_metrics(prices: pd.DataFrame, taiex: pd.Series, cfg: dict[str, Any]) -> dict[str, float] | None:
    """prices：index 為日期、含 close。資料不足一年（年線）時回傳 None。"""
    close = prices["close"].astype(float).dropna().sort_index()
    if len(close) < 240:
        return None
    last = float(close.iloc[-1])
    n, skip = cfg["rs_days"], cfg["rs_skip_days"]
    if len(close) <= n + 1:
        return None
    stock_ret = float(close.iloc[-1 - skip] / close.iloc[-1 - n] - 1)
    t = taiex.sort_index()
    t = t[t.index <= close.index[-1]]
    if len(t) > n + 1:
        start_d, end_d = close.index[-1 - n], close.index[-1 - skip]
        t_start = t[t.index <= start_d]
        t_end = t[t.index <= end_d]
        bench = float(t_end.iloc[-1] / t_start.iloc[-1] - 1) if not t_start.empty and not t_end.empty else 0.0
    else:
        bench = 0.0
    return {
        "rs": stock_ret - bench,
        "vs_ma240": last / float(close.tail(240).mean()) - 1,
        "vs_ma60": last / float(close.tail(60).mean()) - 1,
    }


def stage2_scores(cands: dict[str, Candidate], cfg: dict[str, Any]) -> dict[str, float]:
    ids = [s for s, c in cands.items() if c.metrics.get("rs") is not None]
    ranks = {k: pct_rank({s: cands[s].metrics.get(k) for s in ids}) for k in cfg["weights"]}
    return weighted(ranks, cfg["weights"], ids)


def diversify(ranked: list[Candidate], existing: dict[str, int], target: int, max_per: int,
              min_per: int) -> list[Candidate]:
    """依分數由高到低挑，同產業不超過 max_per；加上現有自選股後產業不足 min_per 檔的，
    能從候選補足就補（強制入選），補不足就整個產業不選。每輪處理一個產業，直到都符合。"""
    forced: set[str] = set()
    excluded: set[str] = set()
    order = {c.stock_id: i for i, c in enumerate(ranked)}
    picks: list[Candidate] = []
    for _ in range(len(ranked) + 1):
        picks = sorted((c for c in ranked if c.stock_id in forced), key=lambda c: order[c.stock_id])
        counts: dict[str, int] = {}
        for c in picks:
            counts[c.industry] = counts.get(c.industry, 0) + 1
        for c in ranked:
            if len(picks) >= target:
                break
            if c.stock_id in forced or c.stock_id in excluded or counts.get(c.industry, 0) >= max_per:
                continue
            picks.append(c)
            counts[c.industry] = counts.get(c.industry, 0) + 1
        picked = {c.stock_id for c in picks}
        bad = [ind for ind, n in counts.items() if existing.get(ind, 0) + n < min_per
               and not all(c.stock_id in forced for c in picks if c.industry == ind)]
        if not bad:
            return sorted(picks, key=lambda c: order[c.stock_id])
        ind = min(bad, key=lambda i: min(order[c.stock_id] for c in picks if c.industry == i))
        need = min_per - existing.get(ind, 0) - counts[ind]
        avail = [c for c in ranked if c.industry == ind and c.stock_id not in picked and c.stock_id not in excluded]
        grown = forced | {c.stock_id for c in picks if c.industry == ind} | {c.stock_id for c in avail[:need]}
        if len(avail) >= need and counts[ind] + need <= max_per and len(grown) <= target:
            forced = grown
        else:
            excluded |= {c.stock_id for c in ranked if c.industry == ind}
    return sorted(picks, key=lambda c: order[c.stock_id])


# ---------- 資料庫與資料來源 ----------

def load_market_snapshot(repo) -> tuple[date, dict[str, Candidate], set[str], dict[str, int]]:
    """全市場近 5 個交易日的成交、法人、估值與基本資料；回傳（最新日、候選、目前自選股、現有自選股產業檔數）。"""
    with repo.conn.cursor() as cur:
        cur.execute("SELECT DISTINCT date FROM daily_prices ORDER BY date DESC LIMIT 5")
        days = [r[0] for r in cur.fetchall()]
        if not days:
            raise RuntimeError("資料庫沒有行情資料，請先執行 fetch")
        latest = days[0]
        cur.execute("SELECT stock_id FROM watchlist UNION SELECT stock_id FROM holdings")
        current = {r[0] for r in cur.fetchall()}
        cur.execute(
            "SELECT s.industry, count(*) FROM watchlist w JOIN stocks s USING (stock_id) GROUP BY 1")
        existing = {ind or "未分類": n for ind, n in cur.fetchall()}
        cur.execute(
            """
            SELECT s.stock_id, s.name, coalesce(s.industry, '未分類'), s.market_cap, s.status,
                   avg(p.turnover), avg(p.volume),
                   (SELECT close FROM daily_prices x WHERE x.stock_id = s.stock_id AND x.date = %s),
                   (SELECT sum(coalesce(foreign_net, 0) + coalesce(trust_net, 0)) FROM chips c
                     WHERE c.stock_id = s.stock_id AND c.date = ANY(%s)),
                   sum(p.volume),
                   (SELECT pe FROM valuations v WHERE v.stock_id = s.stock_id ORDER BY date DESC LIMIT 1)
            FROM stocks s JOIN daily_prices p ON p.stock_id = s.stock_id AND p.date = ANY(%s)
            WHERE s.delisted_date IS NULL
            GROUP BY s.stock_id
            """,
            (latest, days, days),
        )
        rows = cur.fetchall()
    out: dict[str, Candidate] = {}
    for sid, name, ind, cap, status, turnover, volume, close, inst, vol_sum, pe in rows:
        if sid in current or status:
            continue  # 已在自選股或持股；處置、注意、全額交割不選
        out[sid] = Candidate(sid, name, ind, {
            "market_cap": float(cap) if cap else None,
            "avg_turnover": float(turnover) if turnover else None,
            "avg_volume_lots": float(volume) / 1000 if volume else None,
            "close": float(close) if close else None,
            "institutional_ratio": float(inst) / float(vol_sum) if inst is not None and vol_sum else None,
            "pe": float(pe) if pe else None,
        })
    return latest, out, current, existing


def run_screen(repo, exchange, finmind, plan_limit: int | None, log=print) -> dict[str, Any]:
    cfg = load_screen_config()
    latest, cands, current, existing = load_market_snapshot(repo)
    revenue = {r["stock_id"]: r for r in exchange.month_revenue()}
    period = max((r["period"] for r in revenue.values()), default="")
    for sid, c in cands.items():
        r = revenue.get(sid) or {}
        c.metrics["cum_revenue_yoy"] = r.get("cum_yoy")
        c.metrics["month_revenue_yoy"] = r.get("month_yoy")
    universe = len(cands)
    passed = {sid: c for sid, c in cands.items() if passes_stage1(c.metrics, cfg["stage1"])}
    s1 = stage1_scores(passed, cfg["stage1"]) if passed else {}
    for sid, v in s1.items():
        passed[sid].stage1 = v
    top = dict(sorted(passed.items(), key=lambda kv: -kv[1].stage1)[: cfg["stage1"]["candidates"]])
    log(f"全市場 {universe} 檔 → 第一關通過 {len(passed)} 檔 → 取前 {len(top)} 檔抓股價")

    from engine.sources.finmind import parse_prices

    start = latest - timedelta(days=int(cfg["stage2"]["history_days"] * 1.5))
    taiex = pd.Series({pd.Timestamp(d): v for d, v in repo.series("taiex", start, latest)}, dtype=float)
    failed: list[str] = []
    for i, (sid, c) in enumerate(top.items(), start=1):
        try:
            rows = parse_prices(finmind.fetch("price", sid, start, latest))
        except Exception as e:  # 單檔失敗不中斷整批
            failed.append(f"{sid}：{e}")
            continue
        if rows:
            df = pd.DataFrame(rows).set_index(pd.to_datetime([r["date"] for r in rows]))
            tm = technical_metrics(df, taiex, cfg["stage2"])
            if tm:
                c.metrics.update(tm)
        if i % 25 == 0:
            log(f"  股價 {i}/{len(top)}")
    s2 = stage2_scores(top, cfg["stage2"])
    fw = cfg["final_weights"]
    ranked: list[Candidate] = []
    for sid, c in top.items():
        if sid not in s2:
            continue  # 上市未滿一年或抓不到股價
        c.stage2 = s2[sid]
        c.final = (c.stage1 * fw["stage1"] + c.stage2 * fw["stage2"]) / (fw["stage1"] + fw["stage2"])
        ranked.append(c)
    ranked.sort(key=lambda c: (-c.final, c.stock_id))
    room = cfg["target"] if plan_limit is None else max(0, min(cfg["target"], plan_limit - len(current)))
    picks = diversify(ranked, existing, room, cfg["diversify"]["max_per_industry"],
                      cfg["diversify"]["min_per_industry"])
    return {"as_of": latest, "revenue_period": period, "universe": universe, "passed": len(passed),
            "stage2": len(ranked), "picks": picks, "ranked": ranked, "failed": failed, "existing": existing,
            "config": cfg}


def render_report(result: dict[str, Any]) -> str:
    cfg = result["config"]
    s1 = cfg["stage1"]
    p = result["revenue_period"]
    period = f"{int(p[:-2]) + 1911}/{p[-2:]}" if len(p) >= 4 else p

    def pct(v: float | None, digits: int = 1) -> str:
        return "—" if v is None else f"{v * 100:+.{digits}f}%"

    def num(v: float | None, digits: int = 1) -> str:
        return "—" if v is None else f"{v:.{digits}f}"

    lines = [
        f"# 全市場潛力股篩選 {result['as_of']}",
        "",
        f"資料：證交所／櫃買 {period} 月營收彙總、近 5 個交易日成交與法人、最新本益比；候選股 FinMind 近一年股價。",
        f"全市場 {result['universe']} 檔（已排除現有自選股、持股、處置／注意／全額交割）→ 第一關通過 "
        f"{result['passed']} 檔 → 第二關計算 {result['stage2']} 檔 → 入選 {len(result['picks'])} 檔。",
        "",
        "第一關門檻：市值 ≥ {:.0f} 億、5 日均成交金額 ≥ {:.0f} 億、5 日均量 ≥ {:,} 張、股價 ≥ {}、累計營收年增 ≥ {}%、"
        "單月營收年增 ≥ {}%、本益比 0–{}。".format(
            s1["min_market_cap"] / 1e8, s1["min_avg_turnover"] / 1e8, s1["min_avg_volume_lots"], s1["min_price"],
            s1["min_cum_revenue_yoy"], s1["min_month_revenue_yoy"], s1["max_pe"]),
        "排名：第一關（累計營收年增 35%、單月年增 20%、法人買超比 25%、本益比低 20%）與第二關（相對強弱 50%、"
        "相對年線 25%、相對季線 25%）各半；同產業最多 {} 檔，加上現有自選股每個產業至少 {} 檔。".format(
            cfg["diversify"]["max_per_industry"], cfg["diversify"]["min_per_industry"]),
        "",
        "這是固定規則的量化初選，不是買進建議；入選後仍由每日評分、濾網與訊號判斷。股價未還原除權息，相對強弱為近似值。",
        "",
        "| # | 代號 | 名稱 | 產業 | 總分 | 第一關 | 第二關 | 累計營收年增 | 單月年增 | 本益比 | 法人買超比 "
        "| 相對強弱 | 相對年線 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, c in enumerate(result["picks"], start=1):
        m = c.metrics
        lines.append(
            f"| {i} | {c.stock_id} | {c.name} | {c.industry} | {c.final:.1f} | {c.stage1:.1f} | {c.stage2:.1f} | "
            f"{num(m.get('cum_revenue_yoy'))}% | {num(m.get('month_revenue_yoy'))}% | {num(m.get('pe'))} | "
            f"{pct(m.get('institutional_ratio'))} | {pct(m.get('rs'))} | {pct(m.get('vs_ma240'))} |")
    counts: dict[str, int] = {}
    for c in result["picks"]:
        counts[c.industry] = counts.get(c.industry, 0) + 1
    lines += ["", "## 產業分布（新增＋現有）", "", "| 產業 | 新增 | 現有 |", "|---|---:|---:|"]
    existing = result["existing"]
    for ind in sorted(set(counts) | set(existing), key=lambda k: -(counts.get(k, 0) + existing.get(k, 0))):
        lines.append(f"| {ind} | {counts.get(ind, 0)} | {result['existing'].get(ind, 0)} |")
    if result["failed"]:
        lines += ["", "股價抓取失敗：" + "；".join(result["failed"][:10])]
    lines += ["", "入選代號：" + " ".join(c.stock_id for c in result["picks"]), ""]
    return "\n".join(lines)
