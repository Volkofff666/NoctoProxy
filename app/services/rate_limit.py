from __future__ import annotations

import time


class InMemoryRateLimiter:
    def __init__(self, cooldown_seconds: int):
        self.cooldown_seconds = cooldown_seconds
        self._last_called: dict[int, float] = {}

    def allowed(self, user_id: int) -> tuple[bool, int]:
        now = time.monotonic()
        prev = self._last_called.get(user_id)
        if prev is None:
            self._last_called[user_id] = now
            return True, 0

        elapsed = now - prev
        if elapsed >= self.cooldown_seconds:
            self._last_called[user_id] = now
            return True, 0

        retry_after = int(self.cooldown_seconds - elapsed) + 1
        return False, retry_after
