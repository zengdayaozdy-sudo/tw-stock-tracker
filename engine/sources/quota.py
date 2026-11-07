"""API 額度管理：每小時計數，接近上限時暫停到下一個整點，不中斷整批。"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from engine.config import TZ

log = logging.getLogger(__name__)


def _hour_start(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0)


@dataclass
class QuotaTracker:
    provider: str
    hourly_limit: int
    safety_margin: float = 0.9
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(TZ))
    sleep: Callable[[float], None] = time.sleep
    on_record: Callable[[str, datetime, int], None] | None = None  # 寫入 api_usage
    max_wait_seconds: float = 3700
    _hour: datetime | None = field(default=None, init=False)
    _calls: int = field(default=0, init=False)

    @property
    def budget(self) -> int:
        return max(1, int(self.hourly_limit * self.safety_margin))

    @property
    def calls_this_hour(self) -> int:
        self._roll()
        return self._calls

    def preload(self, calls: int) -> None:
        """從資料庫載入本小時已用次數（其他程序也可能在用）。"""
        self._roll()
        self._calls = max(self._calls, calls)

    def _roll(self) -> None:
        hour = _hour_start(self.clock())
        if self._hour != hour:
            self._hour = hour
            self._calls = 0

    def acquire(self) -> None:
        self._roll()
        if self._calls >= self.budget:
            now = self.clock()
            wait = (_hour_start(now) + timedelta(hours=1) - now).total_seconds() + 5
            if wait > self.max_wait_seconds:
                raise RuntimeError(f"{self.provider} 額度已用完，等待時間過長")
            log.warning("%s 本小時額度已用 %d 次，暫停 %.0f 秒", self.provider, self._calls, wait)
            self.sleep(wait)
            self._roll()
        self._calls += 1
        if self.on_record:
            self.on_record(self.provider, self._hour, self._calls)
