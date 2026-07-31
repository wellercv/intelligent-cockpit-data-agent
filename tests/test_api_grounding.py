import pytest
from fastapi.testclient import TestClient

from data_insight.api import create_app
from data_insight.grounding import GroundingVerifier
from data_insight.schemas import SourceRef, ToolCall, ToolObservation


@pytest.mark.requires_business_data
def test_grounding_rejects_unsupported_number(service, settings):
    observation = service.provider.execute(ToolCall(name="get_metrics", arguments={"language": "English"}))
    grounded, unsupported, _ = GroundingVerifier(settings).verify(
        "英语表现如何？", "英语共有 123456 条。", [observation]
    )
    assert grounded is False
    assert "123456" in unsupported


def test_grounding_handles_multiline_knowledge_numbers(settings):
    observation = ToolObservation(
        call_id="knowledge-list",
        tool_name="search_knowledge",
        rows=[{"content": "5. Monitor the run.\n6. Validate the output."}],
    )

    grounded, unsupported, _ = GroundingVerifier(settings).verify(
        "What is the workflow?",
        "Step 6 validates the output.",
        [observation],
    )
    assert grounded is True
    assert unsupported == []

    grounded, unsupported, _ = GroundingVerifier(settings).verify(
        "What is the workflow?",
        "Step 99 validates the output.",
        [observation],
    )
    assert grounded is False
    assert unsupported == ["99"]


@pytest.mark.requires_business_data
def test_grounding_rejects_unsupported_business_entity(service, settings):
    observation = service.provider.execute(
        ToolCall(name="get_metrics", arguments={"language": "English"})
    )

    grounded, unsupported, warnings = GroundingVerifier(settings).verify(
        "英语表现如何？",
        "英语表现已查询，但 German 是最高风险语言。",
        [observation],
    )

    assert grounded is False
    assert unsupported == []
    assert "Unsupported entity in answer: German" in warnings


def test_grounding_rejects_mismatched_source_scope(settings):
    observation = ToolObservation(
        call_id="scope-check",
        tool_name="search_knowledge",
        sources=[
            SourceRef(
                source_id="bad-scope",
                label="Metric definitions",
                path="knowledge/metrics.md",
                scope="csv_cases",
            )
        ],
    )

    grounded, _, warnings = GroundingVerifier(settings).verify(
        "准确率如何定义？",
        "准确率定义已查询。",
        [observation],
    )

    assert grounded is False
    assert any("Source scope mismatch" in warning for warning in warnings)


@pytest.mark.requires_business_data
def test_grounding_resolves_governance_state_from_project_root(service, settings):
    observation = service.provider.execute(
        ToolCall(name="list_change_requests", arguments={"provider": "multilingual_asr"})
    )

    grounded, unsupported, warnings = GroundingVerifier(settings).verify(
        "查看治理变更状态", "治理状态已查询。", [observation]
    )

    assert grounded is True
    assert unsupported == []
    assert warnings == []


@pytest.mark.requires_business_data
def test_fastapi_health_and_chat():
    client = TestClient(create_app())
    paths = client.get("/openapi.json").json()["paths"]
    assert "/governance/changes/{change_id}/confirm" in paths
    assert "/governance/changes/{change_id}/approve" not in paths
    assert "/governance/changes/{change_id}/reject" not in paths
    assert "/nlu/overview" in paths
    assert "/nlu/errors" in paths
    assert "/nlu/errors/{error_id}" in paths
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["provider"]["providers"][0]["cases"] == 92301
    llm_status = client.get("/llm/status")
    assert llm_status.status_code == 200
    llm_payload = llm_status.json()
    assert llm_payload["auth_mode"] in {"key", "entra"}
    assert "api_key" not in llm_payload
    assert "token" not in llm_payload

    nlu = client.get("/nlu/overview")
    assert nlu.status_code == 200
    assert nlu.json()["data"]["sample_count"] == 104897

    response = client.post("/chat", json={"question": "七种语言中哪个错误率最高？"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["grounded"] is True
    assert payload["tools_used"] == ["rank_dimensions"]
    assert "Portuguese" in payload["answer_markdown"]

    governance = client.post("/governance/scan")
    assert governance.status_code == 200
    governance_payload = governance.json()
    assert governance_payload["data"]["finding_count"] == 44
    assert governance_payload["tool_name"] == "governance_scan"

    issues = client.get("/governance/issues", params={"status": "OPEN"})
    assert issues.status_code == 200
    assert any(
        item["finding"]["rule_id"] == "BLANK_NOT_ALLOWED"
        for item in issues.json()
    )

    retrieval = client.get("/eval/retrieval")
    assert retrieval.status_code == 200
    assert retrieval.json()["passed"] == 18
    assert retrieval.json()["recall_at_k"] == 1.0

    memory = client.get(
        "/memory/investigations",
        params={"query": "七种语言哪个错误率最高", "limit": 3},
    )
    assert memory.status_code == 200
    assert isinstance(memory.json(), list)
