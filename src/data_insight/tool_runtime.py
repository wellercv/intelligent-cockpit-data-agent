"""Reliable tool execution with cache, timeout, circuit breaker, and metrics."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from typing import Callable, Dict, Tuple

from data_insight.providers.base import DataProvider
from data_insight.schemas import ToolCall, ToolObservation


@dataclass
class ToolStats:
    total: int = 0
    success: int = 0
    total_ms: float = 0.0
    consecutive_failures: int = 0
    circuit_open_until: float = 0.0
    retries: int = 0

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 1.0

    @property
    def average_ms(self) -> float:
        return self.total_ms / self.total if self.total else 0.0


class ToolRuntime:
    def __init__(
        self,
        provider: DataProvider,
        timeout_s: float = 10.0,
        cache_ttl_s: float = 60.0,
        max_retries: int = 1,
        failure_threshold: int = 3,
        circuit_cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be greater than zero")
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least one")
        self.provider = provider
        self.timeout_s = timeout_s
        self.cache_ttl_s = cache_ttl_s
        self.max_retries = max(0, max_retries)
        self.failure_threshold = failure_threshold
        self.circuit_cooldown_s = max(0.0, circuit_cooldown_s)
        self._clock = clock
        self._cache: Dict[str, Tuple[float, ToolObservation]] = {}
        self._stats: Dict[str, ToolStats] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="data-tool")

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)

    def execute(self, call: ToolCall) -> ToolObservation:
        key = self._cache_key(call)
        now = self._clock()
        with self._lock:
            cached = self._cache.get(key)
            stats = self._stats.setdefault(call.name, ToolStats())
            if cached and cached[0] > now:
                cached_result = cached[1].model_copy(deep=True)
                cached_result.call_id = call.call_id
                cached_result.cached = True
                return cached_result
            if stats.circuit_open_until > now:
                return ToolObservation(call_id=call.call_id, tool_name=call.name, success=False, error="Tool circuit breaker is open; retry later.")

        started = time.perf_counter()
        result: ToolObservation | None = None
        retries_used = 0
        for attempt in range(self.max_retries + 1):
            future = self._pool.submit(self.provider.execute, call)
            try:
                result = future.result(timeout=self.timeout_s)
            except TimeoutError:
                future.cancel()
                result = ToolObservation(
                    call_id=call.call_id,
                    tool_name=call.name,
                    success=False,
                    error=f"Tool timed out after {self.timeout_s:.1f}s",
                )
            except Exception as error:
                result = ToolObservation(
                    call_id=call.call_id,
                    tool_name=call.name,
                    success=False,
                    error=f"Tool execution failed: {error}",
                )
            if result.success or attempt >= self.max_retries:
                break
            retries_used += 1
        assert result is not None
        elapsed_ms = (time.perf_counter() - started) * 1000
        result.elapsed_ms = round(elapsed_ms, 2)
        if retries_used:
            result.warnings = [
                *result.warnings,
                f"Tool retried {retries_used} time(s) after a transient failure.",
            ]

        with self._lock:
            stats.total += 1
            stats.total_ms += elapsed_ms
            stats.retries += retries_used
            if result.success:
                stats.success += 1
                stats.consecutive_failures = 0
                self._cache[key] = (now + self.cache_ttl_s, result.model_copy(deep=True))
            else:
                stats.consecutive_failures += 1
                if stats.consecutive_failures >= self.failure_threshold:
                    stats.circuit_open_until = now + self.circuit_cooldown_s
        return result

    def summary(self) -> Dict[str, Dict[str, float | int | str]]:
        now = self._clock()
        with self._lock:
            return {
                name: {
                    "total": stats.total,
                    "success_rate": round(stats.success_rate, 4),
                    "average_ms": round(stats.average_ms, 2),
                    "consecutive_failures": stats.consecutive_failures,
                    "retries": stats.retries,
                    "circuit_state": "open" if stats.circuit_open_until > now else "closed",
                }
                for name, stats in self._stats.items()
            }

    @staticmethod
    def _cache_key(call: ToolCall) -> str:
        payload = json.dumps({"name": call.name, "arguments": call.arguments}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
