from __future__ import annotations

import pytest

from data_insight.config import Settings
from data_insight.service import AgentService


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings.load()


@pytest.fixture(scope="session")
def service(settings: Settings) -> AgentService:
    return AgentService(settings, mode="offline")
