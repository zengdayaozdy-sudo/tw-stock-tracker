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
    # 記下報告裡的訊號，晚間補抓後比對差異
    out["reported_buys"] = _signal_keys(data["buy_signals"])
    out["reported_sells"] = _signal_keys(data["sell_signals"])
    return out


def _signal_keys(rows: list[dict]) -> list[str]:
    return sorted({f"{r['stock_id']}:{r['horizon']}" for r in rows})


def run_evening(settings: Settings, repo: Repository, line: LineClient, as_of: date,
                now: datetime | None = None) -> dict[str, Any]:
    """晚間補抓（融資融券、借券、晚間重大訊息）後重算的結果和下午報告比對，只推播有變動的部分。

    下午的報告沒送出（例如排程失敗）時，改送完整報告。
    """
    if not repo.report_sent(as_of):
        out = run_notify(settings, repo, line, as_of, now=now)
        out["mode"] = "full"
        return out
    out = send_alerts(settings, repo, line, as_of, now)
    out.update({"mode": "diff", "line_configured": line.configured, "update_sent": False})
    if not _prefs(repo)["daily_report"]:
        out["report_skipped"] = "每日報告通知已關閉"
        return out
    reported = repo.reported_signals(as_of)
    if reported is None:
        out["report_skipped"] = "找不到下午報告的訊號紀錄，無法比對"
        return out
    buys, sells = repo.signals_on(as_of, "buy"), repo.signals_on(as_of, "sell")
    blocked = {b["stock_id"]: b for b in ((repo.latest_job("signals", as_of) or ("", {}))[1].get("blocked") or [])}
    names = repo.stock_names(sorted({k.split(":")[0] for k in reported["buys"]}))
    message = evening_message(as_of, reported, buys, sells, blocked, names)
    out["changes"] = message is not None
    if message:
        out["messages"] = [message]
        out["update_sent"] = line.push_texts([message]) > 0
    return out


def evening_message(as_of: date, reported: dict[str, list[str]], buys: list[dict], sells: list[dict],
                    blocked: dict[str, dict], names: dict[str, str]) -> str | None:
    from engine.notify.report import HORIZON_LABELS, _num

    now_buys = {f"{s['stock_id']}:{s['horizon']}": s for s in buys}
    now_sells = {f"{s['stock_id']}:{s['horizon']}": s for s in sells}
    added = [now_buys[k] for k in sorted(set(now_buys) - set(reported["buys"]))]
    removed = sorted(set(reported["buys"]) - set(now_buys))
    new_sells = [now_sells[k] for k in sorted(set(now_sells) - set(reported["sells"]))]
    if not (added or removed or new_sells):
        return None
    lines = [f"【晚間更新】{as_of:%Y/%m/%d}", "融資融券、借券、晚間重大訊息更新後，訊號有變動："]
    if added:
        lines.append("")
        lines.append("新增買進訊號")
        for s in added:
            det = s["detail"] or {}
            total = (det.get("score") or {}).get("total", 0)
            lines.append(f"・{s['stock_id']} {det.get('name') or ''}｜{HORIZON_LABELS[s['horizon']]} {total:.0f} 分｜"
                         f"買進 {_num(s['entry_low'])}–{_num(s['entry_high'])}｜停損 {_num(s['stop_loss'])}")
    if removed:
        lines.append("")
        lines.append("取消買進訊號（明天先不要進場）")
        for k in removed:
            sid, horizon = k.split(":")
            why = "、".join((blocked.get(sid) or {}).get("reasons") or []) or "分數或進場條件不再符合"
            lines.append(f"・{sid} {names.get(sid) or ''}｜{HORIZON_LABELS.get(horizon, horizon)}｜{why}")
    if new_sells:
        lines.append("")
        lines.append("新增持股提醒")
        for s in new_sells:
            det = s["detail"] or {}
            lines.append(f"・{s['stock_id']} {det.get('name') or ''}｜{s.get('reason') or ''}")
    return "\n".join(lines)
