"""Composite provider that exposes tools from independent provider plugins."""

from __future__ import annotations

from typing import Any, Dict, List

from data_insight.providers.base import DataProvider
from data_insight.schemas import ToolCall, ToolObservation


class CompositeProvider(DataProvider):
    name = "composite"

    def __init__(self, providers: List[DataProvider]) -> None:
        self.providers = providers
        self._tool_routes: Dict[str, DataProvider] = {}
        for provider in providers:
            for tool in provider.tool_catalog():
                name = tool["name"]
                if name in self._tool_routes:
                    owner = self._tool_routes[name]
                    raise ValueError(
                        f"Duplicate tool `{name}` from providers "
                        f"`{owner.name}` and `{provider.name}`"
                    )
                self._tool_routes[name] = provider

    def tool_catalog(self) -> List[Dict[str, Any]]:
        return [tool for provider in self.providers for tool in provider.tool_catalog()]

    def execute(self, call: ToolCall) -> ToolObservation:
        provider = self._tool_routes.get(call.name)
        if provider is None:
            return ToolObservation(call_id=call.call_id, tool_name=call.name, success=False, error=f"No provider owns tool: {call.name}")
        return provider.execute(call)

    def health(self) -> Dict[str, Any]:
        states = [provider.health() for provider in self.providers]
        ready = all(
            item.get("ready") or item.get("optional")
            for item in states
        )
        return {"provider": self.name, "ready": ready, "providers": states, "tools": sorted(self._tool_routes)}

    def close(self) -> None:
        for provider in self.providers:
            close = getattr(provider, "close", None)
            if callable(close):
                close()
