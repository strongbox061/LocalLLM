"""Early Open WebUI response timer prototype.

Tracks elapsed time for LLM completion requests and logs
slow responses to stderr.
"""

import time
from typing import Optional


class ResponseTimer:
    """Measure and report latency of a single LLM call."""

    def __init__(self, slow_threshold_ms: int = 5000) -> None:
        self._threshold_ms = slow_threshold_ms
        self._start: Optional[float] = None
        self._elapsed_ms: Optional[float] = None

    def start(self) -> None:
        self._start = time.monotonic()

    def stop(self) -> float:
        if self._start is None:
            raise RuntimeError("Timer not started")
        self._elapsed_ms = (time.monotonic() - self._start) * 1000
        return self._elapsed_ms

    def __enter__(self) -> "ResponseTimer":
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        elapsed = self.stop()
        if elapsed > self._threshold_ms:
            print(
                f"[timer] Slow response: {elapsed:.0f}ms "
                f"(threshold: {self._threshold_ms}ms)",
                flush=True,
            )


def measure_completion(prompt: str) -> dict:
    """Stub: simulate an LLM completion and return its timing."""
    timer = ResponseTimer(slow_threshold_ms=3000)
    with timer:
        # Placeholder — real call would hit the Open WebUI API
        result = {"text": "", "latency_ms": timer._elapsed_ms or 0}
    return result
