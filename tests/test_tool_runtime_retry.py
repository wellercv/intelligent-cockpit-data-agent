from __future__ import annotations

import threading

from data_insight.schemas import ToolCall, ToolObservation
from data_insight.tool_runtime import ToolRuntime


class FlakyProvider:
    calls = 0

    def execute(self, call):
        self.calls += 1
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            success=self.calls > 1,
            error=None if self.calls > 1 else "temporary failure",
        )


def test_tool_runtime_retries_transient_failure():
    provider = FlakyProvider()
    runtime = ToolRuntime(provider, max_retries=1)
    try:
        observation = runtime.execute(ToolCall(name="flaky"))

        assert observation.success is True
        assert provider.calls == 2
        assert runtime.summary()["flaky"]["retries"] == 1
        assert any("retried 1 time" in item for item in observation.warnings)
    finally:
        runtime.close()


def test_tool_runtime_returns_timeout_observation():
    release = threading.Event()

    class SlowProvider:
        def execute(self, call):
            release.wait(0.2)
            return ToolObservation(
                call_id=call.call_id,
                tool_name=call.name,
            )

    runtime = ToolRuntime(SlowProvider(), timeout_s=0.01, max_retries=0)
    try:
        observation = runtime.execute(ToolCall(name="slow"))

        assert observation.success is False
        assert observation.error == "Tool timed out after 0.0s"
        assert runtime.summary()["slow"]["consecutive_failures"] == 1
    finally:
        release.set()
        runtime.close()


def test_tool_runtime_opens_and_recovers_circuit():
    now = [100.0]

    class RecoveringProvider:
        calls = 0
        available = False

        def execute(self, call):
            self.calls += 1
            return ToolObservation(
                call_id=call.call_id,
                tool_name=call.name,
                success=self.available,
                error=None if self.available else "unavailable",
            )

    provider = RecoveringProvider()
    runtime = ToolRuntime(
        provider,
        max_retries=0,
        failure_threshold=2,
        circuit_cooldown_s=10,
        clock=lambda: now[0],
    )
    try:
        assert runtime.execute(ToolCall(name="unstable")).success is False
        assert runtime.execute(ToolCall(name="unstable")).success is False
        blocked = runtime.execute(ToolCall(name="unstable"))
        assert blocked.error == "Tool circuit breaker is open; retry later."
        assert provider.calls == 2
        assert runtime.summary()["unstable"]["circuit_state"] == "open"

        provider.available = True
        now[0] = 111.0
        recovered = runtime.execute(ToolCall(name="unstable"))
        assert recovered.success is True
        assert provider.calls == 3
        assert runtime.summary()["unstable"]["circuit_state"] == "closed"
    finally:
        runtime.close()