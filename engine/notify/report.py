"""每日報告（第 9 節）：只讀資料庫，確保 LINE 內容與網站一致。

report_data() 產生結構化資料（網站可共用），render_blocks() 轉成 LINE 文字段落。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from engine.config import Settings
from engine.filters.market_light import LIGHT_LABELS
from engine.filters.rebound import STATE_LABELS, evidence_text
from engine.pipeline import universe
from engine.repository import Repository
from engine.signals.regime import market_regime

WEEKDAYS = "一二三四五六日"
HORIZON_LABELS = {"short": "短線", "long": "中長線"}


def report_data(settings: Settings, repo: Repository, as_of: date) -> dict[str, Any]:
    cfg = settings.notify["daily_report"]
    market = repo.market_row(as_of)
    strength = (market or {}).get("group_strength") or []
    ranked = [g for g in strength if g.get("rank")]
    ids = universe(settings, repo)
    scores = repo.score_map(as_of, "short")
    names = repo.stock_names(sorted(set(ids) | {s for g in ranked for s in g["members"]}))

    group_leaders = []
    for g in ranked[: cfg["top_groups"]]:
        members = sorted((s for s in g["members"] if s in scores), key=lambda s: -scores[s])
        group_leaders.append({
            "name": g["name"],
            "stocks": [{"stock_id": s, "name": names.get(s), "score": scores[s]}
                       for s in members[: cfg["stocks_per_group"]]],
        })

    signal_job = repo.latest_job("signals", as_of)
    fetch_job = repo.latest_job("fetch", as_of)
    quality = (fetch_job[1].get("quality") if fetch_job else None) or {}
    rebound = (market or {}).get("rebound") or {}
    regime = market_regime(market["light"], rebound.get("state"), settings.risk) if market else None
    return {
        "date": as_of,
        "plan": settings.plan.label,
        "market": market,
        "regime": regime,
        "groups": ranked[: cfg["top_groups"]],
        "weak_groups": [g["name"] for g in ranked if g.get("weak")],
        "group_leaders": group_leaders,
        "buy_signals": repo.signals_on(as_of, "buy"),
        "sell_signals": repo.signals_on(as_of, "sell"),
        "signal_status": signal_job[0] if signal_job else None,
        "signal_reason": (signal_job[1].get("reason") if signal_job else None),
        "blocked": (signal_job[1].get("blocked") if signal_job else None) or [],
        "setups": (signal_job[1].get("setups") if signal_job else None) or [],
        "holdings": repo.holdings_detail(as_of),
        "quality_ok": quality.get("ok"),
        "quality_warnings": len(quality.get("warnings") or []),
    }


# ---------- 文字 ----------

def _num(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x:,.2f}".rstrip("0").rstrip(".") if x < 1000 else f"{x:,.0f}"


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.1%}"


def _market_block(d: dict) -> str:
    m = d["market"]
    head = f"【台股每日報告】{d['date']:%Y/%m/%d}（{WEEKDAYS[d['date'].weekday()]}）"
    if not m:
        return f"{head}\n大盤燈號：尚未計算"
    total = (m["inputs"] or {}).get("total")
    lines = [head, f"大盤燈號：{LIGHT_LABELS.get(m['light'], m['light'])}（{total:+d}）" if total is not None
             else f"大盤燈號：{LIGHT_LABELS.get(m['light'], m['light'])}"]
    for key, item in (m["inputs"] or {}).items():
        if key == "total" or not isinstance(item, dict):
            continue
        lines.append(f"・{item.get('label', key)}：{item.get('reason')}（{item.get('vote', 0):+d}）")
    rb = m.get("rebound") or {}
    if rb:
        state = STATE_LABELS.get(rb.get("state"), rb.get("state"))
        head = f"反彈偵測：{state}" + (f"（{rb.get('points', 0)} 點）" if rb.get("state") != "none" else "")
        rally = rb.get("rally") or {}
        if rally.get("reason"):
            head += f"｜{rally['reason']}"
        lines.append(head)
        lines += [f"・{t}" for t in evidence_text(rb)]
    regime = d.get("regime") or {}
    if m["light"] == "bearish":
        if regime.get("mode") in ("rebound_confirmed", "rebound_attempt"):
            lines.append(f"大盤偏空但{STATE_LABELS[rb['state']]}：買進訊號以{regime['label']}處理"
                         f"（部位 ×{regime['size_factor']:g}、停損收緊）")
        else:
            lines.append("大盤偏空：不發新買進訊號、停損收緊、建議降低部位")
    return "\n".join(lines)


def _groups_block(d: dict) -> str | None:
    if not d["groups"]:
        return None
    lines = ["【族群強弱】"]
    for g in d["groups"]:
        score = f"平均 {g['avg_score']:.0f} 分" if g.get("avg_score") is not None else "平均分數 —"
        rel = g.get("rel_return_20")
        ret = f"近 1 月 {_pct(g.get('return_20'))}" + (f"（相對大盤 {_pct(rel)}）" if rel is not None else "")
        lines.append(f"{g['rank']}. {g['name']}｜{score}｜{ret}")
    if d["weak_groups"]:
        lines.append(f"弱勢族群（不發買進）：{'、'.join(d['weak_groups'])}")
    leaders = [g for g in d["group_leaders"] if g["stocks"]]
    if leaders:
        lines.append("")
        lines.append("【各族群前幾名】")
        for g in leaders:
            stocks = "、".join(f"{s['stock_id']} {s['name'] or ''} {s['score']:.0f}" for s in g["stocks"])
            lines.append(f"{g['name']}：{stocks}")
    return "\n".join(lines)


def _signal_full(s: dict) -> str:
    det = s["detail"] or {}
    sc = det.get("score") or {}
    sz = det.get("sizing") or {}
    tag = f"【{det['regime_label']}】" if det.get("regime") not in (None, "normal") else ""
    lines = [
        f"{tag}{s['stock_id']} {det.get('name') or ''}｜{HORIZON_LABELS[s['horizon']]} {sc.get('total', 0):.1f} 分"
        f"（基 {sc.get('fundamental', 0):.0f}／技 {sc.get('technical', 0):.0f}／籌 {sc.get('chips', 0):.0f}）",
        f"收盤 {_num(det.get('close'))}｜{'、'.join(det.get('trigger_text') or [])}",
    ]
    if det.get("high_score_reasons"):
        lines.append(f"高分理由：{'、'.join(det['high_score_reasons'])}")
    lines.append(f"買進 {_num(s['entry_low'])}–{_num(s['entry_high'])}｜停損 {_num(s['stop_loss'])}"
                 f"｜停利 {_num(s['take_profit'])}")
    if s["suggested_lots"]:
        lines.append(f"建議 {s['suggested_lots']} 張（約 {sz.get('amount', 0):,} 元，"
                     f"最大虧損約 {sz.get('risk_amount', 0):,} 元）")
    elif sz.get("odd_shares"):
        lines.append(f"建議零股 {sz['odd_shares']:,} 股（約 {sz.get('amount', 0):,} 元）")
    else:
        lines.append("建議 0 張")
    for note in (det.get("warnings") or []) + (sz.get("notes") or []):
        lines.append(f"提示：{note}")
    return "\n".join(lines)


def _signal_compact(s: dict) -> str:
    det = s["detail"] or {}
    total = (det.get("score") or {}).get("total", 0)
    lots = f"{s['suggested_lots']} 張" if s["suggested_lots"] else (
        f"零股 {(det.get('sizing') or {}).get('odd_shares', 0)} 股")
    tag = "反彈" if det.get("regime") not in (None, "normal") else ""
    return (f"{tag}{s['stock_id']} {det.get('name') or ''} {HORIZON_LABELS[s['horizon']][:1]}{total:.0f}｜"
            f"{_num(s['entry_low'])}–{_num(s['entry_high'])}｜損 {_num(s['stop_loss'])}"
            f"｜利 {_num(s['take_profit'])}｜{lots}")


def _signals_blocks(d: dict, compact: bool, max_blocked: int) -> list[str]:
    if d["signal_status"] == "skipped":
        return [f"【買進訊號】今日暫停：{d['signal_reason']}"]
    sigs = d["buy_signals"]
    if not sigs:
        head = "【買進訊號】今日沒有符合條件的個股"
        if d["signal_status"] is None:
            head = "【買進訊號】今日訊號步驟尚未執行"
        blocks = [head]
    elif compact:
        blocks = ["\n".join([f"【買進訊號】{len(sigs)} 筆（精簡版）", *(_signal_compact(s) for s in sigs)])]
    else:
        blocks = [f"【買進訊號】{len(sigs)} 筆", *(_signal_full(s) for s in sigs)]
    if d["blocked"]:
        lines = ["觸發進場條件但未發訊號："]
        for b in d["blocked"][:max_blocked]:
            lines.append(f"・{b['stock_id']} {b.get('name') or ''}：{'、'.join(b['reasons'])}")
            if b.get("entry_high") and not compact:
                lines.append(f"  {'、'.join(b.get('triggers') or [])}")
                lines.append(f"  買進 {_num(b['entry_low'])}–{_num(b['entry_high'])}｜停損 {_num(b['stop_loss'])}"
                             f"｜目標 {_num(b['take_profit'])}｜成立時 {b.get('suggested_lots', 0)} 張")
        if len(d["blocked"]) > max_blocked:
            lines.append(f"・另有 {len(d['blocked']) - max_blocked} 檔")
        blocks.append("\n".join(lines))
    return blocks


def _setups_block(d: dict, compact: bool) -> str | None:
    items = d.get("setups") or []
    if not items:
        return None
    lines = ["【醞釀名單】跌深＋強勢跡象，隔天收盤站上轉強價才成立（非買進訊號）"]
    for x in items:
        if compact:
            lines.append(f"{x['stock_id']} {x.get('name') or ''}｜轉強 {_num(x['trigger_price'])}"
                         f"｜損 {_num(x['stop_loss'])}｜標 {_num(x['take_profit'])}")
            continue
        lines.append(f"{x['stock_id']} {x.get('name') or ''}｜收盤 {_num(x['close'])}｜{'、'.join(x['sign_text'])}")
        lines.append(f"  轉強價 {_num(x['trigger_price'])}｜買進 {_num(x['entry_low'])}–{_num(x['entry_high'])}"
                     f"｜停損 {_num(x['stop_loss'])}｜目標 {_num(x['take_profit'])}（{x['target_basis']}）")
        lots = f"{x['suggested_lots']} 張" if x.get("suggested_lots") else (
            f"零股 {x['odd_shares']:,} 股" if x.get("odd_shares") else "0 張")
        lines.append(f"  成立時建議 {lots}" + (f"（部位 ×{x['size_factor']:g}）" if x.get("size_factor", 1) < 1 else "")
                     + "".join(f"｜{n}" for n in x.get("notes") or []))
    return "\n".join(lines)


def _holdings_block(d: dict) -> str | None:
    if not d["holdings"] and not d["sell_signals"]:
        return None
    lines = ["【持股提醒】"]
    for s in d["sell_signals"]:
        det = s["detail"] or {}
        lines.append(f"・{s['stock_id']} {det.get('name') or ''}：{s['reason']}")
    sold = {s["stock_id"] for s in d["sell_signals"]}
    for h in d["holdings"]:
        if h["stock_id"] in sold:
            continue
        gain = h["close"] / h["avg_cost"] - 1 if h["close"] and h["avg_cost"] else None
        lots = h["shares"] / 1000
        lines.append(f"・{h['stock_id']} {h['name']} {lots:g} 張｜成本 {_num(h['avg_cost'])} 現價 {_num(h['close'])}"
                     f"（{_pct(gain)}）｜停損 {_num(h['stop_loss'])}")
    return "\n".join(lines)


def _footer(d: dict, site_url: str | None) -> str:
    if d["quality_ok"] is False:
        status = "資料完整性檢查未通過"
    elif d["quality_ok"] is None:
        status = "今日資料尚未抓取"
    else:
        status = "資料完整" + (f"（{d['quality_warnings']} 則提醒）" if d["quality_warnings"] else "")
    lines = [f"系統：{status}｜{d['plan']}", "僅供參考，買賣請自行判斷"]
    if site_url:
        lines.append(f"網站：{site_url}")
    return "\n".join(lines)


def render_blocks(d: dict, compact: bool = False, max_blocked: int = 10, site_url: str | None = None) -> list[str]:
    blocks = [_market_block(d)]
    groups = _groups_block(d)
    if groups:
        blocks.append(groups)
    blocks += _signals_blocks(d, compact, max_blocked)
    setups = _setups_block(d, compact)
    if setups:
        blocks.append(setups)
    holdings = _holdings_block(d)
    if holdings:
        blocks.append(holdings)
    blocks.append(_footer(d, site_url))
    return blocks


def render_messages(d: dict, cfg: dict, site_url: str | None = None) -> list[str]:
    """完整版超過則數上限時改用精簡版；仍超過就截斷並附上網站連結。"""
    from engine.notify.line import pack_blocks

    limit, max_msgs = cfg["line"]["max_text_chars"], cfg["line"]["max_messages_per_report"]
    max_blocked = cfg["daily_report"]["max_blocked"]
    for compact in (False, True):
        msgs = pack_blocks(render_blocks(d, compact, max_blocked, site_url), limit)
        if len(msgs) <= max_msgs:
            return msgs
    note = "（內容過長已截斷" + (f"，完整內容請見網站：{site_url}）" if site_url else "，完整內容請見網站）")
    return msgs[: max_msgs - 1] + [msgs[max_msgs - 1][: limit - len(note) - 1] + "\n" + note]
