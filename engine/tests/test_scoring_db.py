import json
from datetime import date

from engine.config import load_settings
from engine.repository import Repository
from engine.scoring.loader import load_frames, load_groups, save_scores
from engine.scoring.model import score_universe
from engine.tests.test_scoring import AS_OF, frames


def _insert(repo: Repository, sid: str, drift: float, seed: int, growth: float):
    f = frames(sid, drift, foreign=100_000 * (1 if drift > 0 else -1), growth=growth, seed=seed)
    repo.upsert_stocks([{"stock_id": sid, "name": sid, "market": "twse", "industry": "半導體業"}])
    repo.upsert("daily_prices", [
        {"stock_id": sid, "date": d.date(), **{k: float(v) for k, v in row.items()}, "turnover": None}
        for d, row in f.prices.iterrows()
    ])
    repo.upsert("valuations", [
        {"stock_id": sid, "date": d.date(), "pe": 15.0, "pb": 2.0, "dividend_yield": 3.0} for d in f.prices.index
    ])
    repo.upsert_chips([
        {"stock_id": sid, "date": d.date(), "foreign_net": int(r.foreign_net), "trust_net": int(r.trust_net),
         "margin_balance": int(r.margin_balance)}
        for d, r in f.chips.iterrows()
    ])
    repo.upsert("financials", [{"stock_id": sid, **row} for row in f.financials.to_dict("records")])


def test_score_from_database(conn):
    repo = Repository(conn)
    for i, (sid, drift, growth) in enumerate([("1111", 0.002, 0.02), ("2222", -0.002, -0.01), ("3333", 0.0, 0.0)]):
        _insert(repo, sid, drift, i + 1, growth)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO groups (name, kind) VALUES ('測試族群', 'custom') RETURNING group_id")
        gid = cur.fetchone()[0]
        cur.executemany("INSERT INTO group_members VALUES (%s, %s)", [(gid, s) for s in ("1111", "2222", "3333")])
    conn.commit()

    settings = load_settings("free")
    ids = ["1111", "2222", "3333"]
    frames_ = load_frames(repo, ids, AS_OF, settings.scoring["lookback_years"])
    assert len(frames_["1111"].prices) > 800
    # 載入時就排除未公布的月營收
    assert frames_["1111"].financials["published_date"].max() <= AS_OF

    results = score_universe(settings, frames_, load_groups(repo), AS_OF)
    assert save_scores(repo, results) == 6
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT stock_id, horizon, total, status, breakdown FROM scores WHERE date = %s ORDER BY 1, 2",
                    (AS_OF,))
        rows = cur.fetchall()
    assert len(rows) == 6
    totals = {(s, h): float(t) for s, h, t, _st, _b in rows}
    assert totals[("1111", "short")] > totals[("2222", "short")]
    breakdown = rows[0][4] if isinstance(rows[0][4], dict) else json.loads(rows[0][4])
    assert breakdown["peers"] == ids
    # 重跑同一天不會重複
    save_scores(repo, results)
    assert repo.count_rows("scores", AS_OF) == 6


def test_score_before_revenue_published(conn):
    repo = Repository(conn)
    _insert(repo, "1111", 0.002, 1, 0.02)
    conn.commit()
    f = load_frames(repo, ["1111"], date(2026, 11, 6), 3)["1111"]
    assert "2026-10" not in set(f.financials["period"])  # 2026-11-10 才公布
