"""消息面排雷（第 7.1 節）：收集重大訊息與新聞、AI 判讀、產生排除名單。

沒有新聞＝不影響。判定為重大利空且信心度 ≥ 門檻者，在 lookback_days 內排除。
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from engine.config import TZ


def dedup_key(item: dict) -> str:
    """同一則訊息重複抓取時的去重鍵：有網址用網址，否則用來源＋股票＋時間＋標題。"""
    if item.get("url"):
        basis = f"url|{item['stock_id']}|{item['url']}"
    else:
        basis = f"{item['source']}|{item['stock_id']}|{item['published_at'].isoformat()}|{item['title']}"
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
        out.append({
            "stock_id": stock_id,
            "published_at": published,
            "source": source,
            "title": title,
            "url": (item.findtext("link") or "").strip() or None,
            "body": (item.findtext("description") or "").strip()[:2000] or None,
        })
    return out


def news_flags(negative: list[dict], cfg: dict[str, Any]) -> list[dict]:
    """把判定為重大利空的新聞轉成排除旗標（信心度未達門檻者只留在 news 供查看）。"""
    threshold = cfg["confidence_threshold"]
    out: list[dict] = []
    for n in negative:
        conf = float(n.get("confidence") or 0)
        if conf < threshold:
            continue
        event = n.get("event_type") or "重大利空"
        out.append({
            "stock_id": n["stock_id"],
            "kind": "exclude",
            "reason": f"{event}：{n['title'][:40]}",
            "detail": {
                "news_id": n["news_id"],
                "event_type": n.get("event_type"),
                "confidence": round(conf, 2),
                "summary": n.get("summary"),
                "source_url": n.get("url"),
                "judged_by": n.get("judged_by"),
                "published_at": n["published_at"].isoformat(),
            },
        })
    return out
