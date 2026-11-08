"""AI 判讀抽象介面（消息面排雷）。

AI_PROVIDER=ollama（預設）或 claude。AI 無法連線或回傳格式錯誤時，
自動改用關鍵字規則判讀（judged_by = rules），不會讓整批中斷。
輸出格式：{stock_id, is_major_negative, event_type, confidence, summary, source_url}
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from engine.config import Settings

log = logging.getLogger(__name__)


@dataclass
class Judgment:
    stock_id: str
    is_major_negative: bool
    event_type: str | None
    confidence: float
    summary: str
    source_url: str | None
    judged_by: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class NewsJudge(Protocol):
    name: str

    def judge(self, item: dict) -> Judgment: ...


NO_EVENT = "無"


class JudgeError(RuntimeError):
    """AI 判讀失敗（連線、格式）。"""


def judgment_schema(event_types: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "is_major_negative": {"type": "boolean"},
            "event_type": {"type": "string", "enum": [*event_types, NO_EVENT]},
            "confidence": {"type": "number"},
            "summary": {"type": "string"},
        },
        "required": ["is_major_negative", "event_type", "confidence", "summary"],
        "additionalProperties": False,
    }


def build_prompt(item: dict, event_types: list[str]) -> tuple[str, str]:
    system = (
        "你是台股消息面風險判讀助手。判斷一則公司新聞或重大訊息，是否屬於會讓短期股價明顯受壓的「重大利空」。\n"
        f"重大利空類型只限：{'、'.join(event_types)}；不是重大利空時 event_type 填「{NO_EVENT}」。\n"
        "一般例行公告（董事會召開、財報通過且獲利正常、更名、股利發放、澄清媒體報導等）不算重大利空。\n"
        "confidence 為 0 到 1 的把握程度。summary 用繁體中文（台灣用語）一句話說明判斷理由，30 字內。\n"
        "只輸出符合格式的 JSON。"
    )
    body = (item.get("body") or "")[:2500]
    user = f"股票代號：{item['stock_id']}\n標題：{item['title']}\n內容：{body or '（無）'}"
    return system, user


def _coerce(stock_id: str, raw: dict, item: dict, judged_by: str, event_types: list[str]) -> Judgment:
    try:
        is_neg = bool(raw["is_major_negative"])
        event = raw.get("event_type")
        event = event if event in event_types else None
        conf = max(0.0, min(1.0, float(raw.get("confidence", 0))))
        summary = str(raw.get("summary") or "").strip()[:200]
    except (KeyError, TypeError, ValueError) as e:
        raise JudgeError(f"判讀結果格式不符：{raw}") from e
    if is_neg and event is None:
        conf = min(conf, 0.5)  # 不在清單內的利空類型不當作重大利空排除
    return Judgment(stock_id, is_neg, event, conf, summary, item.get("url"), judged_by)


class RuleJudge:
    """關鍵字備援判讀：命中設定檔關鍵字即視為重大利空。"""

    name = "rules"

    def __init__(self, cfg: dict):
        self.keywords: dict[str, list[str]] = cfg.get("rule_keywords") or {}
        self.confidence = float(cfg.get("rule_confidence", 0.75))

    def judge(self, item: dict) -> Judgment:
        title = item.get("title") or ""
        text = f"{title}\n{(item.get('body') or '')[:300]}"
        for event, words in self.keywords.items():
            for w in words:
                if w in title:
                    return Judgment(item["stock_id"], True, event, self.confidence, f"標題含「{w}」", item.get("url"),
                                    self.name)
                if w in text:
                    return Judgment(item["stock_id"], True, event, round(self.confidence * 0.8, 2),
                                    f"內文含「{w}」", item.get("url"), self.name)
        return Judgment(item["stock_id"], False, None, 0.5, "未命中利空關鍵字", item.get("url"), self.name)


class OllamaJudge:
    name = "ollama"

    def __init__(self, base_url: str, model: str, event_types: list[str], timeout: float = 120,
                 transport: httpx.BaseTransport | None = None):
        if not model:
            raise JudgeError("未設定 OLLAMA_MODEL")
        self.url = base_url.rstrip("/") + "/api/chat"
        self.model = model
        self.event_types = event_types
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def judge(self, item: dict) -> Judgment:
        system, user = build_prompt(item, self.event_types)
        try:
            resp = self.client.post(self.url, json={
                "model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "format": judgment_schema(self.event_types),
                "stream": False,
                "options": {"temperature": 0},
            })
            resp.raise_for_status()
            raw = json.loads(resp.json()["message"]["content"])
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise JudgeError(f"Ollama 判讀失敗：{e}") from e
        return _coerce(item["stock_id"], raw, item, self.name, self.event_types)


class ClaudeJudge:
    """Claude API（官方 anthropic SDK，選用套件：pip install -e ".[claude]"）。"""

    name = "claude"

    def __init__(self, model: str, event_types: list[str], client: Any = None, on_call=None):
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise JudgeError("未安裝 anthropic 套件（pip install -e \".[claude]\"）") from e
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise JudgeError("未設定 ANTHROPIC_API_KEY")
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.event_types = event_types
        self.on_call = on_call

    def judge(self, item: dict) -> Judgment:
        system, user = build_prompt(item, self.event_types)
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": "low",  # 單則分類，不需要深度推理
                    "format": {"type": "json_schema", "schema": judgment_schema(self.event_types)},
                },
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # 遭安全分類器拒答時由伺服器改用建議模型重跑
            )
        except Exception as e:  # SDK 已內建重試；仍失敗則交給備援
            raise JudgeError(f"Claude 判讀失敗：{e}") from e
        if self.on_call:
            self.on_call()
        if resp.stop_reason == "refusal":
            raise JudgeError("Claude 拒絕判讀此則內容")
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if not text:
            raise JudgeError(f"Claude 未回傳內容（stop_reason={resp.stop_reason}）")
        try:
            raw = json.loads(text)
        except ValueError as e:
            raise JudgeError(f"Claude 回傳不是 JSON：{text[:200]}") from e
        return _coerce(item["stock_id"], raw, item, self.name, self.event_types)


class FallbackJudge:
    """先用 AI，失敗時改用關鍵字規則；AI 連續失敗 3 次後本次執行不再嘗試。"""

    def __init__(self, primary: NewsJudge | None, fallback: RuleJudge, primary_error: str | None = None):
        self.primary = primary
        self.fallback = fallback
        self.failures = 0
        self.errors: list[str] = [primary_error] if primary_error else []

    @property
    def name(self) -> str:
        return self.primary.name if self.primary else self.fallback.name

    def judge(self, item: dict) -> Judgment:
        if self.primary and self.failures < 3:
            try:
                result = self.primary.judge(item)
                self.failures = 0
                return result
            except JudgeError as e:
                self.failures += 1
                self.errors.append(str(e))
                log.warning("%s，改用關鍵字判讀", e)
        return self.fallback.judge(item)


def create_judge(settings: Settings, on_claude_call=None) -> FallbackJudge:
    cfg = settings.filters["news"]
    event_types = list(cfg["negative_event_types"])
    rules = RuleJudge(cfg)
    provider = (os.environ.get("AI_PROVIDER") or "ollama").strip().lower()
    ai_cfg = settings.sources.get("ai", {})
    try:
        if provider == "claude":
            model = os.environ.get("ANTHROPIC_MODEL") or ai_cfg.get("claude_model", "claude-opus-5-5")
            primary: NewsJudge | None = ClaudeJudge(model, event_types, on_call=on_claude_call)
        elif provider == "ollama":
            primary = OllamaJudge(os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434",
                                  os.environ.get("OLLAMA_MODEL") or "", event_types,
                                  timeout=ai_cfg.get("ollama_timeout_seconds", 120))
        elif provider in ("rules", "none"):
            primary = None
        else:
            raise JudgeError(f"AI_PROVIDER 必須是 ollama、claude 或 rules，目前為 {provider}")
    except JudgeError as e:
        log.warning("%s，改用關鍵字判讀", e)
        return FallbackJudge(None, rules, str(e))
    return FallbackJudge(primary, rules)
