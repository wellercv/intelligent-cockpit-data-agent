import json

import pytest

from data_insight.evaluation import AgentEvaluator
from data_insight.judge import JudgeResult

pytestmark = pytest.mark.requires_business_data


def test_evaluation_baseline_and_regression_detection(service, tmp_path):
    dataset = tmp_path / "smoke.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "case_id": "overview-smoke",
                    "question": "英语准确率是多少？",
                    "expected_tools": ["get_metrics"],
                    "expected_intent": "metric_analysis",
                    "expected_entities": {
                        "languages": ["English"],
                        "metrics": ["accuracy"],
                    },
                    "answer_contains": ["94.02"],
                    "min_sources": 1,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    first = AgentEvaluator(service, dataset).run(
        baseline_path=baseline, update_baseline=True
    )
    assert first["pass_rate"] == 1.0
    assert first["intent_accuracy"] == 1.0
    assert first["entity_accuracy"] == 1.0
    assert first["p95_ms"] >= first["p50_ms"] >= 0
    assert baseline.exists()
    baseline_payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert baseline_payload["p50_ms"] == first["p50_ms"]
    assert baseline_payload["p95_ms"] == first["p95_ms"]

    second = AgentEvaluator(service, dataset).run(baseline_path=baseline)
    assert second["regressions"] == []
    assert second["baseline"]["pass_rate"] == 1.0

    degraded = dict(second)
    degraded["tool_accuracy"] = 0.8
    degraded["intent_accuracy"] = 0.8
    degraded["entity_accuracy"] = 0.8
    regressions = AgentEvaluator._detect_regressions(degraded, second["baseline"])
    assert any("tool_accuracy" in item for item in regressions)
    assert any("intent_accuracy" in item for item in regressions)
    assert any("entity_accuracy" in item for item in regressions)


def test_evaluation_scores_structured_entities(service, tmp_path):
    dataset = tmp_path / "structured-understanding.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "case_id": "english-accuracy",
                    "question": "English 的 accuracy 是多少？",
                    "label_source": "synthetic_template",
                    "template_id": "metric-language-accuracy",
                    "tags": ["metric", "English"],
                    "expected_tools": ["get_metrics"],
                    "expected_intent": "metric_analysis",
                    "expected_entities": {
                        "languages": ["English"],
                        "metric": "accuracy",
                    },
                    "min_sources": 1,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = AgentEvaluator(service, dataset).run()

    assert report["intent_accuracy"] == 1.0
    assert report["entity_accuracy"] == 1.0
    assert report["dataset_provenance"] == {
        "label_sources": {"synthetic_template": 1},
        "template_cases": 1,
        "intent_labeled": 1,
        "entity_labeled": 1,
    }
    assert report["results"][0]["intent_match"] is True
    assert report["results"][0]["entity_match"] is True
    assert report["results"][0]["details"]["template_id"] == "metric-language-accuracy"


def test_failed_evaluation_cannot_update_baseline(service, tmp_path):
    dataset = tmp_path / "failed.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "case_id": "intent-mismatch",
                    "question": "英语准确率是多少？",
                    "expected_intent": "out_of_scope",
                    "min_sources": 1,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    baseline = tmp_path / "failed-baseline.json"

    with pytest.raises(ValueError, match="Refusing to update baseline"):
        AgentEvaluator(service, dataset).run(
            baseline_path=baseline,
            update_baseline=True,
        )

    assert baseline.exists() is False
    failed_run = next(
        item
        for item in service.state_store.list_evaluation_runs(20)
        if item["dataset"] == "failed.json"
    )
    assert failed_run["status"] == "FAILED"


def test_evaluation_reports_optional_llm_judge(service, tmp_path):
    class FakeJudge:
        def evaluate(self, **kwargs):
            return JudgeResult(
                relevance=5,
                completeness=4,
                clarity=5,
                actionability=4,
                evidence_use=5,
                rationale="Grounded and complete.",
            )

    dataset = tmp_path / "judge.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "case_id": "overview-judge",
                    "question": "当前一共接入多少条数据？",
                    "expected_tools": ["dataset_overview"],
                    "answer_contains": ["92,301"],
                    "min_sources": 1,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = AgentEvaluator(service, dataset, judge=FakeJudge()).run()

    assert report["judge"]["average"] == 4.6
    assert report["judge"]["evidence_use"] == 5.0
    assert report["judge"]["policy_violations"] == 0
    persisted = service.state_store.get_evaluation_run(report["run_id"])
    assert persisted is not None
    assert persisted["dataset"] == "judge.json"
    assert persisted["mode"] == "offline"
    assert persisted["status"] == "COMPLETED"
    assert persisted["summary"]["judge"]["average"] == 4.6
    assert persisted["judgments"][0]["result"]["success"] is True


def test_evaluation_does_not_pollute_investigation_memory(service, tmp_path):
    dataset = tmp_path / "memory-isolation.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "case_id": "memory-isolation",
                    "question": "当前一共接入多少条数据？",
                    "expected_tools": ["dataset_overview"],
                    "answer_contains": ["92,301"],
                    "min_sources": 1,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = service.investigation_memory.health()["records"]

    AgentEvaluator(service, dataset).run()

    assert service.investigation_memory.health()["records"] == before


def test_judge_failure_does_not_discard_deterministic_report(service, tmp_path):
    class FailingJudge:
        def evaluate(self, **kwargs):
            raise RuntimeError("judge unavailable")

    dataset = tmp_path / "judge-failure.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "case_id": "judge-failure",
                    "question": "当前一共接入多少条数据？",
                    "expected_tools": ["dataset_overview"],
                    "answer_contains": ["92,301"],
                    "min_sources": 1,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = AgentEvaluator(service, dataset, judge=FailingJudge()).run()

    assert report["pass_rate"] == 1.0
    assert report["judge"] is None
    assert report["judge_errors"][0]["error_type"] == "RuntimeError"


def test_low_judge_score_cannot_update_baseline(service, tmp_path):
    class LowJudge:
        def evaluate(self, **kwargs):
            return JudgeResult(
                relevance=2,
                completeness=2,
                clarity=3,
                actionability=1,
                evidence_use=5,
                rationale="Safe but not useful.",
            )

    dataset = tmp_path / "low-judge.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "case_id": "low-judge",
                    "question": "当前一共接入多少条数据？",
                    "expected_tools": ["dataset_overview"],
                    "answer_contains": ["92,301"],
                    "min_sources": 1,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    baseline = tmp_path / "low-judge-baseline.json"

    with pytest.raises(ValueError, match="Refusing to update baseline"):
        AgentEvaluator(service, dataset, judge=LowJudge()).run(
            baseline_path=baseline,
            update_baseline=True,
        )

    assert baseline.exists() is False


def test_evaluation_records_case_error_and_continues(service, tmp_path, monkeypatch):
    dataset = tmp_path / "case-error.json"
    dataset.write_text(
        json.dumps(
            [
                {"case_id": "fails", "question": "first", "min_sources": 0},
                {"case_id": "passes", "question": "second", "min_sources": 0},
            ]
        ),
        encoding="utf-8",
    )
    original_ask = service.ask

    def flaky_ask(question, **kwargs):
        if question == "first":
            raise RuntimeError("temporary planner failure")
        return original_ask("你能做什么？", **kwargs)

    monkeypatch.setattr(service, "ask", flaky_ask)

    report = AgentEvaluator(service, dataset).run()

    assert report["total"] == 2
    assert report["passed"] == 1
    assert report["results"][0]["details"]["error_type"] == "RuntimeError"
    assert report["results"][1]["passed"] is True
    assert "p95_latency_ms" in report["llm_usage"]
    persisted = service.state_store.get_evaluation_run(report["run_id"])
    assert persisted["status"] == "COMPLETED"


def test_new_evaluation_aborts_stale_running_record(service):
    store = service.state_store
    store.start_evaluation_run("stale-run", "stale.json", "azure")
    with store._connect() as connection:
        connection.execute(
            "UPDATE evaluation_runs SET started_at=? WHERE run_id=?",
            ["2000-01-01T00:00:00+00:00", "stale-run"],
        )
    store.start_evaluation_run("active-run", "active.json", "azure")
    store.start_evaluation_run("current-run", "current.json", "offline")

    assert store.get_evaluation_run("stale-run")["status"] == "ABORTED"
    assert store.get_evaluation_run("active-run")["status"] == "RUNNING"
    assert store.get_evaluation_run("current-run")["status"] == "RUNNING"

    store.finish_evaluation_run("active-run", {"passed": 0})
    store.finish_evaluation_run("current-run", {"passed": 0})
