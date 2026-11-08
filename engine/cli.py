"""命令列入口。每個步驟都能單獨執行，方便除錯與補跑指定日期。

用法：python -m engine.cli <指令> [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime

from engine.config import TZ, load_settings

PIPELINE_STEPS: list[tuple[str, str]] = []


def _parse_date(value: str | None) -> date:
    if not value:
        return datetime.now(TZ).date()
    return date.fromisoformat(value)


def _services():
    """建立資料庫連線與各資料來源（FinMind 額度與資料庫同步）。"""
    from engine.db import connect
    from engine.repository import Repository
    from engine.sources.common import HttpClient
    from engine.sources.exchanges import ExchangeClient
    from engine.sources.finmind import FinMindClient

    settings = load_settings()
    conn = connect()
    repo = Repository(conn)
    http = HttpClient.from_settings(settings.sources["http"])
    finmind = FinMindClient.create(settings, http, on_record=repo.record_api_usage)
    now = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    finmind.quota.preload(repo.api_usage("finmind", now))
    return settings, conn, repo, ExchangeClient(settings, http), finmind


def cmd_plan(_args) -> None:
    s = load_settings()
    print(f"目前方案：{s.plan.label}（{s.plan.name}）")
    print(f"選股範圍：{'自選股' if s.plan.universe == 'watchlist' else '全市場'}")
    print("已啟用：" + ("、".join(s.all_features[f] for f in sorted(s.plan.features)) or "（無）"))
    locked = s.locked_features()
    if locked:
        print("鎖定中：" + "、".join(locked.values()))


def cmd_migrate(_args) -> None:
    from engine.db import migrate

    applied = migrate()
    print("已套用：" + ("、".join(applied) if applied else "沒有新的 migration"))


def cmd_fetch(args) -> None:
    from engine.joblog import job_run
    from engine.pipeline import fetch_daily

    trade_date = _parse_date(args.date)
    settings, conn, repo, exchange, finmind = _services()
    with job_run(conn, "fetch", trade_date) as run:
        result = fetch_daily(settings, repo, exchange, finmind, trade_date)
        run.note(**result.to_dict())
        if not result.market_open:
            run.skip("休市或官方資料尚未更新")
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.market_open and not result.quality.ok:
        # 交給後續步驟：資料不完整時暫停訊號並推播警告（第 6 階段接上 LINE）
        raise SystemExit(2)


def cmd_backfill(args) -> None:
    from engine.joblog import job_run
    from engine.pipeline import backfill

    _settings, conn, repo, _exchange, finmind = _services()
    ids = args.stocks or repo.watchlist()
    with job_run(conn, "backfill", _parse_date(None)) as run:
        counts = backfill(repo, finmind, ids, args.years, _parse_date(None))
        run.note(stocks=ids, years=args.years, counts=counts)
    print(json.dumps(counts, ensure_ascii=False, indent=2))


def cmd_watch(args) -> None:
    from engine.db import connect
    from engine.repository import Repository

    settings = load_settings()
    conn = connect()
    repo = Repository(conn)
    with conn.cursor() as cur:
        if args.action == "add":
            known = repo.known_stock_ids()
            unknown = [s for s in args.stocks if s not in known]
            if unknown:
                raise SystemExit(f"股票清單中找不到：{'、'.join(unknown)}（請先執行一次 fetch 建立股票清單）")
            limit = settings.plan.watchlist_limit
            if limit and len(set(repo.watchlist()) | set(args.stocks)) > limit:
                raise SystemExit(f"免費模式自選股上限 {limit} 檔")
            cur.executemany(
                "INSERT INTO watchlist (stock_id) VALUES (%s) ON CONFLICT DO NOTHING", [(s,) for s in args.stocks]
            )
        elif args.action == "remove":
            cur.execute("DELETE FROM watchlist WHERE stock_id = ANY(%s)", (args.stocks,))
    conn.commit()
    print("自選股：" + "、".join(repo.watchlist()))


def cmd_seed_groups(_args) -> None:
    from engine.db import connect

    settings = load_settings()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT stock_id FROM stocks")
        known = {r[0] for r in cur.fetchall()}
        for name, members in (settings.groups.get("custom_groups") or {}).items():
            cur.execute(
                "INSERT INTO groups (name, kind) VALUES (%s, 'custom') "
                "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING group_id",
                (name,),
            )
            gid = cur.fetchone()[0]
            cur.executemany(
                "INSERT INTO group_members (group_id, stock_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                [(gid, s) for s in members if s in known],
            )
        cur.execute("SELECT DISTINCT industry FROM stocks WHERE industry IS NOT NULL")
        for (industry,) in cur.fetchall():
            cur.execute(
                "INSERT INTO groups (name, kind) VALUES (%s, 'industry') ON CONFLICT (name) DO NOTHING",
                (industry,),
            )
            cur.execute(
                "INSERT INTO group_members (group_id, stock_id) "
                "SELECT g.group_id, s.stock_id FROM groups g JOIN stocks s ON s.industry = g.name "
                "WHERE g.name = %s ON CONFLICT DO NOTHING",
                (industry,),
            )
    conn.commit()
    print("族群已建立")


def cmd_companies(_args) -> None:
    """更新上市櫃公司基本資料與產業別。"""
    _settings, conn, repo, exchange, _ = _services()
    n = repo.upsert_stocks(exchange.companies())
    conn.commit()
    print(f"已更新 {n} 家公司資料")


def cmd_probe(args) -> None:
    """實際呼叫各公開資料端點，列出欄位與第一筆內容，用來確認官方欄位名稱。"""
    from engine.sources.common import HttpClient

    settings = load_settings()
    http = HttpClient.from_settings(settings.sources["http"])
    when = _parse_date(args.date)
    for market in ("twse", "tpex"):
        for key, url in settings.sources[market]["endpoints"].items():
            url = url.format(date=when.strftime("%Y%m%d"))
            try:
                payload = http.get_json(url)
                rows = payload if isinstance(payload, list) else payload.get("data") or payload
                fields = payload.get("fields") if isinstance(payload, dict) else None
                sample = rows[0] if isinstance(rows, list) and rows else rows
                print(f"\n[{market}.{key}] 筆數={len(rows) if isinstance(rows, list) else '?'}")
                if fields:
                    print("  fields:", fields)
                if isinstance(sample, dict):
                    print("  keys:", list(sample.keys()))
                print("  sample:", json.dumps(sample, ensure_ascii=False)[:400])
            except Exception as e:
                print(f"\n[{market}.{key}] 失敗：{e}")


def cmd_score(args) -> None:
    from engine.db import connect
    from engine.joblog import job_run
    from engine.pipeline import universe
    from engine.repository import Repository
    from engine.scoring.loader import load_frames, load_groups, save_scores
    from engine.scoring.model import reasons, score_universe

    settings = load_settings()
    as_of = _parse_date(args.date)
    conn = connect()
    repo = Repository(conn)
    with job_run(conn, "score", as_of) as run:
        ids = universe(settings, repo)
        frames = load_frames(repo, ids, as_of, settings.scoring["lookback_years"])
        results = score_universe(settings, frames, load_groups(repo), as_of)
        n = save_scores(repo, results)
        ok = [r for r in results if r.status == "ok"]
        run.note(scored=len(ok), insufficient=len(results) - len(ok), rows=n)
    for h, label in (("short", "短線"), ("long", "中長線")):
        top = sorted((r for r in ok if r.horizon == h), key=lambda r: r.total, reverse=True)[:10]
        print(f"\n{label}前 10 名")
        for r in top:
            print(f"  {r.stock_id}  {r.total:6.1f}  {'、'.join(reasons(r.breakdown))}")
    skipped = {r.stock_id: r.breakdown.get("reason") for r in results if r.status != "ok"}
    for sid, reason in skipped.items():
        print(f"  {sid} 未評分：{reason}")


def _print_flags(flags: list[dict]) -> None:
    for f in flags:
        print(f"  {f['stock_id']}  {f['reason']}")


def cmd_liquidity(args) -> None:
    from engine.db import connect
    from engine.filters.runner import run_liquidity
    from engine.joblog import job_run
    from engine.repository import Repository

    settings = load_settings()
    as_of = _parse_date(args.date)
    conn = connect()
    with job_run(conn, "liquidity", as_of) as run:
        result = run_liquidity(settings, Repository(conn), as_of)
        run.note(universe=result["universe"], excluded=result["excluded"])
    print(f"流動性過濾：{result['universe']} 檔中排除 {result['excluded']} 檔")
    _print_flags(result["flags"])


def cmd_news(args) -> None:
    from engine.ai import create_judge
    from engine.filters.runner import run_news
    from engine.joblog import job_run

    as_of = _parse_date(args.date)
    settings, conn, repo, exchange, _finmind = _services()
    judge = create_judge(settings)
    with job_run(conn, "news", as_of) as run:
        result = run_news(settings, repo, exchange, exchange.http, judge, as_of, collect=not args.no_collect)
        run.note(**{k: v for k, v in result.items() if k != "flags"}, flagged=len(result["flags"]))
    print(f"消息面排雷：收集 {result.get('collected', 0)} 則、判讀 {result['judged']} 則"
          f"（{result['judged_by'] or '無'}），排除 {result['excluded']} 檔")
    for e in result.get("errors", []) + result.get("ai_errors", []):
        print(f"  注意：{e}")
    _print_flags(result["flags"])


def cmd_filters(args) -> None:
    from engine.filters.runner import run_filters
    from engine.joblog import job_run

    as_of = _parse_date(args.date)
    settings, conn, repo, exchange, finmind = _services()
    with job_run(conn, "filters", as_of) as run:
        result = run_filters(settings, repo, exchange, finmind, as_of, refresh=not args.no_refresh)
        run.note(**result)
    print(f"大盤燈號：{result['light_label']}（合計 {result['light_total']:+d}）")
    for item in result["light_inputs"].values():
        print(f"  {item['vote']:+d}  {item['label']}：{item['reason']}")
    print(f"族群：{result['groups_ranked']} 個排名，弱勢 {'、'.join(result['weak_groups']) or '無'}；"
          f"{result['blocked_by_group']} 檔屬弱勢族群")
    print(f"事件提示 {result['event_warnings']} 則；今日排除合計 {result['excluded_total']} 檔")
    for w in result["warnings"]:
        print(f"  注意：{w}")


def cmd_signals(args) -> None:
    from engine.db import connect
    from engine.joblog import job_run
    from engine.repository import Repository
    from engine.signals.runner import run_signals

    settings = load_settings()
    as_of = _parse_date(args.date)
    conn = connect()
    with job_run(conn, "signals", as_of) as run:
        result = run_signals(settings, Repository(conn), as_of, force=args.force)
        run.note(status=result["status"], reason=result["reason"], candidates=result.get("candidates", 0),
                 signals=len(result["signals"]), blocked=result["blocked"])
        if result["status"] in ("halted", "closed"):
            run.skip(result["reason"])
    if result["status"] == "closed":
        print(f"今日不發訊號：{result['reason']}")
        return
    if result["status"] == "halted":
        print(f"暫停發出訊號：{result['reason']}")
        raise SystemExit(2)
    if result["status"] == "forced":
        print(f"注意：{result['reason']}，以 --force 強制產生訊號")
    print(f"候選 {result['candidates']} 檔，買進訊號 {len(result['signals'])} 筆")
    for s in result["signals"]:
        sz = s["detail"]["sizing"]
        lots = f"{s['suggested_lots']} 張" if s["suggested_lots"] else (
            f"零股 {sz['odd_shares']} 股" if sz.get("odd_shares") else "0 張")
        print(f"  {s['stock_id']} {s['detail']['name'] or ''}  區間 {s['entry_low']:g}–{s['entry_high']:g}  "
              f"停損 {s['stop_loss']:g}  停利 {s['take_profit']:g}  建議 {lots}")
        print(f"    {s['reason']}")
        for w in s["detail"]["warnings"] + sz.get("notes", []):
            print(f"    提示：{w}")
    for b in result["blocked"]:
        print(f"  {b['stock_id']} 觸發{'、'.join(b['triggers'])}但未發訊號：{'、'.join(b['reasons'])}")


def cmd_notify(args) -> None:
    from engine.db import connect
    from engine.joblog import job_run
    from engine.notify.line import LineClient
    from engine.notify.runner import run_notify
    from engine.repository import Repository

    settings = load_settings()
    as_of = _parse_date(args.date)
    conn = connect()
    line = LineClient.from_env(settings.notify["line"])
    with job_run(conn, "notify", as_of) as run:
        result = run_notify(settings, Repository(conn), line, as_of, resend=args.resend)
        run.note(**{k: v for k, v in result.items() if k != "messages"}, message_count=len(result.get("messages", [])))
    for m in result.get("messages", []):
        print(m)
        print("─" * 20)
    if not line.configured:
        print("注意：未設定 LINE_CHANNEL_ACCESS_TOKEN／LINE_OWNER_USER_ID，以上內容沒有推播")
    for key in ("report_skipped", "skipped"):
        if result.get(key):
            print(result[key])
    if result["alerts"]:
        print("系統警告：" + "；".join(result["alerts"]))


def cmd_alert(args) -> None:
    """只送系統警告（排程任一步驟失敗時呼叫）。"""
    from engine.db import connect
    from engine.joblog import job_run
    from engine.notify.line import LineClient
    from engine.notify.runner import send_alerts
    from engine.repository import Repository

    settings = load_settings()
    as_of = _parse_date(args.date)
    conn = connect()
    line = LineClient.from_env(settings.notify["line"])
    with job_run(conn, "alert", as_of) as run:
        result = send_alerts(settings, Repository(conn), line, as_of)
        run.note(**result)
    print("系統警告：" + ("；".join(result["alerts"]) or "無"))


def cmd_holdings(args) -> None:
    from engine.db import connect
    from engine.joblog import job_run
    from engine.portfolio.monitor import run_holdings
    from engine.repository import Repository

    settings = load_settings()
    as_of = _parse_date(args.date)
    conn = connect()
    with job_run(conn, "holdings", as_of) as run:
        result = run_holdings(settings, Repository(conn), as_of)
        run.note(status=result["status"], checked=result["checked"], sells=len(result["sell_signals"]),
                 no_price=result.get("no_price", []))
        if result["status"] == "closed":
            run.skip("休市")
    if result["status"] == "closed":
        print("休市，不檢查持股")
        return
    print(f"檢查持股 {result['checked']} 檔，賣出提醒 {len(result['sell_signals'])} 筆")
    for s in result["sell_signals"]:
        print(f"  {s['stock_id']} {s['detail']['name'] or ''}：{s['reason']}")
    if result.get("no_price"):
        print(f"  當日無收盤價未檢查：{'、'.join(result['no_price'])}")


def _price(x) -> str:
    return "—" if x is None else f"{x:g}"


def cmd_trade(args) -> None:
    from engine.db import connect
    from engine.portfolio.trades import TradeError, record_trade
    from engine.repository import Repository

    settings = load_settings()
    conn = connect()
    repo = Repository(conn)
    if args.action == "list":
        for t in repo.trades():
            side = "買" if t["side"] == "buy" else "賣"
            print(f"  {t['date']} {side} {t['stock_id']} {t['shares']:,} 股 @ {t['price']:g}  "
                  f"手續費 {t['fee']:,.0f} 稅 {t['tax']:,.0f}  {t['note'] or ''}")
        print("目前持股：")
        for h in repo.holdings():
            print(f"  {h['stock_id']} {h['shares']:,} 股  成本 {h['avg_cost']:,.2f}  停損 {_price(h['stop_loss'])}  "
                  f"停利 {_price(h['take_profit'])}  自 {h['opened_date']}")
        return
    if args.stock is None or args.price is None or (args.shares is None and args.lots is None):
        raise SystemExit("用法：trade buy|sell 股票代號 價格 股數（或 --lots 張數）[--date YYYY-MM-DD]")
    shares = args.shares if args.shares is not None else args.lots * settings.risk["position"]["lot_size"]
    try:
        result = record_trade(settings, repo, args.stock, _parse_date(args.date), args.action, args.price, shares,
                              fee=args.fee, tax=args.tax, note=args.note)
    except TradeError as e:
        raise SystemExit(f"無法登錄：{e}") from e
    t, h = result["trade"], result["holding"]
    print(f"已登錄 {t['date']} {'買進' if t['side'] == 'buy' else '賣出'} {t['stock_id']} {t['shares']:,} 股 "
          f"@ {t['price']:g}（手續費 {t['fee']:,.0f}、證交稅 {t['tax']:,.0f}）")
    if t["signal_snapshot"]:
        print(f"  對應訊號：{t['signal_snapshot']['date']} {t['signal_snapshot']['reason']}")
    if h:
        print(f"  持股：{h['shares']:,} 股，平均成本 {h['avg_cost']:,.2f}，停損 {_price(h['stop_loss'])}，"
              f"停利 {_price(h['take_profit'])}")
    else:
        print("  已全部賣出")


def cmd_stats(args) -> None:
    from engine.db import connect
    from engine.portfolio.stats import BENCHMARK_LABELS, portfolio_stats
    from engine.repository import Repository

    as_of = _parse_date(args.date)
    st = portfolio_stats(Repository(connect()), as_of)

    def pct(x):
        return "—" if x is None else f"{x:+.2%}"

    if not st["start"]:
        print("尚無交易紀錄")
        return
    print(f"期間 {st['start']} 至 {st['end']}｜已實現交易 {st['closed_trades']} 筆（勝 {st['wins']}／敗 {st['losses']}）")
    win_rate = "—" if st["win_rate"] is None else f"{st['win_rate']:.1%}"
    print(f"勝率 {win_rate}｜平均獲利 {pct(st['avg_win_pct'])}｜平均虧損 {pct(st['avg_loss_pct'])}"
          f"｜期望值 {pct(st['expectancy_pct'])}")
    print(f"已實現損益 {st['realized_pnl']:,} 元｜未實現損益 {st['unrealized_pnl']:,} 元｜"
          f"平均持有 {st['avg_holding_days'] or '—'} 天")
    bench = "｜".join(f"{BENCHMARK_LABELS[k]} {pct(v)}" for k, v in st["benchmarks"].items())
    print(f"整體績效（佔總資金）{pct(st['portfolio_return'])}｜同期 {bench}")


def cmd_capital(args) -> None:
    from engine.db import connect
    from engine.repository import Repository

    repo = Repository(connect())
    if args.amount is not None:
        if args.amount <= 0:
            raise SystemExit("總資金必須大於 0")
        repo.set_setting("total_capital", args.amount)
        repo.conn.commit()
    print(f"總資金：{float(repo.setting('total_capital', 0)):,.0f} 元")


def cmd_monitor(args) -> None:
    from engine.db import connect
    from engine.monitor.quotes import create_quotes
    from engine.monitor.runner import Monitor, check_once
    from engine.notify.line import LineClient
    from engine.repository import Repository
    from engine.sources.common import HttpClient
    from engine.sources.exchanges import ExchangeClient

    settings = load_settings()
    http = HttpClient.from_settings(settings.sources["http"])
    quotes = create_quotes(settings, http)
    line = LineClient.from_env(settings.notify["line"])
    if not line.configured:
        print("注意：未設定 LINE 金鑰，停損提醒只會寫在記錄裡")
    if args.once:
        conn = connect()
        result = check_once(settings, Repository(conn), quotes, line, datetime.now(TZ))
        conn.close()
        print(f"檢查 {result['checked']} 檔持股，觸及停損：{'、'.join(result['alerts']) or '無'}")
        return
    Monitor(settings, connect, quotes, line, ExchangeClient(settings, http).holidays).run_forever()


def cmd_step(args) -> None:
    label = dict(PIPELINE_STEPS)[args.command]
    raise SystemExit(f"「{label}」尚未實作（{_parse_date(args.date)}）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="engine", description="台股自選股評分追蹤程式")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="顯示目前方案與功能開關").set_defaults(func=cmd_plan)
    sub.add_parser("migrate", help="套用資料庫 migration").set_defaults(func=cmd_migrate)
    sub.add_parser("companies", help="更新公司基本資料與產業別").set_defaults(func=cmd_companies)
    sub.add_parser("seed-groups", help="建立產業族群與自訂族群").set_defaults(func=cmd_seed_groups)

    p = sub.add_parser("fetch", help="抓取當日資料並做完整性檢查")
    p.add_argument("--date")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("backfill", help="回補自選股歷史資料（使用 FinMind 額度）")
    p.add_argument("--years", type=int, default=3)
    p.add_argument("stocks", nargs="*", help="股票代號，省略則為全部自選股")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("watch", help="管理自選股")
    p.add_argument("action", choices=["add", "remove", "list"])
    p.add_argument("stocks", nargs="*")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("probe", help="確認證交所／櫃買資料欄位")
    p.add_argument("--date")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("score", help="三面向評分")
    p.add_argument("--date")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("liquidity", help="流動性過濾（日均量、處置、注意、全額交割）")
    p.add_argument("--date")
    p.set_defaults(func=cmd_liquidity)

    p = sub.add_parser("news", help="消息面排雷（收集重大訊息、AI 判讀）")
    p.add_argument("--date")
    p.add_argument("--no-collect", action="store_true", help="不抓新資料，只判讀與更新排除名單")
    p.set_defaults(func=cmd_news)

    p = sub.add_parser("filters", help="大盤燈號、族群強弱、事件行事曆")
    p.add_argument("--date")
    p.add_argument("--no-refresh", action="store_true", help="不抓外部資料，只用資料庫既有資料重算")
    p.set_defaults(func=cmd_filters)

    p = sub.add_parser("signals", help="買進訊號、參考價位、風控張數")
    p.add_argument("--date")
    p.add_argument("--force", action="store_true", help="資料完整性檢查未通過時仍產生訊號（除錯用）")
    p.set_defaults(func=cmd_signals)

    p = sub.add_parser("notify", help="LINE 每日報告與系統警告")
    p.add_argument("--date")
    p.add_argument("--resend", action="store_true", help="今日報告已送出時仍重送")
    p.set_defaults(func=cmd_notify)

    p = sub.add_parser("alert", help="只檢查並推播系統警告")
    p.add_argument("--date")
    p.set_defaults(func=cmd_alert)

    p = sub.add_parser("holdings", help="每日持股檢查（停損、移動停利、分數、持有過久）")
    p.add_argument("--date")
    p.set_defaults(func=cmd_holdings)

    p = sub.add_parser("trade", help="登錄買賣或列出交易日誌")
    p.add_argument("action", choices=["buy", "sell", "list"])
    p.add_argument("stock", nargs="?")
    p.add_argument("price", nargs="?", type=float)
    p.add_argument("shares", nargs="?", type=int, help="股數（一張 = 1000 股）")
    p.add_argument("--lots", type=int, help="以張數登錄")
    p.add_argument("--date", help="成交日，預設今天")
    p.add_argument("--fee", type=float, help="手續費，省略則依 risk.yaml 計算")
    p.add_argument("--tax", type=float, help="證交稅，省略則依 risk.yaml 計算")
    p.add_argument("--note")
    p.set_defaults(func=cmd_trade)

    p = sub.add_parser("stats", help="交易日誌統計（勝率、期望值、與大盤及 0050 比較）")
    p.add_argument("--date")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("capital", help="查看或設定總資金（影響建議張數）")
    p.add_argument("amount", nargs="?", type=float)
    p.set_defaults(func=cmd_capital)

    p = sub.add_parser("monitor", help="盤中停損監控常駐程式（交易時間每隔數分鐘檢查持股）")
    p.add_argument("--once", action="store_true", help="立即檢查一次就結束（不論是否在交易時間）")
    p.set_defaults(func=cmd_monitor)

    for name, label in PIPELINE_STEPS:
        p = sub.add_parser(name, help=label)
        p.add_argument("--date", help="指定日期 YYYY-MM-DD，預設今天（台灣時間）")
        p.set_defaults(func=cmd_step)
    return parser


def main(argv: list[str] | None = None) -> None:
    from engine.config import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
