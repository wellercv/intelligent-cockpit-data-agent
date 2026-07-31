import pytest

from data_insight.schemas import ToolCall
from data_insight.warehouse import ASRWarehouse

pytestmark = pytest.mark.requires_business_data


def test_full_multilingual_ingestion(settings):
    report = ASRWarehouse(settings).ensure_ready()
    assert report.case_count == 92301
    assert report.correct_count == 87182
    assert report.error_count == 5118
    assert report.unknown_count == 1
    assert report.summary_count == 42
    assert report.issue_count == 3
    assert report.languages == ["Arabic", "English", "French", "German", "Italian", "Portuguese", "Spanish"]
    assert len(report.domains) == 6


def test_provider_metrics_and_source_scope(service):
    overview = service.provider.execute(ToolCall(name="dataset_overview"))
    assert overview.success
    assert overview.data["errors"] == 5118
    assert overview.data["unknown_count"] == 1
    assert overview.data["accuracy_pct"] == 94.45

    comparison = service.provider.execute(
        ToolCall(name="compare_source_scopes", arguments={"language": "French", "domain": "carControl"})
    )
    assert comparison.rows[0]["csv_total"] == 4147
    assert comparison.rows[0]["json_total"] == 4178
    assert comparison.rows[0]["total_delta"] == -31


def test_tool_runtime_cache(service):
    first = service.runtime.execute(ToolCall(name="get_metrics", arguments={"language": "English"}))
    second = service.runtime.execute(ToolCall(name="get_metrics", arguments={"language": "English"}))
    assert first.success and second.success
    assert second.cached is True
