"""Token-per-minute throttle for hosted LLM APIs.

WHY THIS EXISTS -- measured, not assumed.
Querying Groq's rate-limit headers during development showed the binding
constraint is NOT requests but tokens per minute:

    x-ratelimit-limit-requests: 1000      (per day)   -- not binding
    x-ratelimit-limit-tokens:   8000      (per MINUTE) -- binding
    x-ratelimit-reset-tokens:   547ms

An Arm C (RAG) review costs roughly 2,200 prompt+completion tokens, so the
sustainable rate is about 3-4 reviews per minute. Firing calls back-to-back
trips HTTP 429 immediately, and retry-with-backoff then burns 60s per attempt --
which is how two 3-repeat runs were killed before writing any results.

Retrying is the wrong primary tool here: the correct fix is to not exceed the
budget in the first place. This throttle maintains a sliding one-minute window
of spent tokens and sleeps just long enough before a call that would overflow
it. Retry remains as a backstop for bursts we mis-estimate.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque


class TokenBucket:
    """Sliding-window tokens-per-minute limiter.

    Thread-safe so a future parallel runner cannot corrupt the window, though
    the current runner is sequential.
    """

    def __init__(self, tokens_per_minute: int = 8000, window_s: float = 60.0,
                 safety_margin: float = 0.85, verbose: bool = True) -> None:
        # Spend only a fraction of the stated limit: our pre-call token estimate
        # is approximate, and the completion length is unknown until it arrives.
        self.budget = int(tokens_per_minute * safety_margin)
        self.window_s = window_s
        self.verbose = verbose
        self._events: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0][0] >= self.window_s:
            self._events.popleft()

    def _spent(self, now: float) -> int:
        self._prune(now)
        return sum(n for _, n in self._events)

    def acquire(self, estimated_tokens: int) -> float:
        """Block until `estimated_tokens` fit in the window. Returns seconds slept."""
        slept = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                spent = self._spent(now)
                if spent + estimated_tokens <= self.budget or not self._events:
                    # Reserve optimistically; `record` corrects it afterwards.
                    self._events.append((now, estimated_tokens))
                    return slept
                oldest_ts = self._events[0][0]
                wait = max(0.2, self.window_s - (now - oldest_ts) + 0.3)

            if self.verbose:
                print(f"      throttle: {spent}/{self.budget} tok in window, "
                      f"sleeping {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
            slept += wait

    def record(self, estimated_tokens: int, actual_tokens: int) -> None:
        """Replace the optimistic reservation with the real cost."""
        if actual_tokens <= 0:
            return
        with self._lock:
            for i in range(len(self._events) - 1, -1, -1):
                ts, n = self._events[i]
                if n == estimated_tokens:
                    self._events[i] = (ts, actual_tokens)
                    return


def estimate_tokens(system: str, user: str, max_completion: int = 600) -> int:
    """Rough pre-call token estimate.

    ~4 characters per token is the usual English/code approximation. We add the
    full `max_completion` because the response length is unknown in advance, and
    under-reserving is what causes a 429.
    """
    return (len(system) + len(user)) // 4 + max_completion
