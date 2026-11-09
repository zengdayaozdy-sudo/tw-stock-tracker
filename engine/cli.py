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
    import time

    from engine.joblog import job_run
    from engine.pipeline import fetch_daily

    trade_date = _parse_date(args.date)
    settings, conn, repo, exchange, finmind = _services()
    waited = 0
    with job_run(conn, "fetch", trade_date) as run:
        result = fetch_daily(settings, repo, exchange, finmind, trade_date)
        # 交易日但官方資料尚未更新：每 10 分鐘重試，最多等 --wait-minutes 分鐘
        while not result.market_open and waited < args.wait_minutes and _is_trading_day(exchange, trade_date):
            logging.info("官方資料尚未更新（最新 %s），10 分鐘後重試", result.counts.get("latest_available"))
            time.sleep(600)
            waited += 10
            result = fetch_daily(settings, repo, exchange, finmind, trade_date)
        run.note(**result.to_dict(), waited_minutes=waited)
        if not result.market_open:
            run.skip("休市" if not _is_trading_day(exchange, trade_date) else "官方資料尚未更新")
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.market_open and not result.quality.ok:
        # 交給後續步驟：資料不完整時暫停訊號並推播警告（第 6 階段接上 LINE）
        raise SystemExit(2)


def _is_trading_day(exchange, d: date) -> bool:
    if d.weekday() >= 5:
        return False
    try:
        return d not in exchange.holidays()
    except Exception:  # 休市日抓不到時只排除週末
        return True


def cmd_trading_day(args) -> None:
    """判斷是否為交易日；在 GitHub Actions 中寫入 open=true/false 供後續步驟判斷。"""
    import os

    from engine.sources.common import HttpClient
    from engine.sources.exchanges import ExchangeClient

    settings = load_settings()
    d = _parse_date(args.date)
    if getattr(args, "skip_if_done", False) and os.environ.get("DATABASE_URL"):
        # 備援排程：當天的每日報告已成功推播就不再重跑，避免重複推播
        from engine.db import connect
        from engine.repository import Repository

        conn = connect()
        done = Repository(conn).latest_job("notify", d)
        conn.close()
        if done and done[0] == "success":
            print(f"{d}：今日排程已完成，略過")
            out = os.environ.get("GITHUB_OUTPUT")
            if out:
                with open(out, "a", encoding="utf-8") as f:
                    f.write("open=false\n")
            return
    is_open = _is_trading_day(ExchangeClient(settings, HttpClient.from_settings(settings.sources["http"])), d)
    print(f"{d}：{'交易日' if is_open else '休市'}")
    if not is_open and os.environ.get("DATABASE_URL"):
        # 留下休市紀錄：系統警告以「前一個平日有沒有任何紀錄」判斷排程是否執行
        from engine.db import connect
        from engine.joblog import job_run

        conn = connect()
        with job_run(conn, "fetch", d) as run:
            run.skip("休市")
        conn.close()
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"open={'true' if is_open else 'false'}\n")


def cmd_holders(_args) -> None:
    """集保股權分散表（每週）：大戶持股比例寫入 chips.major_holder_pct。"""
    from engine.joblog import job_run
    from engine.sources.tdcc import parse_distribution

    settings, conn, repo, exchange, _finmind = _services()
    cfg = settings.sources["tdcc"]
    with job_run(conn, "holders", _parse_date(None)) as run:
        rows = parse_distribution(exchange.http.get_text(cfg["holding_distribution"]), cfg["major_holder_min_level"])
        known = repo.known_stock_ids()
        rows = [r for r in rows if r["stock_id"] in known]
        n = repo.upsert_chips(rows)
        run.note(rows=n, date=str(rows[0]["date"]) if rows else None)
    print(f"已更新 {n} 檔大戶持股比例（{rows[0]['date'] if rows else '無資料'}）")


def cmd_maintenance(args) -> None:
    """清理過舊的紀錄，控制 Neon 免費方案容量。"""
    from engine.db import connect

    conn = connect()
    rules = [
        ("盤中心跳", "DELETE FROM job_logs WHERE status = 'heartbeat' AND started_at < now() - interval '30 days'"),
        ("排程紀錄", "DELETE FROM job_logs WHERE status <> 'heartbeat' AND started_at < now() - make_interval(days => %s)"),
        ("API 用量", "DELETE FROM api_usage WHERE hour < now() - interval '90 days'"),
        ("新聞", "DELETE FROM news WHERE published_at < now() - make_interval(days => %s)"),
        ("濾網旗標", "DELETE FROM screen_flags WHERE date < current_date - make_interval(days => %s)"),
    ]
    params = {"排程紀錄": (args.keep_days,), "新聞": (args.keep_days * 2,), "濾網旗標": (args.keep_days,)}
    with conn.cursor() as cur:
        for label, sql in rules:
            cur.execute(sql, params.get(label))
            print(f"{label}：刪除 {cur.rowcount} 筆")
    conn.commit()


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


def cmd_probe_mops(args) -> None:
    """測試公開資訊觀測站重大訊息端點（新版 API 與舊版網頁），列出狀態與回應開頭。"""
    import httpx

    d = _parse_date(args.date)
    roc = d.year - 1911
    ua = {"User-Agent": "Mozilla/5.0 (tw-stock-tracker)", "Referer": "https://mops.twse.com.tw/mops/"}
    tries = [
        ("POST json", "https://mops.twse.com.tw/mops/api/t05sr01_1", {"json": {}}),
        ("POST json", "https://mops.twse.com.tw/mops/api/t05st02",
         {"json": {"year": str(roc), "month": f"{d.month:02d}", "day": f"{d.day:02d}"}}),
        ("POST json", "https://mops.twse.com.tw/mops/api/t05st02",
         {"json": {"year": str(roc), "month": str(d.month), "day": str(d.day)}}),
        ("POST form", "https://mopsov.twse.com.tw/mops/web/ajax_t05st02",
         {"data": {"encodeURIComponent": "1", "step": "1", "step00": "0", "firstin": "1", "off": "1",
                   "TYPEK": "all", "year": str(roc), "month": f"{d.month:02d}", "day": f"{d.day:02d}"}}),
        ("GET", "https://mopsov.twse.com.tw/mops/web/t05sr01_1", {}),
    ]
    with httpx.Client(timeout=30, headers=ua, follow_redirects=True) as c:
        for method, url, kw in tries:
            try:
                r = c.post(url, **kw) if method.startswith("POST") else c.get(url)
                body = r.text
                print(f"\n[{method}] {url} {kw.get('json') or kw.get('data') or ''}")
                print(f"  status={r.status_code} type={r.headers.get('content-type')} len={len(body)}")
                print("  head:", " ".join(body[:1500].split()))
            except Exception as e:
                print(f"\n[{method}] {url} 失敗：{e}")
        try:
            r = c.post("https://mops.twse.com.tw/mops/api/t05st02",
                       json={"year": str(roc), "month": f"{d.month:02d}", "day": f"{d.day:02d}"})
            rows = r.json()["result"]["data"]
            kinds: dict[str, int] = {}
            for row in rows:
                k = row[-1]["parameters"].get("marketKind")
                kinds[k] = kinds.get(k, 0) + 1
            print(f"\n[t05st02] 筆數={len(rows)} marketKind={kinds} keys={list(r.json()['result'].keys())}")
            params = rows[1][-1]["parameters"]
            r = c.post("https://mops.twse.com.tw/mops/api/t05st02_detail", json=params)
            print(f"[detail] {params} status={r.status_code}")
            print("  head:", " ".join(r.text[:2500].split()))
        except Exception as e:
            print(f"[detail] 失敗：{e}")


def cmd_probe_url(args) -> None:
    """實測任意公開資料網址：列出狀態、筆數、欄位與第一筆；--grep 只列出含關鍵字的 swagger 路徑。"""
    import json as _json

    import httpx

    with httpx.Client(timeout=40, headers={"User-Agent": "Mozilla/5.0 (tw-stock-tracker)"}, follow_redirects=True) as c:
        for url in args.urls:
            try:
                r = c.post(url, json=_json.loads(args.json)) if args.json else c.get(url)
            except Exception as e:
                print(f"\n[{url}] 失敗：{e}")
                continue
            print(f"\n[{url}] status={r.status_code} type={r.headers.get('content-type')} len={len(r.text)}")
            try:
                data = r.json()
            except ValueError:
                print("  head:", " ".join(r.text[:800].split()))
                continue
            if args.grep and isinstance(data, dict) and "paths" in data:
                words = args.grep.split(",")
                for path, ops in data["paths"].items():
                    text = _json.dumps(ops, ensure_ascii=False)
                    if any(w in text or w in path for w in words):
                        summary = next(iter(ops.values())).get("summary", "")
                        print(f"  {path}  {summary}")
                continue
            rows = data if isinstance(data, list) else data.get("data") or data.get("tables") or data
            if isinstance(rows, list):
                print(f"  筆數={len(rows)}")
                if rows:
                    print("  first:", _json.dumps(rows[0], ensure_ascii=False)[:900])
            if isinstance(data, dict):
                print("  keys:", list(data.keys())[:20])
                for k in ("fields", "stat", "title", "date"):
                    if k in data:
                        print(f"  {k}:", _json.dumps(data[k], ensure_ascii=False)[:900])
                if isinstance(data.get("tables"), list):
                    for t in data["tables"][:3]:
                        print("  table:", t.get("title"), _json.dumps(t.get("fields"), ensure_ascii=False)[:600])
                        print("    first:", _json.dumps((t.get("data") or [None])[0], ensure_ascii=False)[:600])


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
                if isinstance(payload, dict) and isinstance(payload.get("tables"), list):
                    for t in payload["tables"]:
                        if t.get("data"):
                            print(f"\n[{market}.{key}] 表格「{t.get('title')}」筆數={len(t['data'])}")
                            print("  fields:", t.get("fields"))
                            print("  sample:", json.dumps(t["data"][0], ensure_ascii=False)[:300])
                    continue
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
        result = send_alerts(settings, Repository(conn), line, as_of, extra=args.message)
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


def runtime_config(settings) -> dict:
    """網站要用的設定快照（方案、鎖定功能、費用與風控規則），由每日排程寫入 settings。"""
    fm = settings.sources["finmind"]
    return {
        "plan": settings.plan.name,
        "plan_label": settings.plan.label,
        "universe": settings.plan.universe,
        "watchlist_limit": settings.plan.watchlist_limit,
        "features": {k: {"label": v, "enabled": settings.enabled(k)} for k, v in settings.all_features.items()},
        "risk": {k: settings.risk[k] for k in ("costs", "stop_loss", "take_profit", "exit", "position")},
        "finmind_hourly_budget": int(fm["hourly_limit"][settings.plan.name] * fm.get("safety_margin", 0.9)),
        "min_history_days": settings.scoring["min_history_days"],
        "min_peers": settings.scoring["min_peers"],
        "updated_at": datetime.now(TZ).isoformat(),
    }


def cmd_sync_config(_args) -> None:
    from engine.db import connect
    from engine.repository import Repository

    repo = Repository(connect())
    repo.set_setting("runtime_config", runtime_config(load_settings()))
    repo.conn.commit()
    print("已同步網站設定快照")


def cmd_backfill_new(args) -> None:
    """自選股或持股中歷史不足者自動回補（網站新增自選股後由每日排程補齊）。"""
    from engine.joblog import job_run
    from engine.pipeline import BACKFILL_CALLS_PER_STOCK, backfill, backfill_capacity, universe

    settings, conn, repo, _exchange, finmind = _services()
    ids = universe(settings, repo)
    need = settings.scoring["min_history_days"]
    with conn.cursor() as cur:
        cur.execute("SELECT stock_id, count(*) FROM daily_prices WHERE stock_id = ANY(%s) GROUP BY 1", (ids,))
        counts = dict(cur.fetchall())
    pending = [s for s in ids if counts.get(s, 0) < need]
    if not pending:
        print("沒有需要回補的股票")
        return
    if args.limit:
        limit = args.limit
    else:
        # 自動：用掉本小時剩餘額度（上限見 sources.yaml backfill_new），新股不必一天只補 10 檔
        cfg = settings.sources["backfill_new"]
        remaining = finmind.quota.budget - finmind.quota.calls_this_hour
        limit = backfill_capacity(remaining, cfg["max_per_run"], cfg["min_per_run"])
        print(f"本小時剩餘 FinMind 額度 {remaining} 次（每檔約 {BACKFILL_CALLS_PER_STOCK} 次），本次回補 {limit} 檔")
    todo = pending[:limit]
    if not todo:
        print(f"額度不足，{len(pending)} 檔待下次排程回補")
        return
    with job_run(conn, "backfill", _parse_date(None)) as run:
        result = backfill(repo, finmind, todo, args.years, _parse_date(None))
        run.note(stocks=todo, years=args.years, counts=result, auto=True)
    print(f"已回補 {'、'.join(todo)}：{json.dumps(result, ensure_ascii=False)}")


def cmd_backtest(args) -> None:
    from pathlib import Path

    from engine.backtest.runner import BacktestError, parse_sets, run_backtest
    from engine.db import connect
    from engine.repository import Repository

    settings = load_settings()
    period = "holdout" if args.holdout else "full" if args.full else "tune"
    try:
        result = run_backtest(settings, Repository(connect()), period, _parse_date(args.end) if args.end else None,
                              overrides=parse_sets(args.set), force=args.force, save=not args.no_save)
    except BacktestError as e:
        raise SystemExit(f"無法回測：{e}") from e
    print(result["report"])
    out = Path(args.out) if args.out else Path("reports") / f"backtest-{period}-{result['fingerprint']}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result["report"], encoding="utf-8")
    print(f"報告：{out}" + (f"（run {result['run_id']}）" if result["run_id"] else ""))


def cmd_backtest_data(args) -> None:
    from engine.backtest.runner import refresh_history

    settings, conn, repo, exchange, finmind = _services()
    counts = refresh_history(settings, repo, exchange, finmind, args.years, _parse_date(None))
    print("已更新大盤歷史：" + "、".join(f"{k} {v}" for k, v in counts.items()))


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
    p.add_argument("--wait-minutes", type=int, default=0, help="交易日但官方資料尚未更新時，最多等待幾分鐘")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("backfill", help="回補自選股歷史資料（使用 FinMind 額度）")
    p.add_argument("--years", type=int, default=5)
    p.add_argument("stocks", nargs="*", help="股票代號，省略則為全部自選股")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("watch", help="管理自選股")
    p.add_argument("action", choices=["add", "remove", "list"])
    p.add_argument("stocks", nargs="*")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("probe", help="確認證交所／櫃買資料欄位")
    p.add_argument("--date")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("probe-url", help="實測任意公開資料網址")
    p.add_argument("urls", nargs="+")
    p.add_argument("--json", help="改用 POST 並送出這段 JSON")
    p.add_argument("--grep", help="swagger 路徑關鍵字，逗號分隔")
    p.set_defaults(func=cmd_probe_url)

    p = sub.add_parser("probe-mops", help="測試公開資訊觀測站重大訊息端點")
    p.add_argument("--date")
    p.set_defaults(func=cmd_probe_mops)

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
    p.add_argument("--message", help="額外加在警告最前面的訊息（例如每週維護失敗）")
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

    sub.add_parser("sync-config", help="把方案與風控設定寫入資料庫供網站使用").set_defaults(func=cmd_sync_config)

    p = sub.add_parser("backfill-new", help="自動回補歷史不足的自選股與持股")
    # FinMind 一次查詢回傳整段期間，5 年與 3 年呼叫次數相同；回測需要 240 日股價＋3 年自身歷史
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--limit", type=int, default=0, help="每次最多回補幾檔；0＝依本小時剩餘 FinMind 額度自動決定")
    p.set_defaults(func=cmd_backfill_new)

    p = sub.add_parser("backtest", help="回測（預設調參區間；--holdout 保留驗證區間只能跑一次）")
    p.add_argument("--holdout", action="store_true", help="執行保留驗證區間（只允許一次）")
    p.add_argument("--full", action="store_true", help="全期間（視同已使用保留區間）")
    p.add_argument("--force", action="store_true", help="保留區間已驗證過仍強制重跑")
    p.add_argument("--set", action="append", metavar="路徑=值", help="覆寫參數，例如 risk.stop_loss.percent=0.06")
    p.add_argument("--end", help="回測迄日，預設為最新加權指數日期")
    p.add_argument("--out", help="報告輸出路徑（Markdown）")
    p.add_argument("--no-save", action="store_true", help="不寫入 backtest_runs")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("backtest-data", help="抓取回測所需的大盤長期歷史")
    p.add_argument("--years", type=int, default=5)
    p.set_defaults(func=cmd_backtest_data)

    p = sub.add_parser("trading-day", help="判斷是否為交易日（排程用）")
    p.add_argument("--date")
    p.add_argument("--skip-if-done", action="store_true", help="當天每日報告已推播成功就略過（備援排程用）")
    p.set_defaults(func=cmd_trading_day)

    sub.add_parser("holders", help="集保股權分散表：大戶持股比例（每週）").set_defaults(func=cmd_holders)

    p = sub.add_parser("maintenance", help="清理過舊的排程紀錄、心跳、新聞")
    p.add_argument("--keep-days", type=int, default=180)
    p.set_defaults(func=cmd_maintenance)

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
