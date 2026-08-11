from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Request


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool


@dataclass(frozen=True)
class ProtectionPolicy:
    scope: str
    label: str
    per_minute: int
    per_day: int
    global_scope: bool = False


PUBLIC_BETA_POLICIES: dict[str, ProtectionPolicy] = {
    "event": ProtectionPolicy("event", "行为事件", 120, 2_000),
    "game-review": ProtectionPolicy("game-review", "整盘分析", 12, 100),
    "deepseek": ProtectionPolicy("deepseek", "DeepSeek 分析", 30, 300),
    "deepseek-global": ProtectionPolicy(
        "deepseek-global",
        "DeepSeek 全站突发保护",
        60,
        86_400,
        global_scope=True,
    ),
}


class RequestProtector:
    """In-memory abuse protection; client addresses are never persisted."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._minute_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._day_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    @staticmethod
    def client_key(request: Request) -> str:
        # Uvicorn resolves trusted proxy headers before building request.client.
        # Never trust a raw client-supplied forwarding header here.
        address = request.client.host if request.client else "unknown"
        return hashlib.sha256(address.encode("utf-8")).hexdigest()

    def allow(
        self,
        scope: str,
        client_key: str,
        *,
        per_minute: int,
        per_day: int,
    ) -> LimitDecision:
        now = time.monotonic()
        minute_key = (scope, client_key)
        day_key = (scope, client_key)
        with self._lock:
            minute = self._minute_hits[minute_key]
            day = self._day_hits[day_key]
            while minute and minute[0] <= now - 60:
                minute.popleft()
            while day and day[0] <= now - 86_400:
                day.popleft()
            if len(minute) >= per_minute or len(day) >= per_day:
                return LimitDecision(allowed=False)
            minute.append(now)
            day.append(now)
        return LimitDecision(allowed=True)

    def allow_global(self, scope: str, *, per_minute: int) -> LimitDecision:
        return self.allow(scope, "global", per_minute=per_minute, per_day=per_minute * 1_440)
