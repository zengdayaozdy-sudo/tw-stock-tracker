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
