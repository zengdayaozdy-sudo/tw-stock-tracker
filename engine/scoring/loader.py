"""從資料庫載入評分所需資料，並寫回 scores。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from engine.adjust import adjust_prices
from engine.repository import Repository
from engine.scoring.indicators import StockFrames
from engine.scoring.model import ScoreResult

PRICE_COLS = ["open", "high", "low", "close", "volume"]
CHIP_COLS = ["foreign_net", "trust_net", "dealer_net", "foreign_holding_pct", "margin_balance",
             "short_balance", "major_holder_pct", "sbl_balance", "branches"]
VALUATION_COLS = ["pe", "pb", "dividend_yield"]


def _frame(rows: list[tuple], cols: list[str], numeric: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["stock_id", "date", *cols])
    df["date"] = pd.to_datetime(df["date"])
    for c in numeric:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def _split(df: pd.DataFrame, cols: list[str]) -> dict[str, pd.DataFrame]:
    return {sid: g.set_index("date")[cols].sort_index() for sid, g in df.groupby("stock_id")}


def load_frames(repo: Repository, ids: list[str], as_of: date, lookback_years: int) -> dict[str, StockFrames]:
    start = as_of - timedelta(days=int(365.25 * (lookback_years + 1.3)))  # 年線需要多一年
    conn = repo.conn
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT stock_id, date, {', '.join(PRICE_COLS)} FROM daily_prices "
            "WHERE stock_id = ANY(%s) AND date BETWEEN %s AND %s", (ids, start, as_of))
        prices = _split(_frame(cur.fetchall(), PRICE_COLS, PRICE_COLS), PRICE_COLS)

        cur.execute(
            f"SELECT stock_id, date, {', '.join(CHIP_COLS)} FROM chips "
            "WHERE stock_id = ANY(%s) AND date BETWEEN %s AND %s", (ids, start, as_of))
        chips = _split(_frame(cur.fetchall(), CHIP_COLS, CHIP_COLS[:-1]), CHIP_COLS)

        cur.execute(
            f"SELECT stock_id, date, {', '.join(VALUATION_COLS)} FROM valuations "
            "WHERE stock_id = ANY(%s) AND date BETWEEN %s AND %s", (ids, start, as_of))
        valuations = _split(_frame(cur.fetchall(), VALUATION_COLS, VALUATION_COLS), VALUATION_COLS)

        # 只取當時已公布的財報
        cur.execute(
            "SELECT stock_id, period_type, period, published_date, metrics FROM financials "
            "WHERE stock_id = ANY(%s) AND published_date <= %s", (ids, as_of))
        fin = pd.DataFrame(cur.fetchall(), columns=["stock_id", "period_type", "period", "published_date", "metrics"])
        fins = {sid: g.drop(columns="stock_id").reset_index(drop=True) for sid, g in fin.groupby("stock_id")}

        cur.execute("SELECT stock_id, industry, market_cap, shares, pledge_date, pledge_ratio FROM stocks "
                    "WHERE stock_id = ANY(%s)", (ids,))
        meta, extra = {}, {}
        for sid, ind, cap, shares, pdate, pratio in cur.fetchall():
            meta[sid] = (ind, float(cap) if cap is not None else None)
            extra[sid] = (float(shares) if shares else None,
                          (pdate, float(pratio)) if pdate is not None and pratio is not None else None)

        # 加權指數（相對強弱的比較基準）
        cur.execute("SELECT date, value FROM market_series WHERE series = 'taiex' AND date BETWEEN %s AND %s "
                    "ORDER BY date", (start, as_of))
        bench_rows = cur.fetchall()
    benchmark = pd.Series([float(v) for _, v in bench_rows],
                          index=pd.DatetimeIndex([pd.Timestamp(d) for d, _ in bench_rows]), dtype=float)

    # 還原股價（除權息、分割、減資），只用 as_of 當天以前已發生的調整
    adjustments = repo.adjustments(ids)
    for sid, frame in prices.items():
        adj = [(d, f) for d, f in adjustments.get(sid, []) if d <= as_of]
        prices[sid] = adjust_prices(frame, adj)

    empty_p = pd.DataFrame(columns=PRICE_COLS, index=pd.DatetimeIndex([]), dtype=float)
    empty_c = pd.DataFrame(columns=CHIP_COLS, index=pd.DatetimeIndex([]))
    empty_v = pd.DataFrame(columns=VALUATION_COLS, index=pd.DatetimeIndex([]), dtype=float)
    empty_f = pd.DataFrame(columns=["period_type", "period", "published_date", "metrics"])
    return {
        sid: StockFrames(
            stock_id=sid,
            prices=prices.get(sid, empty_p),
            chips=chips.get(sid, empty_c),
            valuations=valuations.get(sid, empty_v),
            financials=fins.get(sid, empty_f),
            industry=meta.get(sid, (None, None))[0],
            market_cap=meta.get(sid, (None, None))[1],
            benchmark=benchmark,
            shares=extra.get(sid, (None, None))[0],
            pledge=extra.get(sid, (None, None))[1],
        )
        for sid in ids
    }


def load_groups(repo: Repository) -> dict[str, tuple[str, set[str]]]:
    with repo.conn.cursor() as cur:
        cur.execute(
            "SELECT g.name, g.kind, gm.stock_id FROM groups g JOIN group_members gm USING (group_id)"
        )
        out: dict[str, tuple[str, set[str]]] = {}
        for name, kind, sid in cur.fetchall():
            out.setdefault(name, (kind, set()))[1].add(sid)
        return out


def save_scores(repo: Repository, results: list[ScoreResult]) -> int:
    return repo.upsert("scores", [r.row() for r in results])
