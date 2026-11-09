"""盤中停損監控常駐程式（第 10 節）。

交易日 09:00–13:30 每隔設定分鐘數檢查持股現價，觸及停損即推播 LINE；同一檔同一天只提醒一次
（紀錄在 job_logs，程式重啟也不會重複）。每次檢查成功寫入心跳，收盤後批次以心跳判斷是否停止。
斷線或資料庫錯誤時依序等待後重試，不會結束程式。每一輪重新連線資料庫（Neon 閒置會斷線）。
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Any

from engine.config import TZ, Settings
from engine.joblog import heartbeat
from engine.notify.alerts import MONITOR_JOB
from engine.portfolio.monitor import adjust_holding, effective_stop
from engine.repository import Repository

log = logging.getLogger(__name__)
ALERT_JOB = "intraday_alert"


def _clock(text: str) -> dtime:
    h, m = text.split(":")
    return dtime(int(h), int(m))


def previous_light(repo: Repository, today: date) -> str | None:
    """盤中使用前一個交易日收盤後算出的大盤燈號。"""
    with repo.conn.cursor() as cur:
        cur.execute("SELECT light FROM market WHERE date < %s ORDER BY date DESC LIMIT 1", (today,))
        row = cur.fetchone()
        return row[0] if row else None


def alerted_today(repo: Repository, today: date) -> set[str]:
    with repo.conn.cursor() as cur:
        cur.execute("SELECT detail->>'stock_id' FROM job_logs WHERE job = %s AND run_date = %s", (ALERT_JOB, today))
        return {r[0] for r in cur.fetchall()}


def record_alert(repo: Repository, today: date, detail: dict) -> None:
    with repo.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO job_logs (job, run_date, status, detail, finished_at) VALUES (%s, %s, 'success', %s, now())",
            (ALERT_JOB, today, json.dumps(detail, ensure_ascii=False, default=str)),
        )


def stop_message(hits: list[dict], now: datetime) -> str:
    lines = [f"【停損提醒】{now:%m/%d %H:%M}"]
    for h in hits:
        src = "" if h["source"] == "last" else "（無成交，以委買價計）"
        lines.append(f"・{h['stock_id']} {h['name'] or ''} 現價 {h['price']:g}{src} ≤ 停損 {h['stop']:g}"
                     f"｜成本 {h['avg_cost']:g}（{h['price'] / h['avg_cost'] - 1:+.1%}）｜{h['shares']:,} 股")
    lines.append("請自行判斷是否賣出")
    return "\n".join(lines)


def check_once(settings: Settings, repo: Repository, quotes, line, now: datetime) -> dict[str, Any]:
    today = now.date()
    holdings = [h for h in repo.holdings() if h["opened_date"] <= today]
    if not holdings:
        return {"checked": 0, "alerts": []}
    bearish = previous_light(repo, today) == "bearish"
    with repo.conn.cursor() as cur:
        cur.execute("SELECT stock_id, market, name FROM stocks WHERE stock_id = ANY(%s)",
                    ([h["stock_id"] for h in holdings],))
        meta = {s: (m, n) for s, m, n in cur.fetchall()}
    prices = quotes.fetch({h["stock_id"]: meta.get(h["stock_id"], ("twse", None))[0] for h in holdings})
    done = alerted_today(repo, today)
    adjustments = repo.adjustments([h["stock_id"] for h in holdings])
    hits: list[dict] = []
    for h in holdings:
        q = prices.get(h["stock_id"])
        h, _events = adjust_holding(h, adjustments.get(h["stock_id"]), today)  # 除權息後停損一併下調
        stop = effective_stop(h, bearish, settings.risk)
        if q is None or not stop or q.price > stop or h["stock_id"] in done:
            continue
        hits.append({"stock_id": h["stock_id"], "name": meta.get(h["stock_id"], (None, None))[1], "price": q.price,
                     "source": q.source, "stop": stop, "avg_cost": h["avg_cost"], "shares": h["shares"],
                     "bearish": bearish})
    out: dict[str, Any] = {"checked": len(holdings), "quoted": len(prices), "alerts": [h["stock_id"] for h in hits]}
    if not hits:
        return out
    prefs = {"intraday_stop_loss": True, **(repo.setting("notify", {}) or {})}
    if not prefs["intraday_stop_loss"]:
        out["skipped"] = "盤中停損通知已關閉"
        return out
    sent = line.push_texts([stop_message(hits, now)])
    for h in hits:
        # 未設定 LINE 時也記錄，避免同一天每 3 分鐘重複輸出
        record_alert(repo, today, {**h, "pushed": bool(sent)})
    repo.conn.commit()
    return out


class Monitor:
    """常駐迴圈。時間、睡眠、連線都可注入，方便測試。"""

    def __init__(self, settings: Settings, connect: Callable, quotes, line, holidays: Callable[[], set[date]],
                 clock: Callable[[], datetime] = lambda: datetime.now(TZ), sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.cfg = settings.notify["intraday"]
        self.connect = connect
        self.quotes = quotes
        self.line = line
        self.holidays_fn = holidays
        self.clock = clock
        self.sleep = sleep
        self._holidays: set[date] | None = None
        self._holiday_year: int | None = None
        self._errors = 0
        self._awake_until: datetime | None = None
        self._schedule_checked: date | None = None

    def is_trading_day(self, d: date) -> bool:
        if d.weekday() >= 5:
            return False
        if self._holidays is None or self._holiday_year != d.year:
            try:
                self._holidays, self._holiday_year = self.holidays_fn(), d.year
            except Exception as e:  # 休市日抓不到時只排除週末，下次再試
                log.warning("休市日抓取失敗：%s", e)
                return True
        return d not in self._holidays

    def next_wake(self, now: datetime) -> tuple[str, float]:
        """回傳 (狀態, 需要等待的秒數)。狀態：check 盤中檢查／schedule 檢查收盤後排程／wait 等待。"""
        start, end = _clock(self.cfg["start"]), _clock(self.cfg["end"])
        today = now.date()
        if self.is_trading_day(today) and start <= now.time() <= end:
            return "check", 0
        sched = self.cfg.get("schedule_check")
        if sched and self.is_trading_day(today) and self._schedule_checked != today and now.time() > end:
            target = datetime.combine(today, _clock(sched), tzinfo=TZ)
            if now >= target:
                return "schedule", 0
            return "wait", max(1.0, (target - now).total_seconds())
        d = now.date()
        if self.is_trading_day(d) and now.time() < start:
            target = datetime.combine(d, start, tzinfo=TZ)
        else:
            d += timedelta(days=1)
            while not self.is_trading_day(d):
                d += timedelta(days=1)
            target = datetime.combine(d, start, tzinfo=TZ)
        return "wait", max(1.0, (target - now).total_seconds())

    def tick(self) -> float:
        """執行一輪，回傳下一輪前要等待的秒數。"""
        now = self.clock()
        state, wait = self.next_wake(now)
        if state == "wait":
            return min(wait, 3600)  # 最多睡一小時就醒來重新判斷（避免睡眠中時鐘跳動）
        if state == "schedule":
            return self._check_schedule(now)
        self._keep_awake(now)
        conn = None
        try:
            conn = self.connect()
            repo = Repository(conn)
            result = check_once(self.settings, repo, self.quotes, self.line, now)
            heartbeat(conn, MONITOR_JOB, {"checked": result["checked"], "alerts": result["alerts"],
                                          "at": now.isoformat()})
            self._errors = 0
            if result["alerts"]:
                log.warning("停損提醒：%s", "、".join(result["alerts"]))
            return self.cfg["interval_minutes"] * 60
        except Exception as e:
            backoff = self.cfg["error_backoff_seconds"]
            wait = backoff[min(self._errors, len(backoff) - 1)]
            self._errors += 1
            log.error("盤中監控第 %d 次失敗，%d 秒後重試：%s", self._errors, wait, e)
            return wait
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _check_schedule(self, now: datetime) -> float:
        """收盤後排程（GitHub Actions）到了這個時間還沒有任何抓取紀錄 → 推播警告（同一天一次）。"""
        conn = None
        try:
            conn = self.connect()
            repo = Repository(conn)
            today = now.date()
            if repo.latest_job("fetch", today) is None and "SCHEDULE" not in alerted_today(repo, today):
                prefs = {"system_alerts": True, **(repo.setting("notify", {}) or {})}
                msg = (f"【系統警告】{today:%Y/%m/%d}\n・排程未執行：{self.cfg['schedule_check']} 仍沒有收盤後批次紀錄，"
                       "請到 GitHub Actions 確認每日排程是否啟用，或手動執行一次")
                sent = self.line.push_texts([msg]) if prefs["system_alerts"] else 0
                record_alert(repo, today, {"stock_id": "SCHEDULE", "pushed": bool(sent)})
                conn.commit()
                log.warning("收盤後排程未執行，已推播警告")
            self._schedule_checked = today
            return 60
        except Exception as e:
            log.error("檢查收盤後排程失敗：%s", e)
            return 300
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def run_forever(self) -> None:
        log.info("盤中停損監控啟動（每 %s 分鐘，%s–%s）", self.cfg["interval_minutes"], self.cfg["start"],
                 self.cfg["end"])
        while True:
            self.sleep(self.tick())

    def _keep_awake(self, now: datetime) -> None:
        """macOS：交易時間內用 caffeinate 阻止閒置睡眠（闔上螢幕仍可能睡眠）。"""
        if not self.cfg.get("keep_awake") or platform.system() != "Darwin" or not shutil.which("caffeinate"):
            return
        if self._awake_until and now < self._awake_until:
            return
        end = datetime.combine(now.date(), _clock(self.cfg["end"]), tzinfo=TZ) + timedelta(minutes=5)
        seconds = int((end - now).total_seconds())
        if seconds > 0:
            subprocess.Popen(["caffeinate", "-i", "-t", str(seconds)])
            self._awake_until = end
