"""消息面排雷（第 7.1 節）：收集重大訊息與新聞、AI 判讀、產生排除名單。

沒有新聞＝不影響。判定為重大利空且信心度 ≥ 門檻者，在 lookback_days 內排除。
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

from engine.config import TZ


def dedup_key(item: dict) -> str:
    """同一則訊息重複抓取時的去重鍵：有網址用網址，否則用來源＋股票＋時間＋標題。

    產業、盤勢消息沒有個股代號，改用「#主題」區分（同一篇文章在不同主題各留一筆）。
    """
    owner = item["stock_id"] if item.get("stock_id") else f"#{item.get('topic') or ''}"
    if item.get("url"):
        basis = f"url|{owner}|{item['url']}"
    else:
        basis = f"{item['source']}|{owner}|{item['published_at'].isoformat()}|{item['title']}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def parse_rss(xml_text: str, stock_id: str, source: str) -> list[dict]:
    """解析 RSS 2.0；只保留標題含股票代號或由個股專屬來源取得者（呼叫端決定 stock_id）。"""
    out: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        pub = item.findtext("pubDate")
        try:
            published = parsedate_to_datetime(pub).astimezone(TZ) if pub else datetime.now(TZ)
        except (TypeError, ValueError):
            published = datetime.now(TZ)
        provider = (item.findtext("source") or "").strip()
        body = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        out.append({
            "stock_id": stock_id,
            "published_at": published,
            "source": f"{source}／{provider}" if provider else source,  # 保留原始出處（Yahoo股市 RSS 條款要求）
            "title": title,
            "url": (item.findtext("link") or "").strip() or None,
            "body": " ".join(body.split())[:2000] or None,
        })
    return out


def name_variants(name: str) -> list[str]:
    """新聞內文常用的公司簡稱：去掉「*」標記與「-KY」字尾，兩個字以上才比對。"""
    base = name.replace("*", "").strip()
    out = {base, base.removesuffix("-KY").removesuffix("KY").strip()}
    return [v for v in out if len(v) >= 2]


def match_stocks(text: str, names: dict[str, str]) -> list[str]:
    """文章提到哪些股票：標題或摘要出現代號（前後不接數字）或公司簡稱。"""
    hits: list[str] = []
    for sid, name in names.items():
        found = re.search(rf"(?<!\d){re.escape(sid)}(?!\d)", text) is not None
        if not found:
            found = any(v in text for v in name_variants(name))
        if found:
            hits.append(sid)
    return hits


def yahoo_feeds(cfg: dict[str, Any], ids: list[str], groups: dict[str, set[str]]) -> list[dict]:
    """Yahoo股市 RSS 要抓的清單：個股（每檔一個）、族群名稱與自訂關鍵字、新聞分類。

    Yahoo股市 RSS 條款允許個人非商業使用，須保留每則訊息的出處（source 欄位記錄「Yahoo股市／原始來源」）。
    """
    feeds: list[dict] = []
    for sid in ids:
        feeds.append({"url": cfg["stock_url"].format(stock_id=sid), "scope": "stock", "stock_id": sid,
                      "topic": None})
    keywords: list[tuple[str, str, str]] = []  # （搜尋字、範圍、主題）
    if cfg.get("group_keywords", True):
        for g, members in sorted(groups.items()):
            if not members & set(ids) or g.startswith("其他"):
                continue  # 「其他電子業」這類名稱當關鍵字沒有意義
            kw = g[:-1] if g.endswith("業") and len(g) > 2 else g  # 產業別「半導體業」→ 搜尋「半導體」
            keywords.append((kw, "group", g))
    keywords += [(k, "market", k) for k in cfg.get("extra_keywords") or []]
    for kw, scope, topic in keywords:
        feeds.append({"url": cfg["keyword_url"].format(keyword=quote(kw)), "scope": scope, "stock_id": None,
                      "topic": topic})
    for cat, label in (cfg.get("categories") or {}).items():
        feeds.append({"url": cfg["category_url"].format(category=cat), "scope": "market", "stock_id": None,
                      "topic": label})
    return feeds


def assign_feed_items(feed: dict, items: list[dict], names: dict[str, str]) -> list[dict]:
    """個股 RSS 直接歸到該股；關鍵字與分類 RSS 先比對文中提到的自選股，沒提到的留作族群或盤勢消息。"""
    out: list[dict] = []
    for it in items:
        base = {**it, "scope": feed["scope"], "topic": feed["topic"]}
        if feed["scope"] == "stock":
            out.append({**base, "stock_id": feed["stock_id"], "scope": "stock"})
            continue
        hits = match_stocks(f"{it['title']}\n{it.get('body') or ''}", names)
        if hits:
            out += [{**base, "stock_id": sid, "scope": "stock"} for sid in hits]
        else:
            out.append({**base, "stock_id": None})
    return out


def news_flags(negative: list[dict], cfg: dict[str, Any], groups: dict[str, set[str]] | None = None,
               ids: list[str] | None = None) -> list[dict]:
    """把判定為重大利空的新聞轉成旗標（信心度未達門檻者只留在 news 供查看）。

    個股的重大利空（negative_event_types）＝排除；產業、政策類利空（scope_rule_keywords 的類型）只加警示，
    族群消息套用到該族群的自選股、盤勢消息套用到全部自選股（scope_actions 可改為 none 停用）。
    """
    threshold = cfg["confidence_threshold"]
    soft_events = set((cfg.get("scope_rule_keywords") or {}).keys())
    actions = cfg.get("scope_actions") or {"group": "warn", "market": "warn"}
    groups = groups or {}
    universe = list(ids or [])
    out: list[dict] = []
    for n in negative:
        conf = float(n.get("confidence") or 0)
        if conf < threshold:
            continue
        event = n.get("event_type") or "重大利空"
        scope = n.get("scope") or "stock"
        detail = {
            "news_id": n["news_id"],
            "event_type": n.get("event_type"),
            "confidence": round(conf, 2),
            "summary": n.get("summary"),
            "source_url": n.get("url"),
            "judged_by": n.get("judged_by"),
            "published_at": n["published_at"].isoformat(),
            "scope": scope,
        }
        if scope == "stock":
            targets, kind, label = [n["stock_id"]], ("warn" if event in soft_events else "exclude"), ""
        else:
            if actions.get(scope, "warn") == "none":
                continue
            topic = n.get("topic") or ""
            targets = sorted(groups.get(topic, set()) & set(universe)) if scope == "group" else universe
            kind, label = "warn", ("族群消息" if scope == "group" else "盤勢消息") + (f"（{topic}）" if topic else "") + "："
        for sid in targets:
            out.append({
                "stock_id": sid,
                "kind": kind,
                "reason": f"{label}{event}：{n['title'][:40]}",
                "detail": detail,
            })
    return out

