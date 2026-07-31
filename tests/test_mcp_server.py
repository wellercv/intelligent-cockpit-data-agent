from __future__ import annotations

import asyncio

import pytest

from data_insight.mcp_server import create_mcp_server

pytestmark = pytest.mark.requires_business_data


def test_mcp_exposes_approved_tools_and_executes_overview(service):
    server = create_mcp_server(service)

    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}

    assert "dataset_overview" in names
    assert "search_knowledge" in names
    assert "governance_scan" in names
    assert "nlu_report_overview" in names
    assert "search_nlu_errors" in names
    assert "publish_version" not in names
    assert "rollback_version" not in names

    content, structured = asyncio.run(server.call_tool("dataset_overview", {}))

    assert content
    result = structured["result"]
    assert result["success"] is True
    assert result["data"]["total"] == 92301

    _, nlu_structured = asyncio.run(server.call_tool("nlu_report_overview", {}))
    nlu_result = nlu_structured["result"]
    assert nlu_result["success"] is True
    assert nlu_result["data"]["sample_count"] == 104897