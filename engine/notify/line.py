"""LINE Messaging API 推播（只推給 LINE_OWNER_USER_ID 本人，不做好友管理或群發）。

規格（官方 Messaging API reference，2026-11-08 確認）：
POST https://api.line.me/v2/bot/message/push，Authorization: Bearer {channel access token}，
單次最多 5 則訊息；X-Line-Retry-Key（UUID）讓重送不會重複推播，同一金鑰重送回 409 視為已送出。
未設定金鑰時不推播，只把內容輸出到記錄（方便本機測試與尚未設定 LINE 時）。
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)


class LineError(RuntimeError):
    """LINE 推播失敗。"""


def split_text(text: str, limit: int) -> list[str]:
    """超過上限的文字依換行切開；單行過長時硬切。"""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def pack_blocks(blocks: list[str], limit: int) -> list[str]:
    """把多個段落盡量合併成少數幾則訊息（段落之間空一行）。"""
    messages: list[str] = []
    current = ""
    for block in blocks:
        for piece in split_text(block.strip(), limit):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) > limit:
                messages.append(current)
                current = piece
            else:
                current = candidate
    if current:
        messages.append(current)
    return messages


@dataclass
class LineClient:
    token: str | None
    user_id: str | None
    cfg: dict
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep
    sent: list[list[str]] = field(default_factory=list)  # 本次執行送出的內容（測試與記錄用）

    @classmethod
    def from_env(cls, cfg: dict) -> LineClient:
        return cls(os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or None,
                   os.environ.get("LINE_OWNER_USER_ID") or None, cfg)

    @property
    def configured(self) -> bool:
        return bool(self.token and self.user_id)

    def push_texts(self, texts: list[str], notification: bool = True) -> int:
        """推播多則文字；每 5 則一次請求。回傳送出的訊息則數（未設定金鑰時為 0）。"""
        limit = self.cfg["max_text_chars"]
        chunks = [piece for t in texts for piece in split_text(t, limit) if piece.strip()]
        if not chunks:
            return 0
        if not self.configured:
            log.warning("未設定 LINE_CHANNEL_ACCESS_TOKEN／LINE_OWNER_USER_ID，以下內容未推播：\n%s",
                        "\n---\n".join(chunks))
            return 0
        per = self.cfg["max_messages_per_push"]
        for i in range(0, len(chunks), per):
            batch = chunks[i:i + per]
            self._push(batch, notification)
            self.sent.append(batch)
        return len(chunks)

    def _push(self, texts: list[str], notification: bool) -> None:
        body = {
            "to": self.user_id,
            "messages": [{"type": "text", "text": t} for t in texts],
            "notificationDisabled": not notification,
        }
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "X-Line-Retry-Key": str(uuid.uuid4()),  # 同一批次重送用同一把金鑰
        }
        last = ""
        with httpx.Client(timeout=self.cfg["timeout_seconds"], transport=self.transport) as client:
            for attempt in range(self.cfg["max_retries"] + 1):
                try:
                    resp = client.post(self.cfg["push_url"], json=body, headers=headers)
                except httpx.HTTPError as e:
                    last = str(e)
                else:
                    if resp.status_code == 200:
                        return
                    if resp.status_code == 409:  # 同一金鑰已被接受：先前那次其實送出了
                        return
                    last = f"HTTP {resp.status_code} {resp.text[:200]}"
                    if resp.status_code != 429 and resp.status_code < 500:
                        break  # 400／401／403 等重送也不會成功
                if attempt < self.cfg["max_retries"]:
                    self.sleep(self.cfg["backoff_base_seconds"] * 2 ** attempt)
        raise LineError(f"LINE 推播失敗：{last}")
