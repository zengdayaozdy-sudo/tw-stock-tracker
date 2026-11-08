"""資料庫讀寫。所有寫入皆為 upsert，可重複執行（補跑同一天不會重複）。"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

UPSERT_SPECS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # 資料表: (主鍵欄位, 其他欄位)
    "daily_prices": (("stock_id", "date"), ("open", "high", "low", "close", "volume", "turnover")),
    "valuations": (("stock_id", "date"), ("pe", "pb", "dividend_yield")),
    "financials": (("stock_id", "period_type", "period"), ("published_date", "metrics")),
    "scores": (("stock_id", "date", "horizon"), ("total", "fundamental", "technical", "chips", "breakdown", "status")),
    "market_series": (("series", "date"), ("value",)),
    "market": (("date",), ("light", "inputs", "group_strength")),
    "events": (("stock_id", "date", "event_type"), ("note", "source")),
}

# chips 由多個來源分批寫入，只更新本次有提供的欄位
CHIP_COLUMNS = (
    "foreign_net", "trust_net", "dealer_net", "foreign_holding_pct",
    "margin_balance", "short_balance", "major_holder_pct", "branches",
)


def _adapt(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


class Repository:
    def __init__(self, conn):
        self.conn = conn

    def upsert(self, table: str, rows: Iterable[dict]) -> int:
        keys, cols = UPSERT_SPECS[table]
        rows = list(rows)
        if not rows:
            return 0
        all_cols = keys + cols
        sql = (
            f"INSERT INTO {table} ({', '.join(all_cols)}) VALUES ({', '.join(['%s'] * len(all_cols))}) "
            f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET "
            + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        )
        with self.conn.cursor() as cur:
            cur.executemany(sql, [tuple(_adapt(r.get(c)) for c in all_cols) for r in rows])
        return len(rows)

    def upsert_chips(self, rows: Iterable[dict]) -> int:
        # 依欄位組合分批，用 executemany 一次送出（逐筆送出跨洲連線會非常慢）
        batches: dict[tuple[str, ...], list[tuple]] = {}
        for r in rows:
            cols = tuple(c for c in CHIP_COLUMNS if c in r)
            if cols:
                batches.setdefault(cols, []).append((r["stock_id"], r["date"], *(_adapt(r[c]) for c in cols)))
        with self.conn.cursor() as cur:
            for cols, values in batches.items():
                cur.executemany(
                    f"INSERT INTO chips (stock_id, date, {', '.join(cols)}) "
                    f"VALUES ({', '.join(['%s'] * (len(cols) + 2))}) "
                    f"ON CONFLICT (stock_id, date) DO UPDATE SET "
                    + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols),
                    values,
                )
        return sum(len(v) for v in batches.values())

    def upsert_stocks(self, rows: Iterable[dict]) -> int:
        rows = list(rows)
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO stocks (stock_id, name, market, industry, listed_date, shares) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (stock_id) DO UPDATE SET name = EXCLUDED.name, market = EXCLUDED.market, "
                "industry = COALESCE(EXCLUDED.industry, stocks.industry), "
                "listed_date = COALESCE(EXCLUDED.listed_date, stocks.listed_date), "
                "shares = COALESCE(EXCLUDED.shares, stocks.shares), updated_at = now()",
                [
                    (r["stock_id"], r["name"], r["market"], r.get("industry"), r.get("listed_date"), r.get("shares"))
                    for r in rows
                ],
            )
        return len(rows)

    def update_market_caps(self, trade_date: date) -> int:
        """市值 = 發行股數 × 當日收盤價。"""
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE stocks s SET market_cap = s.shares * p.close FROM daily_prices p "
                "WHERE p.stock_id = s.stock_id AND p.date = %s AND s.shares IS NOT NULL AND p.close IS NOT NULL",
                (trade_date,),
            )
            return cur.rowcount

    def set_status_flags(self, flags: dict[str, set[str]]) -> None:
        with self.conn.cursor() as cur:
            cur.execute("UPDATE stocks SET status = '{}' WHERE status <> '{}'")
            for stock_id, f in flags.items():
                cur.execute("UPDATE stocks SET status = %s WHERE stock_id = %s", (sorted(f), stock_id))

    # ---------- 讀取 ----------
    def watchlist(self) -> list[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT stock_id FROM watchlist ORDER BY stock_id")
            return [r[0] for r in cur.fetchall()]

    def all_stock_ids(self) -> list[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT stock_id FROM stocks WHERE delisted_date IS NULL ORDER BY stock_id")
            return [r[0] for r in cur.fetchall()]

    def known_stock_ids(self) -> set[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT stock_id FROM stocks")
            return {r[0] for r in cur.fetchall()}

    def latest_period(self, stock_id: str, period_type: str) -> str | None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT max(period) FROM financials WHERE stock_id = %s AND period_type = %s",
                (stock_id, period_type),
            )
            return cur.fetchone()[0]

    def financials_as_of(self, stock_id: str, as_of: date, period_type: str) -> list[dict]:
        """只回傳在 as_of 當天（含）以前已公布的財報，避免偷看未來。"""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT period, published_date, metrics FROM financials "
                "WHERE stock_id = %s AND period_type = %s AND published_date <= %s ORDER BY period",
                (stock_id, period_type, as_of),
            )
            return [{"period": p, "published_date": d, "metrics": m} for p, d, m in cur.fetchall()]

    def count_rows(self, table: str, on_date: date) -> int:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {table} WHERE date = %s", (on_date,))
            return cur.fetchone()[0]

    def record_api_usage(self, provider: str, hour: datetime, calls: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO api_usage (provider, hour, calls) VALUES (%s, %s, %s) "
                "ON CONFLICT (provider, hour) DO UPDATE SET calls = GREATEST(api_usage.calls, EXCLUDED.calls)",
                (provider, hour, calls),
            )
        self.conn.commit()

    def api_usage(self, provider: str, hour: datetime) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT calls FROM api_usage WHERE provider = %s AND hour = %s", (provider, hour))
            row = cur.fetchone()
            return row[0] if row else 0

    # ---------- 濾網（第 4 階段） ----------
    def replace_flags(self, on_date: date, filter_name: str, rows: Iterable[dict]) -> int:
        """覆寫某一濾網在某日的結果（重跑同一步驟不會殘留舊資料）。"""
        rows = list(rows)
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM screen_flags WHERE date = %s AND filter = %s", (on_date, filter_name))
            cur.executemany(
                "INSERT INTO screen_flags (stock_id, date, filter, kind, reason, detail) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [
                    (r["stock_id"], on_date, filter_name, r["kind"], r["reason"], _adapt(r.get("detail") or {}))
                    for r in rows
                ],
            )
        return len(rows)

    def flags(self, on_date: date) -> dict[str, list[dict]]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT stock_id, filter, kind, reason, detail FROM screen_flags WHERE date = %s "
                "ORDER BY stock_id, filter, reason",
                (on_date,),
            )
            out: dict[str, list[dict]] = {}
            for sid, f, kind, reason, detail in cur.fetchall():
                out.setdefault(sid, []).append({"filter": f, "kind": kind, "reason": reason, "detail": detail})
            return out

    def series(self, name: str, start: date, end: date) -> list[tuple[date, float]]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT date, value FROM market_series WHERE series = %s AND date BETWEEN %s AND %s ORDER BY date",
                (name, start, end),
            )
            return [(d, float(v)) for d, v in cur.fetchall()]

    def latest_series_date(self, name: str) -> date | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT max(date) FROM market_series WHERE series = %s", (name,))
            return cur.fetchone()[0]

    def closes(self, ids: list[str], start: date, end: date) -> list[tuple[str, date, float]]:
        if not ids:
            return []
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT stock_id, date, close FROM daily_prices "
                "WHERE stock_id = ANY(%s) AND date BETWEEN %s AND %s AND close IS NOT NULL ORDER BY date",
                (ids, start, end),
            )
            return [(s, d, float(c)) for s, d, c in cur.fetchall()]

    def insert_news(self, rows: Iterable[dict]) -> int:
        """新增新聞與重大訊息；已存在（同一去重鍵）者略過，不會覆寫既有判讀。"""
        rows = list(rows)
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO news (stock_id, published_at, source, title, url, body, dedup_key) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [
                    (r["stock_id"], r["published_at"], r["source"], r["title"], r.get("url"), r.get("body"),
                     r["dedup_key"])
                    for r in rows
                ],
            )
        return len(rows)

    def unjudged_news(self, ids: list[str], since: datetime, limit: int) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT news_id, stock_id, published_at, source, title, body, url FROM news "
                "WHERE stock_id = ANY(%s) AND published_at >= %s AND is_major_negative IS NULL "
                "ORDER BY published_at DESC LIMIT %s",
                (ids, since, limit),
            )
            cols = ("news_id", "stock_id", "published_at", "source", "title", "body", "url")
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def save_judgment(self, news_id: int, j: dict) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE news SET is_major_negative = %s, event_type = %s, confidence = %s, summary = %s, "
                "judged_by = %s, judged_at = now() WHERE news_id = %s",
                (j["is_major_negative"], j.get("event_type"), j.get("confidence"), j.get("summary"),
                 j.get("judged_by"), news_id),
            )

    def negative_news(self, ids: list[str], since: datetime, until: datetime) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT news_id, stock_id, published_at, title, url, event_type, confidence, summary, judged_by "
                "FROM news WHERE stock_id = ANY(%s) AND published_at BETWEEN %s AND %s "
                "AND is_major_negative ORDER BY published_at",
                (ids, since, until),
            )
            cols = ("news_id", "stock_id", "published_at", "title", "url", "event_type", "confidence", "summary",
                    "judged_by")
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def events_between(self, ids: list[str], start: date, end: date) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT stock_id, date, event_type, note FROM events "
                "WHERE stock_id = ANY(%s) AND date BETWEEN %s AND %s ORDER BY date",
                (ids, start, end),
            )
            return [dict(zip(("stock_id", "date", "event_type", "note"), r, strict=True)) for r in cur.fetchall()]

    def stock_status(self, ids: list[str]) -> dict[str, list[str]]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT stock_id, status FROM stocks WHERE stock_id = ANY(%s)", (ids,))
            return {s: list(st or []) for s, st in cur.fetchall()}

    def avg_volume(self, ids: list[str], as_of: date, days: int) -> dict[str, tuple[float, int]]:
        """近 N 個交易日（含當日）平均成交股數與實際筆數。"""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT stock_id, avg(volume), count(*) FROM ("
                " SELECT stock_id, volume, row_number() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn"
                " FROM daily_prices WHERE stock_id = ANY(%s) AND date <= %s"
                ") t WHERE rn <= %s GROUP BY stock_id",
                (ids, as_of, days),
            )
            return {s: (float(v or 0), int(n)) for s, v, n in cur.fetchall()}

    def financial_periods(self, ids: list[str], as_of: date) -> dict[tuple[str, str], str]:
        """每檔在 as_of 當天已公布的最新期別 {(stock_id, period_type): period}。"""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT stock_id, period_type, max(period) FROM financials "
                "WHERE stock_id = ANY(%s) AND published_date <= %s GROUP BY 1, 2",
                (ids, as_of),
            )
            return {(s, t): p for s, t, p in cur.fetchall()}

    def score_totals(self, on_date: date, horizon: str) -> dict[str, float]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT stock_id, total FROM scores WHERE date = %s AND horizon = %s AND status <> 'insufficient_data' "
                "AND total IS NOT NULL",
                (on_date, horizon),
            )
            return {s: float(t) for s, t in cur.fetchall()}

    def mark_excluded_scores(self, on_date: date, ids: Iterable[str]) -> int:
        """依當日排除結果更新 scores.status（先還原再標記，重跑結果一致）。"""
        with self.conn.cursor() as cur:
            cur.execute("UPDATE scores SET status = 'ok' WHERE date = %s AND status = 'excluded'", (on_date,))
            cur.execute(
                "UPDATE scores SET status = 'excluded' WHERE date = %s AND stock_id = ANY(%s) AND status = 'ok'",
                (on_date, list(ids)),
            )
            return cur.rowcount

    # ---------- 訊號（第 5 階段） ----------
    def latest_job(self, job: str, run_date: date) -> tuple[str, dict] | None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT status, detail FROM job_logs WHERE job = %s AND run_date = %s "
                "ORDER BY started_at DESC, log_id DESC LIMIT 1",
                (job, run_date),
            )
            row = cur.fetchone()
            return (row[0], row[1] or {}) if row else None

    def score_rows(self, on_date: date, ids: list[str]) -> dict[tuple[str, str], dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT stock_id, horizon, total, fundamental, technical, chips, breakdown, status FROM scores "
                "WHERE date = %s AND stock_id = ANY(%s)",
                (on_date, ids),
            )
            out = {}
            for sid, h, total, f, t, c, b, status in cur.fetchall():
                out[(sid, h)] = {
                    "total": None if total is None else float(total),
                    "fundamental": None if f is None else float(f),
                    "technical": None if t is None else float(t),
                    "chips": None if c is None else float(c),
                    "breakdown": b or {},
                    "status": status,
                }
            return out

    def market_light(self, on_date: date) -> str | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT light FROM market WHERE date = %s", (on_date,))
            row = cur.fetchone()
            return row[0] if row else None

    def setting(self, key: str, default: Any = None) -> Any:
        with self.conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else default

    def holdings_value(self, as_of: date) -> dict[str, float]:
        """目前持股市值（股數 × as_of 當天以前最後收盤，查無收盤時用成本）。"""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT h.stock_id, h.shares * COALESCE(("
                " SELECT close FROM daily_prices p WHERE p.stock_id = h.stock_id AND p.date <= %s"
                " AND p.close IS NOT NULL ORDER BY p.date DESC LIMIT 1), h.avg_cost) FROM holdings h",
                (as_of,),
            )
            return {s: float(v) for s, v in cur.fetchall()}

    def stock_names(self, ids: list[str]) -> dict[str, str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT stock_id, name FROM stocks WHERE stock_id = ANY(%s)", (ids,))
            return dict(cur.fetchall())

    def replace_signals(self, on_date: date, side: str, rows: Iterable[dict]) -> int:
        rows = list(rows)
        cols = ("stock_id", "date", "horizon", "side", "triggers", "entry_low", "entry_high", "stop_loss",
                "take_profit", "suggested_lots", "reason", "detail")
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM signals WHERE date = %s AND side = %s", (on_date, side))
            cur.executemany(
                f"INSERT INTO signals ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                [tuple(_adapt(r.get(c)) if c != "triggers" else r[c] for c in cols) for r in rows],
            )
        return len(rows)
