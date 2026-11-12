"""消息面擴大（2026-11-12）：Yahoo股市 RSS、族群與盤勢新聞、產業利空只警示不排除。"""

from datetime import datetime

from engine.ai import RuleJudge, build_prompt
from engine.config import TZ, load_settings
from engine.filters.news import (
    assign_feed_items,
    dedup_key,
    match_stocks,
    name_variants,
    news_flags,
    parse_rss,
    yahoo_feeds,
)

SETTINGS = load_settings("free")
NEWS = SETTINGS.filters["news"]
YAHOO = NEWS["yahoo_rss"]
T = datetime(2026, 11, 12, 20, 0, tzinfo=TZ)
NAMES = {"2408": "南亞科", "2327": "國巨*", "3661": "世芯-KY", "2330": "台積電"}
GROUPS = {"記憶體": {"2408"}, "被動元件": {"2327"}, "半導體業": {"2330", "3661"}, "其他電子業": {"2330"},
          "航運": {"2603"}}


def _item(title, body=None, **kw):
    return {"stock_id": None, "published_at": T, "source": "Yahoo股市", "title": title, "url": None, "body": body,
            **kw}


def test_name_variants_strip_marks():
    assert name_variants("國巨*") == ["國巨"]
    assert sorted(name_variants("世芯-KY")) == ["世芯", "世芯-KY"]


def test_match_stocks_by_code_and_name():
    assert match_stocks("南亞科（2408）報價走強", NAMES) == ["2408"]
    assert match_stocks("國巨調漲價格", NAMES) == ["2327"]
    assert match_stocks("世芯營收創高", NAMES) == ["3661"]
    assert match_stocks("代號 24080 不是南亞", NAMES) == []    # 代號前後接數字不算


def test_yahoo_feeds_cover_stock_group_market():
    feeds = yahoo_feeds(YAHOO, ["2408", "2327", "2330"], GROUPS)
    urls = [f["url"] for f in feeds]
    assert "https://tw.stock.yahoo.com/rss?s=2408" in urls
    topics = {f["topic"]: f for f in feeds if f["scope"] == "group"}
    assert set(topics) == {"記憶體", "被動元件", "半導體業"}   # 沒有自選股的「航運」、「其他電子業」不抓
    assert topics["半導體業"]["url"].endswith("q=%E5%8D%8A%E5%B0%8E%E9%AB%94")   # 搜尋「半導體」
    assert any(f["scope"] == "market" and f["topic"] == "台股動態" for f in feeds)


def test_assign_feed_items_maps_mentions_to_stocks():
    feed = {"scope": "group", "topic": "記憶體", "stock_id": None}
    items = [_item("南亞科受惠 DRAM 漲價"), _item("記憶體報價下跌 庫存調整")]
    out = assign_feed_items(feed, items, NAMES)
    assert [(o["stock_id"], o["scope"]) for o in out] == [("2408", "stock"), (None, "group")]
    assert out[1]["topic"] == "記憶體"


def test_dedup_key_separates_topics():
    a = _item("同一篇", url="https://x/1", topic="記憶體", scope="group")
    b = _item("同一篇", url="https://x/1", topic="被動元件", scope="group")
    assert dedup_key(a) != dedup_key(b)


def test_parse_rss_keeps_provider():
    xml = """<rss version="2.0"><channel><item><title>南亞科法說</title><link>https://x/a</link>
      <source>中央社財經</source><description>&lt;p&gt;內容&lt;/p&gt;</description>
      <pubDate>Thu, 12 Nov 2026 12:00:00 GMT</pubDate></item></channel></rss>"""
    it = parse_rss(xml, None, "Yahoo股市")[0]
    assert it["source"] == "Yahoo股市／中央社財經" and it["body"] == "內容"


def test_rule_judge_scope_keywords():
    judge = RuleJudge(NEWS)
    # 產業利空關鍵字：族群消息判為利空
    j = judge.judge(_item("記憶體報價下跌 模組廠喊苦", scope="group", topic="記憶體"))
    assert j.is_major_negative and j.event_type == "產業需求轉弱"
    # 個股專用關鍵字（訴訟）不套用到盤勢消息，避免誤判
    j = judge.judge(_item("美國專利訴訟案開庭", scope="market", topic="國際財經"))
    assert not j.is_major_negative
    # 個股新聞兩種都比對
    assert judge.judge(_item("南亞科遭砍單", stock_id="2408")).event_type == "產業需求轉弱"


def test_news_flags_scope_actions():
    base = {"published_at": T, "url": None, "summary": "", "judged_by": "rules", "confidence": 0.9}
    rows = [
        {**base, "news_id": 1, "stock_id": "2408", "scope": "stock", "topic": None, "title": "南亞科現增",
         "event_type": "現金增資"},
        {**base, "news_id": 2, "stock_id": "2327", "scope": "stock", "topic": None, "title": "國巨遭砍單",
         "event_type": "產業需求轉弱"},
        {**base, "news_id": 3, "stock_id": None, "scope": "group", "topic": "記憶體", "title": "DRAM 報價下跌",
         "event_type": "產業需求轉弱"},
        {**base, "news_id": 4, "stock_id": None, "scope": "market", "topic": "國際財經", "title": "美擴大出口管制",
         "event_type": "政策與地緣風險"},
    ]
    ids = ["2408", "2327", "2330"]
    flags = news_flags(rows, NEWS, GROUPS, ids)
    kinds = [(f["stock_id"], f["kind"], f["detail"]["news_id"]) for f in flags]
    assert ("2408", "exclude", 1) in kinds                 # 個股重大利空：排除
    assert ("2327", "warn", 2) in kinds                    # 個股的產業利空：只警示
    assert ("2408", "warn", 3) in kinds and not any(k[2] == 3 and k[0] != "2408" for k in kinds)
    assert {k[0] for k in kinds if k[2] == 4} == set(ids)  # 盤勢利空：全部自選股警示
    assert any(f["reason"].startswith("族群消息（記憶體）") for f in flags)
    off = {**NEWS, "scope_actions": {"group": "none", "market": "none"}}
    assert {f["detail"]["news_id"] for f in news_flags(rows, off, GROUPS, ids)} == {1, 2}


def test_prompt_for_group_news():
    _system, user = build_prompt(_item("DRAM 報價下跌", scope="group", topic="記憶體"), ["產業需求轉弱"])
    assert "族群「記憶體」" in user
