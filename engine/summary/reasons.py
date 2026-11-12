"""漲跌原因歸納（規則）：把事實轉成「為什麼這樣走」的條列，依重要性排序。

每條原因只陳述資料算得出來的事實與常見解讀，不臆測資料以外的事；消息面只列標題與出處。
tone：neg＝偏空因素、pos＝偏多因素、info＝中性資訊。
"""

from __future__ import annotations

from typing import Any


def _pts(x: float) -> str:
    return f"{abs(x):,.0f} 點"


def _yi(x: float) -> str:
    """元 → 億元（估算，取到 0.1 億）。"""
    return f"{abs(x) / 1e8:,.1f} 億元"


def _reason(key: str, text: str, tone: str, weight: float) -> dict[str, Any]:
    return {"key": key, "text": text, "tone": tone, "weight": round(weight, 2)}


def build_reasons(f: dict[str, Any], cfg: dict) -> list[dict[str, Any]]:
    move = f.get("index")
    if not move:
        return []
    down = move["change"] < 0
    up = move["change"] > 0
    out: list[dict[str, Any]] = []

    # 1. 短線漲多／跌深
    run = f.get("prior_run")
    th = cfg["run_threshold"]
    if run and down and run["pct"] >= th:
        out.append(_reason("profit_taking", f"前 {run['days']} 個交易日累計漲 {run['pct']:.1%}（{_pts(run['points'])}），"
                           "短線漲多，獲利了結賣壓出籠", "neg", 9))
    elif run and up and run["pct"] <= -th:
        out.append(_reason("oversold_bounce", f"前 {run['days']} 個交易日累計跌 {abs(run['pct']):.1%}"
                           f"（{_pts(run['points'])}），跌深後的技術性反彈", "pos", 9))

    # 2. 均線壓力／支撐、跌破／站回
    for ma in f.get("ma") or []:
        n = ma["days"]
        if ma.get("touched") == "resistance" and down:
            out.append(_reason(f"ma{n}_resistance", f"盤中高點 {move.get('high', 0):,.0f} 碰到 {n} 日線 "
                               f"{ma['value']:,.0f} 後拉回，均線反壓明顯", "neg", 8 + n / 100))
        elif ma.get("touched") == "support" and up:
            out.append(_reason(f"ma{n}_support", f"盤中低點 {move.get('low', 0):,.0f} 碰到 {n} 日線 "
                               f"{ma['value']:,.0f} 後回升，均線有撐", "pos", 8 + n / 100))
        if ma.get("crossed") == "down":
            out.append(_reason(f"ma{n}_break", f"收盤跌破 {n} 日線 {ma['value']:,.0f}", "neg", 5 + n / 100))
        elif ma.get("crossed") == "up":
            out.append(_reason(f"ma{n}_reclaim", f"收盤站回 {n} 日線 {ma['value']:,.0f}", "pos", 5 + n / 100))

    # 3. 權值股貢獻
    con = f.get("contributors")
    if con:
        lead = con["drags"] if down else con["boosts"] if up else []
        if lead:
            names = "、".join(f"{x['name'] or x['stock_id']} {x['points']:+,.0f} 點" for x in lead[:3])
            share = sum(x["points"] for x in lead[:3]) / move["change"] if move["change"] else 0
            text = f"權值股{'拖累' if down else '撐盤'}：{names}（估算），前三檔約佔{'跌' if down else '漲'}點 {share:.0%}"
            out.append(_reason("heavyweights", text, "neg" if down else "pos", 8.5 if share >= 0.3 else 6))

    # 4. 寬度：普漲普跌，或指數與多數股票方向相反
    b = f.get("breadth")
    if b and b.get("adv_ratio") is not None:
        r = b["adv_ratio"]
        counts = f"上漲 {b['advancers']:,} 家、下跌 {b['decliners']:,} 家"
        limits = (f"、漲停 {b['limit_up']}" if b["limit_up"] else "") + (
            f"、跌停 {b['limit_down']}" if b["limit_down"] else "")
        if down and r <= 1 - cfg["breadth_one_sided"]:
            out.append(_reason("breadth", f"賣壓普遍：{counts}{limits}", "neg", 7))
        elif up and r >= cfg["breadth_one_sided"]:
            out.append(_reason("breadth", f"買盤普遍：{counts}{limits}", "pos", 7))
        elif down and r > 0.5:
            out.append(_reason("breadth", f"指數下跌但{counts}，跌勢集中在權值股", "info", 6))
        elif up and r < 0.5:
            out.append(_reason("breadth", f"指數上漲但{counts}，漲勢集中在權值股", "info", 6))

    # 5. 法人（上市，估算）
    fl = f.get("flows")
    if fl:
        th_v = cfg["flow_threshold_billion"] * 1e8
        fv, streak = fl["foreign"], fl.get("foreign_streak", 0)
        side = "賣超" if fv < 0 else "買超"
        if abs(fv) >= th_v:
            more = f"，連續 {streak} 日{side}" if streak >= 3 else ""
            out.append(_reason("foreign", f"外資{side}約 {_yi(fv)}{more}", "neg" if fv < 0 else "pos", 7.5))
        elif streak >= 5:
            out.append(_reason("foreign", f"外資今日{side}約 {_yi(fv)}，金額不大，但已連續 {streak} 日{side}",
                               "neg" if fv < 0 else "pos", 5))
        for k, label in (("trust", "投信"), ("dealer", "自營商")):
            v = fl[k]
            if abs(v) >= th_v:
                out.append(_reason(k, f"{label}{'賣超' if v < 0 else '買超'}約 {_yi(v)}", "neg" if v < 0 else "pos", 4.5))
        total = fl["total"]
        if down and total > 0:
            out.append(_reason("flows_divergence", f"三大法人合計買超約 {_yi(total)}，指數下跌的賣壓主要不是來自法人",
                               "info", 6.5))
        elif up and total < 0:
            out.append(_reason("flows_divergence", f"三大法人合計賣超約 {_yi(total)}，上漲的買盤主要不是來自法人",
                               "info", 6.5))

    # 6. 成交值
    t = f.get("turnover")
    if t and t.get("ratio"):
        val = f"上市成交值 {t['value'] / 1e8:,.0f} 億元，為近 {t['avg_days']} 日平均的 {t['ratio']:.2f} 倍"
        if t["ratio"] >= cfg["heavy_volume_ratio"]:
            out.append(_reason("volume", f"{val}，{'爆量下跌，賣壓沉重' if down else '價漲量增' if up else '量大不動'}",
                               "neg" if down else "pos", 6.8))
        elif t["ratio"] <= cfg["light_volume_ratio"]:
            out.append(_reason("volume", f"{val}，量縮{'下跌，賣壓有限' if down else '上漲，追價意願不足' if up else ''}",
                               "info", 4))

    # 7. 收盤位置
    if move.get("close_zone") == "low" and down:
        out.append(_reason("close_low", "收在當日最低點附近，尾盤沒有買盤承接，隔日開盤容易延續弱勢", "neg", 6.6))
    elif move.get("close_zone") == "high" and up:
        out.append(_reason("close_high", "收在當日最高點附近，尾盤買盤積極", "pos", 6.6))

    # 8. 隔夜美股、匯率、期貨
    on = f.get("overnight") or {}
    for key, label in (("sox", "費城半導體指數"), ("tsm_adr", "台積電 ADR")):
        x = on.get(key)
        if not x or x.get("pct") is None or abs(x["pct"]) < cfg["overnight_threshold"]:
            continue
        same = (x["pct"] > 0) == up
        tail = "" if same else "，但台股走勢相反，利多沒有延續" if x["pct"] > 0 else "，但台股未受拖累"
        out.append(_reason(key, f"{label}前一交易日 {x['pct']:+.1%}{tail}", "pos" if x["pct"] > 0 else "neg",
                           5.5 if same else 6.2))
    fx = on.get("usd_twd")
    if fx and fx.get("pct") is not None and abs(fx["pct"]) >= cfg["fx_threshold"]:
        weak = fx["change"] > 0  # 美元兌台幣上升＝台幣貶值
        out.append(_reason("fx", f"新台幣{'貶' if weak else '升'}值 {abs(fx['change']):.3f} 元至 {fx['value']:.3f}，"
                           f"{'資金偏向匯出' if weak else '資金偏向匯入'}", "neg" if weak else "pos", 5.2))
    oi = on.get("foreign_tx_oi")
    if oi and abs(oi["change"]) >= 3000:
        more_short = oi["change"] < 0  # 淨部位下降＝空單增加
        out.append(_reason("futures_oi", f"外資台指期淨部位{'減少' if more_short else '增加'} {abs(oi['change']):,.0f} 口"
                           f"（淨{'空' if oi['value'] < 0 else '多'}單 {abs(oi['value']):,.0f} 口）",
                           "neg" if more_short else "pos", 4.8))

    # 9. 族群輪動
    g = f.get("groups") or {}
    if g.get("strong") and g.get("weak"):
        strong = "、".join(f"{x['name']} {x['pct']:+.1%}" for x in g["strong"])
        weak = "、".join(f"{x['name']} {x['pct']:+.1%}" for x in g["weak"])
        out.append(_reason("rotation", f"族群：強勢 {strong}；弱勢 {weak}", "info", 5.8))
    elif g.get("weak") and down:
        weak = "、".join(f"{x['name']} {x['pct']:+.1%}" for x in g["weak"])
        out.append(_reason("rotation", f"族群全面走弱：{weak}", "neg", 5.8))
    elif g.get("strong") and up:
        strong = "、".join(f"{x['name']} {x['pct']:+.1%}" for x in g["strong"])
        out.append(_reason("rotation", f"族群全面走強：{strong}", "pos", 5.8))

    # 10. 消息面（只列標題，判讀為利空者標註）
    news = f.get("news") or []
    if news:
        neg = [n for n in news if n.get("is_major_negative")]
        top = (neg or news)[:2]
        titles = "；".join(f"「{n['title']}」（{n['source']}）" for n in top)
        out.append(_reason("news", f"消息面：{titles}", "neg" if neg else "info", 7.8 if neg else 5))

    out.sort(key=lambda r: -r["weight"])
    return out
