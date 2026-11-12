"""每日盤勢總結的事實計算（純函式，不碰資料庫，方便用假資料測試）。

所有函式只使用 as_of 當天（含）以前的資料；美股以台股收盤前最後一個美國交易日為準。
法人買賣超金額與權值股貢獻點數為估算值（股數 × 收盤價），會與交易所公布的金額有些微差距。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd


def _upto(s: pd.Series, as_of: date, strict: bool = False) -> pd.Series:
    if s is None or s.empty:
        return pd.Series(dtype=float)
    s = s.sort_index().dropna()
    ts = pd.Timestamp(as_of)
    return s[s.index < ts] if strict else s[s.index <= ts]


def to_series(points: list[tuple[date, float]] | None) -> pd.Series:
    if not points:
        return pd.Series(dtype=float)
    return pd.Series({pd.Timestamp(d): float(v) for d, v in points}, dtype=float).sort_index()


# ---------- 指數盤型 ----------

def index_move(closes: pd.Series, ohlc: dict[str, float | None], as_of: date, cfg: dict) -> dict[str, Any] | None:
    """加權指數當日走勢與盤型。ohlc：當日 open／high／low（可缺）；closes：收盤序列（含當日）。"""
    s = _upto(closes, as_of)
    if len(s) < 2 or s.index[-1] != pd.Timestamp(as_of):
        return None
    close, prev = float(s.iloc[-1]), float(s.iloc[-2])
    change = close - prev
    out: dict[str, Any] = {
        "close": round(close, 2), "prev_close": round(prev, 2), "change": round(change, 2),
        "pct": round(change / prev, 4), "prev_date": s.index[-2].date().isoformat(),
    }
    o, h, lo = ohlc.get("open"), ohlc.get("high"), ohlc.get("low")
    if not (o and h and lo):
        out["pattern"] = "up" if change > 0 else "down" if change < 0 else "flat"
        out["pattern_label"] = "上漲" if change > 0 else "下跌" if change < 0 else "平盤"
        return out
    flat = cfg["flat_gap"]
    gap = o / prev - 1
    open_dir = "high" if gap > flat else "low" if gap < -flat else "flat"
    trend = "up" if close > o else "down" if close < o else "flat"
    rng = h - lo
    loc = (close - lo) / rng if rng > 0 else 0.5
    out.update({
        "open": round(o, 2), "high": round(h, 2), "low": round(lo, 2), "gap_pct": round(gap, 4),
        "range": round(rng, 2), "range_pct": round(rng / prev, 4), "close_location": round(loc, 3),
        "high_vs_prev": round(h - prev, 2), "low_vs_prev": round(lo - prev, 2),
    })
    open_text = {"high": "開高", "low": "開低", "flat": "平開"}[open_dir]
    trend_text = {"up": "走高", "down": "走低", "flat": "震盪"}[trend]
    out["pattern"] = f"{open_dir}_{trend}"
    out["pattern_label"] = f"{open_text}{trend_text}"
    # 盤中翻紅／翻黑：開盤與收盤在昨收同一側，盤中卻穿越昨收
    if close < prev and h > prev and o <= prev:
        out["intraday_flip"] = "翻紅後收黑"
    elif close > prev and lo < prev and o >= prev:
        out["intraday_flip"] = "翻黑後收紅"
    elif o > prev and close < prev:
        out["intraday_flip"] = "翻黑"
    elif o < prev and close > prev:
        out["intraday_flip"] = "翻紅"
    if loc <= cfg["close_low_zone"]:
        out["close_zone"] = "low"
    elif loc >= 1 - cfg["close_high_zone"]:
        out["close_zone"] = "high"
    return out


def describe_move(m: dict[str, Any]) -> str:
    """一句話描述當日走勢，例如「開低後盤中一度翻紅漲 268 點，之後一路走低，終場跌 1,165 點（−2.63%），收在最低點附近」。"""
    parts: list[str] = []
    change, pct = m["change"], m["pct"]
    verb = "漲" if change > 0 else "跌" if change < 0 else "平"
    end = f"終場{verb} {abs(change):,.0f} 點（{pct:+.2%}），收 {m['close']:,.0f} 點"
    if "open" not in m:
        return f"加權指數{end}"
    open_text = {"high": "開高", "low": "開低", "flat": "平開"}[m["pattern"].split("_")[0]]
    parts.append(f"加權指數{open_text}（{m['gap_pct']:+.2%}）")
    flip = m.get("intraday_flip")
    if flip == "翻紅後收黑":
        parts.append(f"盤中一度翻紅、最多漲 {m['high_vs_prev']:,.0f} 點，之後一路走低")
    elif flip == "翻黑後收紅":
        parts.append(f"盤中一度翻黑、最多跌 {abs(m['low_vs_prev']):,.0f} 點，之後一路走高")
    elif m["pattern"].endswith("_down"):
        top = f"早盤最多漲 {m['high_vs_prev']:,.0f} 點，" if m["high_vs_prev"] > 0 else ""
        parts.append(f"{top}之後走低" + ("、終場翻黑" if flip == "翻黑" else ""))
    elif m["pattern"].endswith("_up"):
        bottom = f"早盤最多跌 {abs(m['low_vs_prev']):,.0f} 點，" if m["low_vs_prev"] < 0 else ""
        parts.append(f"{bottom}之後走高" + ("、終場翻紅" if flip == "翻紅" else ""))
    parts.append(f"高低差 {m['range']:,.0f} 點")
    zone = {"low": "，收在當日最低點附近", "high": "，收在當日最高點附近"}.get(m.get("close_zone"), "")
    return "，".join(parts) + "，" + end + zone


# ---------- 均線 ----------

def ma_context(closes: pd.Series, as_of: date, high: float | None, low: float | None, cfg: dict) -> list[dict]:
    """各關鍵均線（含當日）的位置、今天是否跌破／站回、盤中是否碰到後拉回。"""
    s = _upto(closes, as_of)
    out: list[dict] = []
    if s.empty:
        return out
    close = float(s.iloc[-1])
    prev = float(s.iloc[-2]) if len(s) >= 2 else None
    tol = cfg["ma_touch_tolerance"]
    for n in cfg["ma_days"]:
        if len(s) < n:
            continue
        ma = float(s.iloc[-n:].mean())
        item: dict[str, Any] = {"days": n, "value": round(ma, 2), "above": close >= ma, "gap": round(close / ma - 1, 4)}
        if prev is not None and len(s) >= n + 1:
            prev_ma = float(s.iloc[-n - 1:-1].mean())
            if prev >= prev_ma and close < ma:
                item["crossed"] = "down"
            elif prev < prev_ma and close >= ma:
                item["crossed"] = "up"
        if high is not None and close < ma <= high * (1 + tol):
            item["touched"] = "resistance"   # 盤中碰到均線後收在均線下
        elif low is not None and close > ma >= low * (1 - tol):
            item["touched"] = "support"      # 盤中跌到均線後收在均線上
        out.append(item)
    return out


def prior_run(closes: pd.Series, as_of: date, days: int) -> dict[str, Any] | None:
    """今天以前 days 個交易日的累計漲跌（不含今天），判斷短線漲多或跌深。"""
    s = _upto(closes, as_of)
    if len(s) < days + 2:
        return None
    end, start = float(s.iloc[-2]), float(s.iloc[-2 - days])
    return {"days": days, "pct": round(end / start - 1, 4), "points": round(end - start, 2)}


# ---------- 全市場：寬度、成交值、權值股貢獻 ----------

def breadth(rows: list[dict], cfg: dict) -> dict[str, Any] | None:
    """rows：{close, prev_close}。上漲、下跌、平盤、漲停、跌停家數。"""
    valid = [r for r in rows if r.get("close") and r.get("prev_close")]
    if len(valid) < cfg["min_market_rows"]:
        return None
    up = down = flat = lu = ld = 0
    limit = cfg["limit_move"]
    for r in valid:
        ret = r["close"] / r["prev_close"] - 1
        if ret > 0:
            up += 1
        elif ret < 0:
            down += 1
        else:
            flat += 1
        if ret >= limit:
            lu += 1
        elif ret <= -limit:
            ld += 1
    moved = up + down
    return {"advancers": up, "decliners": down, "unchanged": flat, "limit_up": lu, "limit_down": ld,
            "total": len(valid), "adv_ratio": round(up / moved, 3) if moved else None}


def turnover(totals: dict[date, tuple[float, int]], as_of: date, cfg: dict) -> dict[str, Any] | None:
    """上市成交值與近 N 日平均比較。totals：日期 →（成交金額, 筆數）。筆數不足的日期（只有自選股回補）不計。"""
    full = {d: v for d, (v, n) in totals.items() if n >= cfg["min_market_rows"] // 2 and v}
    if as_of not in full:
        return None
    today = full[as_of]
    past = [full[d] for d in sorted(full) if d < as_of][-cfg["volume_avg_days"]:]
    avg = sum(past) / len(past) if past else None
    return {"value": today, "avg": avg, "ratio": round(today / avg, 3) if avg else None, "avg_days": len(past)}


def contributors(rows: list[dict], prev_index: float, cfg: dict) -> dict[str, Any] | None:
    """上市股票對加權指數的貢獻點數（估算）：前一日指數 ×（股數 × 漲跌金額）÷ 前一日總市值。"""
    valid = [r for r in rows if r.get("market") == "twse" and r.get("shares") and r.get("close") and r.get("prev_close")]
    base = sum(r["shares"] * r["prev_close"] for r in valid)
    if not valid or base <= 0:
        return None
    items = []
    for r in valid:
        pts = prev_index * r["shares"] * (r["close"] - r["prev_close"]) / base
        items.append({"stock_id": r["stock_id"], "name": r.get("name"), "points": round(pts, 1),
                      "pct": round(r["close"] / r["prev_close"] - 1, 4)})
    items.sort(key=lambda x: x["points"])
    n = cfg["top_contributors"]
    return {"estimated_change": round(sum(x["points"] for x in items), 1),
            "drags": [x for x in items[:n] if x["points"] < 0],
            "boosts": [x for x in reversed(items[-n:]) if x["points"] > 0]}


# ---------- 法人 ----------

INVESTORS = {"foreign": "外資", "trust": "投信", "dealer": "自營商"}


def flows(by_date: dict[date, dict[str, float]], as_of: date) -> dict[str, Any] | None:
    """by_date：日期 → {foreign, trust, dealer}（上市，元）。今日金額與外資連續買／賣超天數。"""
    if as_of not in by_date:
        return None
    today = by_date[as_of]
    out: dict[str, Any] = {k: round(today.get(k) or 0) for k in INVESTORS}
    out["total"] = sum(out[k] for k in INVESTORS)
    for k in INVESTORS:
        sign = (today.get(k) or 0) > 0
        streak = 0
        for d in sorted((d for d in by_date if d <= as_of), reverse=True):
            v = by_date[d].get(k) or 0
            if v == 0 or (v > 0) != sign:
                break
            streak += 1
        out[f"{k}_streak"] = streak if (today.get(k) or 0) != 0 else 0
    return out


# ---------- 族群 ----------

def group_moves(groups: list[dict], moves: dict[str, float], cfg: dict) -> dict[str, list[dict]]:
    """族群當日漲跌（成分股等權平均）。groups：market.group_strength；moves：代號 → 當日漲跌幅。"""
    rows = []
    for g in groups:
        rets = [moves[s] for s in g.get("members") or [] if s in moves]
        if len(rets) < cfg["group_min_members"]:
            continue
        rows.append({"name": g["name"], "pct": round(sum(rets) / len(rets), 4), "count": len(rets)})
    rows.sort(key=lambda r: r["pct"], reverse=True)
    n = cfg["top_groups"]
    return {"strong": [r for r in rows[:n] if r["pct"] > 0], "weak": [r for r in reversed(rows[-n:]) if r["pct"] < 0],
            "count": len(rows)}


# ---------- 隔夜美股、匯率、期貨 ----------

def last_change(s: pd.Series, as_of: date, strict: bool) -> dict[str, Any] | None:
    """序列最後一筆相對前一筆的變化。strict＝True 只看 as_of 以前（美股：台股收盤時當天美股尚未開盤）。"""
    t = _upto(s, as_of, strict=strict)
    if len(t) < 2:
        return None
    v, p = float(t.iloc[-1]), float(t.iloc[-2])
    return {"value": round(v, 4), "prev": round(p, 4), "change": round(v - p, 4),
            "pct": round(v / p - 1, 4) if p else None, "date": t.index[-1].date().isoformat()}


def overnight(series: dict[str, pd.Series], as_of: date) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, strict in (("sox", True), ("tsm_adr", True), ("usd_twd", False), ("foreign_tx_oi", False)):
        if key in series:
            ch = last_change(series[key], as_of, strict)
            if ch and (strict or ch["date"] == as_of.isoformat()):
                out[key] = ch
    return out
