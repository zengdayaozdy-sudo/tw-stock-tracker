"""盤勢總結的文字敘述：AI 改寫（ollama／claude），失敗或 AI_PROVIDER=rules 時用規則組合。

AI 只能使用程式算出的事實與新聞標題，提示詞明確禁止加入資料以外的數字與推測；
規則版本永遠會先產生，AI 只是把同樣的內容寫得更通順。
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from engine.config import Settings

log = logging.getLogger(__name__)


class WriterError(RuntimeError):
    pass


def rule_narrative(headline: str | None, reasons: list[dict], posture: dict | None, max_reasons: int = 4) -> str:
    if not headline:
        return "今日加權指數資料尚未更新，無法產生盤勢總結"
    picked = [r["text"] for r in reasons if r["key"] != "news"][:max_reasons]
    news = next((r["text"] for r in reasons if r["key"] == "news"), None)
    parts = [headline + "。"]
    if picked:
        parts.append("主要原因：" + "；".join(picked) + "。")
    if news:
        parts.append(news + "。")
    if posture:
        parts.append(f"隔日方針：{posture['title']}，{posture['text']}。")
    return "".join(parts)


SYSTEM = (
    "你是台股盤後分析助手，替一位資深投資人寫當日盤勢總結。\n"
    "只能使用使用者提供的「事實」「原因」「新聞標題」「隔日方針」，不可加入任何未提供的數字、事件、人物或推測；"
    "新聞只能依標題轉述並保留出處，不可延伸細節。\n"
    "用繁體中文（台灣用語）寫成一段 {max_chars} 字以內的文字：先說今天怎麼走，再說最主要的二到三個原因（依重要性），"
    "最後一句說隔日方針。不要條列、不要標題、不要括號註記、不要行銷用語，數字照抄不要四捨五入成別的值。"
)


def build_prompt(headline: str, reasons: list[dict], news: list[dict], posture: dict | None,
                 max_chars: int) -> tuple[str, str]:
    lines = [f"事實：{headline}", "原因（依重要性）："]
    lines += [f"- {r['text']}" for r in reasons if r["key"] != "news"]
    if news:
        lines.append("新聞標題：")
        lines += [f"- {n['title']}（{n['source']}）" for n in news[:6]]
    if posture:
        lines.append(f"隔日方針：{posture['title']}，{posture['text']}")
    return SYSTEM.format(max_chars=max_chars), "\n".join(lines)


class OllamaWriter:
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float = 120, transport: httpx.BaseTransport | None = None):
        if not model:
            raise WriterError("未設定 OLLAMA_MODEL")
        self.url = base_url.rstrip("/") + "/api/chat"
        self.model = model
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def write(self, system: str, user: str) -> str:
        try:
            resp = self.client.post(self.url, json={
                "model": self.model, "stream": False, "options": {"temperature": 0.2},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            })
            resp.raise_for_status()
            return str(resp.json()["message"]["content"]).strip()
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise WriterError(f"Ollama 產生總結失敗：{e}") from e


class ClaudeWriter:
    name = "claude"

    def __init__(self, model: str, client: Any = None):
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise WriterError("未安裝 anthropic 套件（pip install -e \".[claude]\"）") from e
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise WriterError("未設定 ANTHROPIC_API_KEY")
            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def write(self, system: str, user: str) -> str:
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=1500,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low"},  # 改寫既有事實，不需要深度推理
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except Exception as e:
            raise WriterError(f"Claude 產生總結失敗：{e}") from e
        if resp.stop_reason == "refusal":
            raise WriterError("Claude 拒絕產生此則總結")
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if not text:
            raise WriterError(f"Claude 未回傳內容（stop_reason={resp.stop_reason}）")
        return text.strip()


def create_writer(settings: Settings):
    """依 AI_PROVIDER 建立；rules／none 或建立失敗回傳 None（使用規則文字）。"""
    provider = (os.environ.get("AI_PROVIDER") or "ollama").strip().lower()
    ai_cfg = settings.sources.get("ai", {})
    try:
        if provider == "claude":
            return ClaudeWriter(os.environ.get("ANTHROPIC_MODEL") or ai_cfg.get("claude_model", "claude-opus-5-5"))
        if provider == "ollama":
            return OllamaWriter(os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434",
                                os.environ.get("OLLAMA_MODEL") or "", ai_cfg.get("ollama_timeout_seconds", 120))
    except WriterError as e:
        log.warning("%s，改用規則文字", e)
    return None


def clean(text: str, max_chars: int) -> str:
    """去掉 AI 偶爾加上的標題、條列符號與換行；超過上限時在句號處截斷。"""
    lines = [ln.strip().lstrip("#-*・ ").strip() for ln in text.splitlines()]
    out = "".join(ln for ln in lines if ln)
    if len(out) > max_chars * 1.3:
        cut = out[: int(max_chars * 1.3)]
        out = cut[: cut.rfind("。") + 1] or cut
    return out


def write_narrative(writer, headline: str | None, reasons: list[dict], news: list[dict], posture: dict | None,
                    cfg: dict) -> tuple[str, str, str | None]:
    """回傳（敘述, generated_by, AI 錯誤訊息）。"""
    fallback = rule_narrative(headline, reasons, posture)
    if writer is None or not headline or not cfg.get("ai_narrative", True):
        return fallback, "rules", None
    system, user = build_prompt(headline, reasons, news, posture, cfg["narrative_max_chars"])
    try:
        text = clean(writer.write(system, user), cfg["narrative_max_chars"])
    except WriterError as e:
        log.warning("%s，改用規則文字", e)
        return fallback, "rules", str(e)
    if not text:
        return fallback, "rules", "AI 回傳空白"
    return text, writer.name, None
