"""盤勢總結與隔日計畫的文字版（LINE、CLI 共用；網站讀同一份 daily_summaries）。"""

from __future__ import annotations

from typing import Any


def _num(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x:,.2f}".rstrip("0").rstrip(".") if abs(x) < 1000 else f"{x:,.0f}"


def _yi(x: float) -> str:
    return f"{x / 1e8:+,.1f} 億"


def summary_block(s: dict[str, Any], compact: bool = False) -> str:
    f = s.get("facts") or {}
    lines = ["【盤勢總結】", s.get("narrative") or ""]
    if compact:
        return "\n".join(lines)
    data = []
    b = f.get("breadth")
    if b:
        limits = (f"／漲停 {b['limit_up']}" if b["limit_up"] else "") + (
            f"／跌停 {b['limit_down']}" if b["limit_down"] else "")
        data.append(f"漲跌家數：上漲 {b['advancers']:,}／下跌 {b['decliners']:,}{limits}")
    t = f.get("turnover")
    if t:
        ratio = f"（{t['avg_days']} 日均量 {t['ratio']:.2f} 倍）" if t.get("ratio") else ""
        data.append(f"上市成交值 {t['value'] / 1e8:,.0f} 億{ratio}")
    fl = f.get("flows")
    if fl:
        streak = fl.get("foreign_streak", 0)
        side = "賣" if fl["foreign"] < 0 else "買"
        data.append(f"法人（上市估算）：外資 {_yi(fl['foreign'])}" + (f"（連 {streak} {side}）" if streak >= 2 else "")
                    + f"、投信 {_yi(fl['trust'])}、自營 {_yi(fl['dealer'])}")
    if data:
        lines.append("")
        lines += [f"・{x}" for x in data]
    other = [r for r in s.get("reasons") or [] if r["key"] not in ("news",)]
    if other:
        lines.append("")
        lines.append("原因拆解：")
        lines += [f"{i}. {r['text']}" for i, r in enumerate(other[:6], start=1)]
    news = f.get("news") or []
    if news:
        lines.append("")
        lines.append("相關新聞：")
        lines += [f"・{n['title']}（{n['source']}）" for n in news[:4]]
    return "\n".join(lines)


def plan_block(p: dict[str, Any], compact: bool = False) -> str:
    pos = p.get("posture") or {}
    lines = ["【隔日操作計畫】", f"方針：{pos.get('title', '—')}｜{pos.get('text', '')}"]
    lv = p.get("levels") or {}
    if lv.get("resistance") or lv.get("support"):
        res = "、".join(f"{x['label']} {_num(x['value'])}" for x in lv.get("resistance") or []) or "—"
        sup = "、".join(f"{x['label']} {_num(x['value'])}" for x in lv.get("support") or []) or "—"
        lines.append(f"壓力：{res}")
        lines.append(f"支撐：{sup}")
    if not compact:
        for sc in p.get("scenarios") or []:
            lines.append(f"・{sc['case']}｜{sc['when']}：{sc['action']}")
    holds = p.get("holdings") or []
    if holds:
        lines.append("")
        lines.append("持股：")
        for h in holds:
            if compact and h["level"] == "hold":
                continue
            gain = f"（{h['gain']:+.1%}）" if h.get("gain") is not None else ""
            lines.append(f"・{h['stock_id']} {h.get('name') or ''} {_num(h.get('close'))}{gain}：{h['action']}")
        if compact and all(h["level"] == "hold" for h in holds):
            lines.append("・全部續抱，依停損價執行")
    entries = p.get("entries") or []
    if entries:
        lines.append("")
        lines.append("進場名單：")
        tag = {"buy": "買進", "setup": "醞釀", "watch": "觀察"}
        for e in entries[: 4 if compact else 8]:
            lines.append(f"・[{tag[e['kind']]}] {e['stock_id']} {e.get('name') or ''}：{e['action']}")
        if len(entries) > (4 if compact else 8):
            lines.append(f"・另有 {len(entries) - (4 if compact else 8)} 檔，詳見網站")
    if p.get("events"):
        lines.append("")
        lines.append("事件：" + "；".join(p["events"][:5]))
    if p.get("watch") and not compact:
        lines.append("")
        lines.append("觀察重點：" + "；".join(p["watch"]))
    return "\n".join(lines)
