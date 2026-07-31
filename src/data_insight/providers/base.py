"""Provider protocol for pluggable business data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from data_insight.schemas import ToolCall, ToolObservation


class DataProvider(ABC):
    name: str

    @abstractmethod
    def tool_catalog(self) -> List[Dict[str, Any]]:
        """Return LLM-facing tool definitions."""

    @abstractmethod
    def execute(self, call: ToolCall) -> ToolObservation:
        """Execute one validated provider tool call."""

    @abstractmethod
    def health(self) -> Dict[str, Any]:
        """Return provider readiness and dataset metadata."""
