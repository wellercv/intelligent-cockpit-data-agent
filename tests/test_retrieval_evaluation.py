from __future__ import annotations

from pathlib import Path

import pytest

from data_insight.retrieval_evaluation import RetrievalEvaluator

pytestmark = pytest.mark.requires_business_data


def test_retrieval_evaluation_reports_recall_and_mrr(service):
    dataset = (
        Path(__file__).resolve().parents[1]
        / "eval"
        / "datasets"
        / "retrieval_questions.json"
    )

    report = RetrievalEvaluator(service.knowledge_provider.index, dataset).run()

    assert report["total"] == 18
    assert report["passed"] == 18
    assert report["recall_at_k"] == 1.0
    assert report["mrr"] == 1.0
    assert report["passed_threshold"] is True