"""資料來源共用工具：HTTP 重試、數字與民國日期轉換、統一資料格式。"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """外部資料來源錯誤。"""


def to_number(value: Any) -> float | None:
    """將 '1,234.5'、'--'、'' 等字串轉成數字；無法轉換回傳 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("+", "")
    if text in {"", "-", "--", "---", "N/A", "X", "除權息", "除息", "除權"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    n = to_number(value)
    return None if n is None else round(n)


def parse_tw_date(value: Any) -> date | None:
    """支援 '1141107'、'114/11/07'、'2025-11-07'、'20251107'。"""
    if value is None:
        return None
    text = str(value).strip().replace("/", "").replace("-", "")
    if not text.isdigit():
        return None
    if len(text) == 8:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    if len(text) == 7:  # 民國年
        return date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:]))
    return None


def pick(row: dict[str, Any], *keys: str) -> Any:
    """依序嘗試多個欄位名稱，回傳第一個存在的值（因應官方欄位改名）。"""
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


@dataclass
class HttpClient:
    timeout: float = 30
    max_retries: int = 4
    backoff_base: float = 2
    user_agent: str = "tw-stock-tracker/0.1"
    sleep: Callable[[float], None] = time.sleep
    transport: httpx.BaseTransport | None = None
    _client: httpx.Client | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_settings(cls, http_cfg: dict[str, Any]) -> HttpClient:
        return cls(
            timeout=http_cfg.get("timeout_seconds", 30),
            max_retries=http_cfg.get("max_retries", 4),
            backoff_base=http_cfg.get("backoff_base_seconds", 2),
            user_agent=http_cfg.get("user_agent", "tw-stock-tracker/0.1"),
        )

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
                transport=self.transport,
                follow_redirects=True,
            )
        return self._client

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self._get(url, params, lambda r: r.json())

    def get_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        return self._get(url, params, lambda r: r.text)

    def post_json(self, url: str, payload: dict[str, Any]) -> Any:
        """POST JSON 並解析 JSON 回應（公開資訊觀測站新版 API）。"""
        return self._get(url, None, lambda r: r.json(), payload=payload)

    def _get(self, url: str, params: dict[str, Any] | None, parse: Callable[[httpx.Response], Any],
             payload: dict[str, Any] | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = (self.client.post(url, json=payload) if payload is not None
                        else self.client.get(url, params=params))
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise SourceError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                return parse(resp)  # 解析失敗（ValueError）也重試
            except (httpx.HTTPError, SourceError, ValueError) as e:
                last_error = e
                if attempt < self.max_retries:
                    wait = self.backoff_base * (2**attempt)
                    log.warning("請求失敗（%s），%.0f 秒後重試：%s", e, wait, url)
                    self.sleep(wait)
        raise SourceError(f"請求失敗：{url}（{last_error}）")
