"""系統警告（第 9 節）：資料抓取失敗、完整性檢查未過、排程未執行、FinMind 額度用盡、盤中監控停止。

只讀 job_logs 與 api_usage，所以任何步驟失敗後都能在最後補送警告。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from engine.config import TZ, Settings
from engine.repository import Repository

STEP_LABELS = {
    "fetch": "資料抓取", "liquidity": "流動性過濾", "news": "消息面排雷", "score": "評分",
    "filters": "濾網", "signals": "買進訊號", "notify": "LINE 報告", "backfill": "歷史回補",
}
MONITOR_JOB = "monitor_heartbeat"
MARKET_CLOSE = time(13, 30)


def previous_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def collect_alerts(settings: Settings, repo: Repository, as_of: date, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(TZ)
    alerts: list[str] = []
    jobs = {j["job"]: j for j in repo.jobs_on(as_of)}
    fetch = jobs.get("fetch")
    market_open = True

    if fetch is None:
        alerts.append(f"排程未執行：{as_of} 尚未抓取資料")
    elif fetch["status"] == "skipped":
        market_open = False
    elif fetch["status"] == "failed":
        alerts.append(f"資料抓取失敗：{str(fetch['detail'].get('error', ''))[:200]}")
    elif fetch["status"] == "running":
        alerts.append("資料抓取未完成（可能逾時或中斷）")
    else:
        quality = fetch["detail"].get("quality") or {}
        if quality.get("ok") is False:
            errors = "；".join((quality.get("errors") or [])[:5])
            alerts.append(f"資料完整性檢查未通過，今日暫停訊號：{errors}")

    for name, job in sorted(jobs.items()):
        if name == "fetch" or name not in STEP_LABELS:
            continue
        if job["status"] == "failed":
            alerts.append(f"{STEP_LABELS[name]}步驟失敗：{str(job['detail'].get('error', ''))[:200]}")

    # 前一個平日完全沒有執行紀錄＝排程沒跑（休市日也會留下 skipped 紀錄）
    prev = previous_weekday(as_of)
    if not repo.jobs_on(prev):
        alerts.append(f"排程未執行：前一個交易日 {prev} 沒有任何執行紀錄")

    # FinMind 額度：某小時用量達到預算上限（程式會暫停等待，但代表額度吃緊）
    fm = settings.sources["finmind"]
    budget = int(fm["hourly_limit"][settings.plan.name] * fm.get("safety_margin", 0.9))
    day_start = datetime.combine(as_of, time(0), tzinfo=TZ)
    for hour, calls in repo.api_usage_on("finmind", day_start, day_start + timedelta(days=1)):
        if calls >= budget:
            alerts.append(f"FinMind 額度用盡：{hour.astimezone(TZ):%H} 時已用 {calls} 次（上限 {budget}），已暫停等待")

    # 盤中停損監控心跳（第 8 階段啟用後才檢查）
    last = repo.last_heartbeat(MONITOR_JOB)
    if market_open and last is not None and now.date() == as_of and now.time() >= MARKET_CLOSE:
        max_age = timedelta(minutes=settings.notify["alerts"]["heartbeat_max_age_minutes"])
        close_at = datetime.combine(as_of, MARKET_CLOSE, tzinfo=TZ)
        if last < close_at - max_age:
            alerts.append(f"盤中停損監控可能已停止：最後心跳 {last.astimezone(TZ):%m/%d %H:%M}")
    return alerts


def alert_message(as_of: date, alerts: list[str]) -> str:
    return "\n".join([f"【系統警告】{as_of:%Y/%m/%d}", *(f"・{a}" for a in alerts)])
