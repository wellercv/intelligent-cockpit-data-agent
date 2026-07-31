from __future__ import annotations

import json
from pathlib import Path

from data_insight.schemas import EvaluationCase


def test_synthetic_silver_dataset_is_explicit_and_well_formed():
    path = (
        Path(__file__).parents[1]
        / "eval"
        / "datasets"
        / "synthetic_understanding.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = [EvaluationCase.model_validate(item) for item in payload]

    assert len(cases) == 62
    assert len({item.case_id for item in cases}) == len(cases)
    assert all(item.label_source == "synthetic_template" for item in cases)
    assert all(item.template_id for item in cases)
    assert {item.expected_intent for item in cases} == {
        "metric_analysis",
        "case_investigation",
        "knowledge_qa",
        "data_governance",
        "mixed",
        "out_of_scope",
    }
    assert sum(bool(item.expected_entities) for item in cases) >= 45