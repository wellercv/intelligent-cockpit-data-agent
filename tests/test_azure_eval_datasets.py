from __future__ import annotations

import json
from pathlib import Path

from data_insight.schemas import EvaluationCase


def test_azure_evaluation_subsets_are_valid_and_unique():
    root = Path(__file__).parents[1] / "eval" / "datasets"
    expected_sizes = {
        "azure_overview.json": 1,
        "azure_failed_cases.json": 4,
        "azure_smoke.json": 6,
    }

    for name, expected_size in expected_sizes.items():
        payload = json.loads((root / name).read_text(encoding="utf-8"))
        cases = [EvaluationCase.model_validate(item) for item in payload]
        assert len(cases) == expected_size
        assert len({item.case_id for item in cases}) == expected_size

    smoke = [
        EvaluationCase.model_validate(item)
        for item in json.loads(
            (root / "azure_smoke.json").read_text(encoding="utf-8")
        )
    ]
    assert {item.expected_intent for item in smoke} == {
        "metric_analysis",
        "case_investigation",
        "knowledge_qa",
        "data_governance",
        "mixed",
        "out_of_scope",
    }