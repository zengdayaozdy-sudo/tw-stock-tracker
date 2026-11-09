"""LINE 每日報告與系統警告步驟（第 4 節步驟 9）。

同一天的報告只送一次（--resend 可重送）；同一則警告同一天不重複推播。
未設定 LINE 金鑰時只輸出內容，不算已送出，設定後重跑即可補送。
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

from engine.config import Settings
from engine.notify.alerts import alert_message, collect_alerts
from engine.notify.line import LineClient
from engine.notify.report import render_messages, report_data
from engine.repository import Repository

DEFAULT_PREFS = {"daily_report": True, "intraday_stop_loss": True, "system_alerts": True}


def _prefs(repo: Repository) -> dict[str, bool]:
    return {**DEFAULT_PREFS, **(repo.setting("notify", {}) or {})}


def send_alerts(settings: Settings, repo: Repository, line: LineClient, as_of: date,
                now: datetime | None = None, extra: str | None = None) -> dict[str, Any]:
    alerts = collect_alerts(settings, repo, as_of, now)
    if extra:
        alerts.insert(0, extra)
    new = [a for a in alerts if a not in repo.alerts_sent(as_of)]
    out: dict[str, Any] = {"alerts": alerts, "alerts_sent": []}
    if not new:
        return out
    if not _prefs(repo)["system_alerts"]:
        out["skipped"] = "系統警告通知已關閉"
        return out
    if line.push_texts([alert_message(as_of, new)]):
        out["alerts_sent"] = new
    return out


def run_notify(settings: Settings, repo: Repository, line: LineClient, as_of: date, resend: bool = False,
               now: datetime | None = None) -> dict[str, Any]:
    out = send_alerts(settings, repo, line, as_of, now)
    out["line_configured"] = line.configured
    out["report_sent"] = False
    if not _prefs(repo)["daily_report"]:
        out["report_skipped"] = "每日報告通知已關閉"
        return out
    fetch = repo.latest_job("fetch", as_of)
    if fetch and fetch[0] == "skipped" and not resend:
        out["report_skipped"] = "休市，不送每日報告"
        return out
    if repo.report_sent(as_of) and not resend:
        out["report_skipped"] = "今日報告已送出（加 --resend 重送）"
        return out
    data = report_data(settings, repo, as_of)
    messages = render_messages(data, settings.notify, os.environ.get("SITE_URL") or None)
    out["messages"] = messages
    out["report_sent"] = line.push_texts(messages) > 0
    return out
