"""Deterministic evaluation for Hybrid RAG retrieval quality."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from data_insight.retrieval import HybridKnowledgeIndex


class RetrievalCase(BaseModel):
    case_id: str
    query: str
    expected_paths: List[str] = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=20)


class RetrievalEvaluator:
    def __init__(
        self,
        index: HybridKnowledgeIndex,
        dataset_path: Path,
        min_recall_at_k: float = 0.9,
        min_mrr: float = 0.75,
    ) -> None:
        self.index = index
        self.dataset_path = dataset_path
        self.min_recall_at_k = min_recall_at_k
        self.min_mrr = min_mrr

    def run(self) -> Dict[str, Any]:
        cases = self._load_cases()
        results: List[Dict[str, Any]] = []
        for case in cases:
            rows = self.index.search(case.query, limit=case.k)
            paths = [str(row["path"]) for row in rows]
            relevant_ranks = [
                rank
                for rank, path in enumerate(paths, start=1)
                if path in set(case.expected_paths)
            ]
            recalled = len(set(paths) & set(case.expected_paths))
            recall = recalled / len(case.expected_paths)
            reciprocal_rank = 1.0 / min(relevant_ranks) if relevant_ranks else 0.0
            results.append(
                {
                    "case_id": case.case_id,
                    "query": case.query,
                    "expected_paths": case.expected_paths,
                    "actual_paths": paths,
                    "recall_at_k": round(recall, 4),
                    "reciprocal_rank": round(reciprocal_rank, 4),
                    "passed": recall == 1.0,
                }
            )
        total = len(results)
        recall_at_k = (
            round(sum(item["recall_at_k"] for item in results) / total, 4)
            if total
            else 0.0
        )
        mrr = (
            round(sum(item["reciprocal_rank"] for item in results) / total, 4)
            if total
            else 0.0
        )
        return {
            "dataset": self.dataset_path.name,
            "total": total,
            "passed": sum(item["passed"] for item in results),
            "recall_at_k": recall_at_k,
            "mrr": mrr,
            "thresholds": {
                "min_recall_at_k": self.min_recall_at_k,
                "min_mrr": self.min_mrr,
            },
            "passed_threshold": (
                total > 0
                and recall_at_k >= self.min_recall_at_k
                and mrr >= self.min_mrr
            ),
            "results": results,
        }

    def _load_cases(self) -> List[RetrievalCase]:
        payload = json.loads(self.dataset_path.read_text(encoding="utf-8"))
        return [RetrievalCase.model_validate(item) for item in payload]