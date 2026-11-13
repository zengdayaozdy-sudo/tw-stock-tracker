"""隔日操作計畫：盤勢方針、關鍵價位、三種情境、持股處理、進場名單、事件提醒。

全部由既有的燈號、反彈偵測、訊號、持股提醒與價位推導，不另外產生新的買賣訊號；
買賣仍由擁有者自行判斷。
"""

from __future__ import annotations

from typing import Any

from engine.signals.regime import MODE_LABELS

SELL_ACTIONS = {
    # 觸發條件 → （優先順序, 隔日動作）
    "stop_loss": (1, "已跌破停損，明日依紀律出場，不攤平"),
    "trailing_stop": (2, "自高點回落觸發移動停利，明日出場或至少減碼一半"),
    "stale": (3, "持有過久未上漲，明日考慮換股"),
    "score_drop": (4, "分數跌出門檻，明日考慮減碼或換股"),
}


def _at(verb: str, label: str) -> str:
    """「跌破 10 日線」「跌破今日低點」：中文接數字時空一格。"""
    return f"{verb} {label}" if label[:1].isdigit() else f"{verb}{label}"


def _num(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x:,.2f}".rstrip("0").rstrip(".") if abs(x) < 1000 else f"{x:,.0f}"


def posture(light: str | None, regime: dict | None, rebound: dict | None) -> dict[str, str]:
    mode = (regime or {}).get("mode")
    state = (rebound or {}).get("state")
    rb_label = (rebound or {}).get("label") or ""
    factor = (regime or {}).get("size_factor")
    if light is None:
        return {"tone": "neutral", "title": "大盤燈號尚未計算", "text": "先不開新倉，等燈號與訊號產生後再判斷"}
    if mode == "blocked":
        if (regime or {}).get("watch") or state in ("attempt", "confirmed"):
            return {"tone": "bearish", "title": f"偏空、{rb_label}尚未確認",
                    "text": "不開新倉；觀察名單等收盤站上轉強價、反彈確認後才成立；持股反彈減碼、嚴守停損"}
        return {"tone": "bearish", "title": "偏空", "text": "不開新倉，持股逢反彈降低部位，跌破停損一律出場"}
    if mode in ("rebound_confirmed", "rebound_attempt"):
        return {"tone": "neutral", "title": f"偏空但{rb_label}",
                "text": f"只做{MODE_LABELS[mode]}，部位 ×{factor:g}、停損收緊，買在建議區間內，不追高"}
    if light == "bullish":
        return {"tone": "bullish", "title": "偏多", "text": "依買進訊號分批進場，部位依建議張數，拉回到區間再買"}
    return {"tone": "neutral", "title": "中性", "text": "只做高分買進訊號，單筆部位依建議張數，避免追高"}


def key_levels(index: dict[str, Any], mas: list[dict], n: int) -> dict[str, list[dict]]:
    """收盤上方最近的壓力、下方最近的支撐（當日高低點與各均線）。"""
    close = index["close"]
    cands = [{"label": f"{m['days']} 日線", "value": m["value"]} for m in mas]
    if index.get("high"):
        cands.append({"label": "今日高點", "value": index["high"]})
    if index.get("low"):
        cands.append({"label": "今日低點", "value": index["low"]})
    res = sorted((c for c in cands if c["value"] > close), key=lambda c: c["value"])
    sup = sorted((c for c in cands if c["value"] < close), key=lambda c: -c["value"])

    def tag(c: dict) -> dict:
        return {**c, "value": round(c["value"], 2), "distance": round(c["value"] - close, 2),
                "pct": round(c["value"] / close - 1, 4)}

    return {"resistance": [tag(c) for c in _dedupe(res)[:n]], "support": [tag(c) for c in _dedupe(sup)[:n]]}


def _dedupe(levels: list[dict], tol: float = 0.002) -> list[dict]:
    """相距 0.2% 內的價位合併成一個（標籤串起來）。"""
    out: list[dict] = []
    for c in levels:
        if out and abs(c["value"] / out[-1]["value"] - 1) <= tol:
            out[-1] = {**out[-1], "label": f"{out[-1]['label']}／{c['label']}"}
            continue
        out.append(dict(c))
    return out


def scenarios(levels: dict[str, list[dict]], pos: dict[str, str], has_entries: bool) -> list[dict[str, str]]:
    res, sup = levels["resistance"], levels["support"]
    out: list[dict[str, str]] = []
    bearish = pos["tone"] == "bearish"
    if res:
        r = res[0]
        act = ("反彈延續，但燈號仍偏空：只看觀察名單是否站上轉強價，不追價" if bearish
               else "多方延續，可依買進訊號在區間內分批" if has_entries else "多方延續，等新訊號，不追價")
        out.append({"case": "強", "when": f"{_at('站上', r['label'])} {_num(r['value'])}", "action": act})
    if res and sup:
        out.append({"case": "盤整", "when": f"在 {_num(sup[0]['value'])}～{_num(res[0]['value'])} 之間",
                    "action": "區間整理，不追高、不殺低，持股照停損價執行"})
    if sup:
        s = sup[0]
        nxt = f"，下一個支撐 {sup[1]['label']} {_num(sup[1]['value'])}" if len(sup) > 1 else ""
        out.append({"case": "弱", "when": f"{_at('跌破', s['label'])} {_num(s['value'])}",
                    "action": f"弱勢延續{nxt}；持股跌破停損即出場，不攤平、不搶反彈"})
    return out


def holding_actions(holdings: list[dict], sells: list[dict], cfg: dict) -> list[dict[str, Any]]:
    by_stock: dict[str, list[dict]] = {}
    for s in sells:
        by_stock.setdefault(s["stock_id"], []).append(s)
    out = []
    for h in holdings:
        close, cost, stop = h.get("close"), h.get("avg_cost"), h.get("stop_loss")
        gain = close / cost - 1 if close and cost else None
        item: dict[str, Any] = {"stock_id": h["stock_id"], "name": h.get("name"), "close": close, "gain": gain,
                                "stop_loss": stop, "shares": h.get("shares")}
        hits = by_stock.get(h["stock_id"])
        if hits:
            triggers = sorted({t for s in hits for t in (s.get("triggers") or [])},
                              key=lambda t: SELL_ACTIONS.get(t, (9, ""))[0])
            main = triggers[0] if triggers else None
            item.update({"level": "sell", "triggers": triggers,
                         "action": SELL_ACTIONS.get(main, (9, "依賣出提醒處理"))[1],
                         "reason": "；".join(s.get("reason") or "" for s in hits)})
            # 移動停利的停損價在提醒裡（持股表的停損價可能是舊的）
            stops = [s.get("stop_loss") for s in hits if s.get("stop_loss")]
            if stops:
                item["stop_loss"] = max(stops)
        elif close and stop:
            dist = close / stop - 1
            item["stop_distance"] = round(dist, 4)
            if dist <= cfg["stop_watch_pct"]:
                item.update({"level": "watch", "action": f"接近停損 {_num(stop)}（距 {dist:.1%}），盤中跌破即出場"})
            else:
                item.update({"level": "hold", "action": f"續抱，停損 {_num(stop)}（距 {dist:.1%}）"})
        else:
            item.update({"level": "hold", "action": "尚未設定停損，請先設定"})
        out.append(item)
    order = {"sell": 0, "watch": 1, "hold": 2}
    out.sort(key=lambda x: (order[x["level"]], x["stock_id"]))
    return out


def entry_candidates(buys: list[dict], blocked: list[dict], setups: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in buys:
        det = s.get("detail") or {}
        lots = s.get("suggested_lots") or 0
        out.append({"kind": "buy", "stock_id": s["stock_id"], "name": det.get("name"), "horizon": s.get("horizon"),
                    "entry_low": s.get("entry_low"), "entry_high": s.get("entry_high"), "stop_loss": s.get("stop_loss"),
                    "take_profit": s.get("take_profit"), "lots": lots,
                    "action": f"買進區間 {_num(s.get('entry_low'))}～{_num(s.get('entry_high'))} 內分批，"
                              f"停損 {_num(s.get('stop_loss'))}" + (f"，建議 {lots} 張" if lots else "")})
    for x in setups:
        out.append({"kind": "setup", "stock_id": x["stock_id"], "name": x.get("name"),
                    "trigger_price": x.get("trigger_price"),
                    "entry_low": x.get("entry_low"), "entry_high": x.get("entry_high"), "stop_loss": x.get("stop_loss"),
                    "take_profit": x.get("take_profit"),
                    "action": f"收盤站上轉強價 {_num(x.get('trigger_price'))} 才成立；成立後買進 "
                              f"{_num(x.get('entry_low'))}～{_num(x.get('entry_high'))}，停損 {_num(x.get('stop_loss'))}"})
    seen = {c["stock_id"] for c in out}
    for b in blocked:
        if not b.get("entry_high") or b["stock_id"] in seen:
            continue
        out.append({"kind": "watch", "stock_id": b["stock_id"], "name": b.get("name"),
                    "entry_low": b.get("entry_low"), "entry_high": b.get("entry_high"), "stop_loss": b.get("stop_loss"),
                    "take_profit": b.get("take_profit"),
                    "action": f"已觸發{'、'.join(b.get('triggers') or [])}，等反彈確認；成立時買進 "
                              f"{_num(b.get('entry_low'))}～{_num(b.get('entry_high'))}，停損 {_num(b.get('stop_loss'))}",
                    "blocked_by": b.get("reasons") or []})
    return out


def event_notes(flags: dict[str, list[dict]], focus: set[str], names: dict[str, str]) -> list[str]:
    """持股與進場名單的事件提醒（除權息、財報、法說會、MSCI 調整）。"""
    out = []
    for sid in sorted(focus):
        for f in flags.get(sid) or []:
            if f["filter"] == "event":
                out.append(f"{sid} {names.get(sid) or ''}：{f['reason']}")
    return out


def build_plan(f: dict[str, Any], ctx: dict[str, Any], cfg: dict) -> dict[str, Any]:
    """f：facts；ctx：light、regime、rebound、holdings、sells、buys、blocked、setups、flags、names。"""
    pos = posture(ctx.get("light"), ctx.get("regime"), ctx.get("rebound"))
    index = f.get("index")
    levels = key_levels(index, f.get("ma") or [], cfg["plan_levels"]) if index else {"resistance": [], "support": []}
    entries = entry_candidates(ctx.get("buys") or [], ctx.get("blocked") or [], ctx.get("setups") or [])
    holds = holding_actions(ctx.get("holdings") or [], ctx.get("sells") or [], cfg)
    focus = {h["stock_id"] for h in holds} | {e["stock_id"] for e in entries}
    watch: list[str] = []
    on = f.get("overnight") or {}
    if "sox" in on or "tsm_adr" in on:
        watch.append("今晚美股：費城半導體指數與台積電 ADR 收盤（大盤燈號輸入）")
    if (f.get("flows") or {}).get("foreign_streak", 0) >= 3:
        fl = f["flows"]
        watch.append(f"外資是否結束連續{'賣' if fl['foreign'] < 0 else '買'}超（目前 {fl['foreign_streak']} 日）")
    after = f.get("news_after_close") or []
    if after:
        top = sorted(after, key=lambda n: not n.get("is_major_negative"))[:2]
        watch.append("晚間消息：" + "；".join(f"「{n['title']}」（{n['source']}）" for n in top))
    if levels["support"]:
        s = levels["support"][0]
        watch.append(f"開盤 30 分鐘能否{_at('守住', s['label'])} {_num(s['value'])}")
    return {
        "posture": pos,
        "levels": levels,
        "scenarios": scenarios(levels, pos, any(e["kind"] == "buy" for e in entries)),
        "holdings": holds,
        "entries": entries,
        "events": event_notes(ctx.get("flags") or {}, focus, ctx.get("names") or {}),
        "watch": watch,
    }
