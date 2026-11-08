"""濾網與資料庫整合測試：用假的資料來源跑完流動性、排雷、燈號、族群、事件，並驗證可重跑。"""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from engine.ai import FallbackJudge, RuleJudge
from engine.config import TZ, load_settings
from engine.filters.runner import run_filters, run_liquidity, run_news, screen_summary
from engine.repository import Repository

AS_OF = date(2026, 11, 6)
DATES = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=90)
STOCKS = {
    # 代號: (產業, 每日漲幅, 每日成交股數)
    "2330": ("半導體業", 0.004, 30_000_000),
    "2303": ("半導體業", 0.003, 20_000_000),
    "1101": ("水泥工業", -0.004, 5_000_000),
    "1102": ("水泥工業", -0.003, 4_000_000),
    "2603": ("航運業", 0.0, 8_000_000),
    "9999": ("其他", 0.001, 100_000),  # 流動性不足
}


@pytest.fixture()
def repo(conn):
    r = Repository(conn)
    r.upsert_stocks([{"stock_id": s, "name": s, "market": "twse", "industry": ind} for s, (ind, _, _) in STOCKS.items()])
    rows = []
    for sid, (_ind, drift, vol) in STOCKS.items():
        closes = 100 * np.cumprod(np.full(len(DATES), 1 + drift))
        rows += [{"stock_id": sid, "date": d.date(), "open": c, "high": c, "low": c, "close": float(c),
                  "volume": vol, "turnover": None} for d, c in zip(DATES, closes, strict=True)]
    r.upsert("daily_prices", rows)
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO watchlist (stock_id) VALUES (%s)", [(s,) for s in STOCKS])
        cur.execute("UPDATE stocks SET status = '{attention}' WHERE stock_id = '2603'")
        for ind in {v[0] for v in STOCKS.values()}:
            cur.execute("INSERT INTO groups (name, kind) VALUES (%s, 'industry') RETURNING group_id", (ind,))
            gid = cur.fetchone()[0]
            cur.executemany("INSERT INTO group_members VALUES (%s, %s)",
                            [(gid, s) for s, v in STOCKS.items() if v[0] == ind])
    r.upsert("scores", [
        {"stock_id": s, "date": AS_OF, "horizon": h, "total": t, "fundamental": t, "technical": t, "chips": t,
         "breakdown": {}, "status": "ok"}
        for s, t in {"2330": 80, "2303": 70, "1101": 30, "1102": 35, "2603": 50, "9999": 55}.items()
        for h in ("short", "long")
    ])
    # 已公布 10 月營收的只有 2330
    r.upsert("financials", [{"stock_id": "2330", "period_type": "month", "period": "2026-10",
                             "published_date": date(2026, 11, 5), "metrics": {"revenue": 1}}])
    conn.commit()
    return r


class FakeExchange:
    http = None
    last_errors: list = []

    def __init__(self, taiex_drift=0.002):
        self.taiex_drift = taiex_drift

    def announcements(self):
        t = datetime(2026, 11, 6, 17, 30, tzinfo=TZ)
        return [
            {"stock_id": "1101", "published_at": t, "source": "twse_announcement",
             "title": "公告本公司董事會決議辦理現金增資", "body": "發行新股", "url": None},
            {"stock_id": "2330", "published_at": t, "source": "twse_announcement",
             "title": "公告本公司董事會通過第三季財報", "body": None, "url": None},
            {"stock_id": "8888", "published_at": t, "source": "twse_announcement",  # 不在自選股
             "title": "現金增資", "body": None, "url": None},
        ]

    def taiex_month(self, month):
        out = []
        for i, d in enumerate(DATES):
            if d.month == month.month and d.year == month.year:
                out.append({"series": "taiex", "date": d.date(), "value": 20000 * (1 + self.taiex_drift) ** i})
        return out

    def ex_dividend_events(self):
        return [{"stock_id": "2303", "date": date(2026, 11, 10), "event_type": "除權息", "note": "除息"},
                {"stock_id": "8888", "date": date(2026, 11, 10), "event_type": "除權息", "note": "除息"}]

    def shareholder_meetings(self):
        return [{"stock_id": "2330", "date": date(2026, 6, 1), "event_type": "股東會", "note": "股東常會"}]

    def holidays(self):
        return set()


class FakeFinMind:
    def __init__(self, bearish=False):
        self.bearish = bearish
        self.calls = []

    def fetch(self, key, data_id, start, end=None):
        self.calls.append((key, data_id))
        days = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=60)
        sign = -1 if self.bearish else 1
        if key == "us_price":
            base = 9000 if data_id == "^SOX" else 180
            return [{"date": d.date().isoformat(), "stock_id": data_id, "Close": base * (1 + sign * 0.003) ** i}
                    for i, d in enumerate(days)]
        if key == "exchange_rate":
            return [{"date": d.date().isoformat(), "spot_buy": 32 * (1 + sign * -0.001) ** i,
                     "spot_sell": 32.1 * (1 + sign * -0.001) ** i} for i, d in enumerate(days)]
        if key == "futures_institutional":
            net = -60000 if self.bearish else 5000
            return [{"date": d.date().isoformat(), "institutional_investors": "外資",
                     "long_open_interest_balance_volume": max(net, 0) + 20000,
                     "short_open_interest_balance_volume": max(-net, 0) + 20000} for d in days]
        raise AssertionError(key)


def test_liquidity_step(repo):
    settings = load_settings("free")
    result = run_liquidity(settings, repo, AS_OF)
    reasons = {(f["stock_id"], f["reason"]) for f in result["flags"]}
    assert ("2603", "注意股") in reasons
    assert any(s == "9999" and "日均量" in r for s, r in reasons)
    assert result["excluded"] == 2
    # 重跑不會重複
    run_liquidity(settings, repo, AS_OF)
    assert len(repo.flags(AS_OF)["9999"]) == 1


def test_news_step_with_rule_fallback(repo):
    settings = load_settings("free")
    judge = FallbackJudge(None, RuleJudge(settings.filters["news"]))
    result = run_news(settings, repo, FakeExchange(), None, judge, AS_OF)
    assert result["collected"] == 2 and result["judged"] == 2
    assert [f["stock_id"] for f in result["flags"]] == ["1101"]
    # 重跑：同一則不重複收集、不重複判讀
    again = run_news(settings, repo, FakeExchange(), None, judge, AS_OF)
    assert again["judged"] == 0 and again["excluded"] == 1
    with repo.conn.cursor() as cur:
        cur.execute("SELECT count(*), count(*) FILTER (WHERE judged_by = 'rules') FROM news")
        assert cur.fetchone() == (2, 2)
    # 三天後超出 lookback 期間，不再排除
    later = run_news(settings, repo, FakeExchange(), None, judge, AS_OF + timedelta(days=5), collect=False)
    assert later["excluded"] == 0


def test_filters_step_bullish(repo):
    settings = load_settings("free")
    run_liquidity(settings, repo, AS_OF)
    result = run_filters(settings, repo, FakeExchange(), FakeFinMind(), AS_OF)
    assert result["warnings"] == []
    assert result["light"] == "bullish"
    assert result["weak_groups"] == ["航運業", "水泥工業"]  # 4 個族群 × 30% → 後 2 名

    with repo.conn.cursor() as cur:
        cur.execute("SELECT light, inputs, group_strength FROM market WHERE date = %s", (AS_OF,))
        light, inputs, strength = cur.fetchone()
    assert light == "bullish" and inputs["total"] == result["light_total"]
    assert not inputs["tsmc_adr_premium"].get("missing")
    assert strength[0]["name"] == "半導體業"

    summary = screen_summary(repo, AS_OF)
    assert summary["1101"]["block"] == ["弱勢族群：水泥工業"]
    assert summary["2603"]["block"] == ["弱勢族群：航運業"]
    assert "2330" not in summary or not summary["2330"]["block"]
    assert any("除權息 11/10" in w for w in summary["2303"]["warn"])
    assert any("月營收" in w for w in summary["2303"]["warn"])          # 尚未公布 10 月營收
    assert not any("月營收" in w for w in summary.get("2330", {}).get("warn", []))

    # 被排除的股票在 scores 標記 excluded
    with repo.conn.cursor() as cur:
        cur.execute("SELECT stock_id FROM scores WHERE date = %s AND status = 'excluded' AND horizon = 'short' "
                    "ORDER BY 1", (AS_OF,))
        assert [r[0] for r in cur.fetchall()] == ["2603", "9999"]


def test_filters_step_bearish_blocks_all_and_rerun_is_stable(repo):
    settings = load_settings("free")
    result = run_filters(settings, repo, FakeExchange(taiex_drift=-0.004), FakeFinMind(bearish=True), AS_OF)
    assert result["light"] == "bearish"
    summary = screen_summary(repo, AS_OF)
    assert all("大盤燈號偏空" in summary[s]["block"] for s in STOCKS)

    # 改成偏多後重跑（只用資料庫既有資料），偏空擋買進的旗標要清掉
    with repo.conn.cursor() as cur:
        cur.execute("DELETE FROM market_series")
    repo.conn.commit()
    run_filters(settings, repo, FakeExchange(), FakeFinMind(), AS_OF)
    summary = screen_summary(repo, AS_OF)
    assert not any("大盤燈號偏空" in summary.get(s, {}).get("block", []) for s in STOCKS)


def test_market_series_incremental_refresh(repo):
    settings = load_settings("free")
    fm_client = FakeFinMind()
    run_filters(settings, repo, FakeExchange(), fm_client, AS_OF)
    n = repo.series("taiex", AS_OF - timedelta(days=400), AS_OF)
    assert len(n) == len(DATES) - sum(1 for d in DATES if d.date() < AS_OF - timedelta(days=200))
    assert len(fm_client.calls) == 4  # 每日只需 4 次 FinMind 呼叫


def test_filters_step_sponsor_plan_uses_market_wide_groups(repo):
    settings = load_settings("sponsor")
    with repo.conn.cursor() as cur:
        cur.execute("DELETE FROM watchlist")  # 付費模式不看自選股
    repo.conn.commit()
    result = run_filters(settings, repo, FakeExchange(), FakeFinMind(), AS_OF)
    assert result["groups_ranked"] == 4
    assert "1101" in screen_summary(repo, AS_OF)
