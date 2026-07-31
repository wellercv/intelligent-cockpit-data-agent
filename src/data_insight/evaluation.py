"""Deterministic end-to-end agent evaluation and regression-ready reports."""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
from uuid import uuid4

from data_insight.judge import AnswerJudge
from data_insight.retrieval_evaluation import RetrievalEvaluator
from data_insight.schemas import EvaluationCase, EvaluationResult
from data_insight.service import AgentService


class AgentEvaluator:
    def __init__(
        self,
        service: AgentService,
        dataset_path: Path,
        judge: AnswerJudge | None = None,
    ) -> None:
        self.service = service
        self.dataset_path = dataset_path
        self.judge = judge

    def load_cases(self) -> List[EvaluationCase]:
        payload = json.loads(self.dataset_path.read_text(encoding="utf-8"))
        return [EvaluationCase.model_validate(item) for item in payload]

    def run(
        self,
        baseline_path: Path | None = None,
        update_baseline: bool = False,
    ) -> Dict:
        run_id = f"eval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"
        results: List[EvaluationResult] = []
        judge_results: List[Dict] = []
        judge_errors: List[Dict] = []
        cases = self.load_cases()
        llm_start_id = self.service.state_store.latest_llm_call_id()
        self.service.state_store.start_evaluation_run(
            run_id,
            self.dataset_path.name,
            self.service.mode,
        )
        for case in cases:
            started = time.perf_counter()
            try:
                answer = self.service.ask(
                    case.question,
                    use_investigation_memory=False,
                )
            except Exception as error:
                elapsed_ms = (time.perf_counter() - started) * 1000
                result = EvaluationResult(
                    case_id=case.case_id,
                    passed=False,
                    tool_match=False,
                    agent_match=False,
                    intent_match=False,
                    entity_match=False,
                    answer_match=False,
                    citation_match=False,
                    elapsed_ms=round(elapsed_ms, 2),
                    details={
                        "error_type": type(error).__name__,
                        "error": str(error),
                        "label_source": case.label_source,
                        "template_id": case.template_id,
                        "tags": case.tags,
                    },
                )
                results.append(result)
                self.service.state_store.save_evaluation(
                    run_id,
                    case.case_id,
                    result.model_dump(mode="json"),
                )
                continue
            elapsed_ms = (time.perf_counter() - started) * 1000
            tool_match = set(case.expected_tools).issubset(set(answer.tools_used))
            agent_match = set(case.expected_agents).issubset(set(answer.agents_used))
            trace = self.service.trace(answer.trace_id)
            plan_event = next(
                (event for event in trace if event.event_type == "plan"),
                None,
            )
            understanding = (
                plan_event.payload.get("understanding", {}) if plan_event else {}
            )
            intent_match = (
                case.expected_intent is None
                or understanding.get("intent") == case.expected_intent
            )
            entity_match = all(
                understanding.get(name) == value
                for name, value in case.expected_entities.items()
            )
            answer_match = all(value in answer.answer_markdown for value in case.answer_contains)
            citation_match = len(answer.sources) >= case.min_sources and answer.grounded
            result = EvaluationResult(
                case_id=case.case_id,
                passed=(
                    tool_match
                    and agent_match
                    and intent_match
                    and entity_match
                    and answer_match
                    and citation_match
                ),
                tool_match=tool_match,
                agent_match=agent_match,
                intent_match=intent_match,
                entity_match=entity_match,
                answer_match=answer_match,
                citation_match=citation_match,
                elapsed_ms=round(elapsed_ms, 2),
                details={
                    "expected_tools": case.expected_tools,
                    "actual_tools": answer.tools_used,
                    "expected_agents": case.expected_agents,
                    "actual_agents": answer.agents_used,
                    "expected_intent": case.expected_intent,
                    "actual_understanding": understanding,
                    "expected_entities": case.expected_entities,
                    "label_source": case.label_source,
                    "template_id": case.template_id,
                    "tags": case.tags,
                    "missing_text": [value for value in case.answer_contains if value not in answer.answer_markdown],
                    "source_count": len(answer.sources),
                    "trace_id": answer.trace_id,
                },
            )
            results.append(result)
            if self.judge is not None:
                try:
                    judged = self.judge.evaluate(
                        question=case.question,
                        answer=answer.answer_markdown,
                        observations=answer.observations,
                        sources=answer.sources,
                    )
                    judge_results.append(
                        {
                            "case_id": case.case_id,
                            **judged.model_dump(),
                            "average": judged.average,
                        }
                    )
                    self.service.state_store.save_evaluation_judgment(
                        run_id,
                        case.case_id,
                        {"success": True, **judge_results[-1]},
                    )
                except Exception as error:
                    judge_errors.append(
                        {
                            "case_id": case.case_id,
                            "error_type": type(error).__name__,
                            "error": str(error),
                        }
                    )
                    self.service.state_store.save_evaluation_judgment(
                        run_id,
                        case.case_id,
                        {"success": False, **judge_errors[-1]},
                    )
            self.service.state_store.save_evaluation(run_id, case.case_id, result.model_dump(mode="json"))
        passed = sum(item.passed for item in results)
        latencies = [item.elapsed_ms for item in results]
        report = {
            "run_id": run_id,
            "dataset": self.dataset_path.name,
            "mode": self.service.mode,
            "total": len(results),
            "passed": passed,
            "pass_rate": round(passed / len(results), 4) if results else 0.0,
            "tool_accuracy": round(sum(item.tool_match for item in results) / len(results), 4) if results else 0.0,
            "agent_accuracy": round(sum(item.agent_match for item in results) / len(results), 4) if results else 0.0,
            "intent_accuracy": self._conditional_accuracy(
                results,
                cases,
                "intent_match",
                lambda item: item.expected_intent is not None,
            ),
            "entity_accuracy": self._conditional_accuracy(
                results,
                cases,
                "entity_match",
                lambda item: bool(item.expected_entities),
            ),
            "answer_accuracy": round(sum(item.answer_match for item in results) / len(results), 4) if results else 0.0,
            "citation_accuracy": round(sum(item.citation_match for item in results) / len(results), 4) if results else 0.0,
            "average_ms": round(sum(item.elapsed_ms for item in results) / len(results), 2) if results else 0.0,
            "p50_ms": self._percentile(latencies, 0.50),
            "p95_ms": self._percentile(latencies, 0.95),
            "dataset_provenance": {
                "label_sources": dict(Counter(item.label_source for item in cases)),
                "template_cases": sum(item.template_id is not None for item in cases),
                "intent_labeled": sum(item.expected_intent is not None for item in cases),
                "entity_labeled": sum(bool(item.expected_entities) for item in cases),
            },
            "results": [item.model_dump(mode="json") for item in results],
            "judge": self._judge_summary(judge_results),
            "judge_results": judge_results,
            "judge_errors": judge_errors,
            "llm_usage": self.service.state_store.llm_usage_summary(
                self.service.llm_config.input_price_per_million,
                self.service.llm_config.output_price_per_million,
                after_id=llm_start_id,
            ),
        }
        retrieval_dataset = self.dataset_path.parent / "retrieval_questions.json"
        report["retrieval"] = (
            RetrievalEvaluator(
                self.service.knowledge_provider.index,
                retrieval_dataset,
            ).run()
            if retrieval_dataset.exists()
            else None
        )
        baseline = self._load_baseline(baseline_path)
        report["baseline"] = baseline
        report["regressions"] = self._detect_regressions(report, baseline)
        report["recommendations"] = self._recommendations(report)
        if update_baseline and baseline_path is not None:
            retrieval_ready = not report.get("retrieval") or report["retrieval"].get(
                "passed_threshold", False
            )
            if (
                report["pass_rate"] != 1.0
                or report["regressions"]
                or report["judge_errors"]
                or (
                    report.get("judge") is not None
                    and (
                        report["judge"]["average"] < 4.0
                        or report["judge"]["policy_violations"] > 0
                    )
                )
                or not retrieval_ready
            ):
                report["baseline_updated"] = None
                self.service.state_store.finish_evaluation_run(
                    run_id,
                    self._persisted_summary(report),
                    status="FAILED",
                )
                raise ValueError(
                    "Refusing to update baseline from a failed, regressed, "
                    "or incomplete evaluation run"
                )
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_payload = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "dataset": self.dataset_path.name,
                "pass_rate": report["pass_rate"],
                "tool_accuracy": report["tool_accuracy"],
                "agent_accuracy": report["agent_accuracy"],
                "intent_accuracy": report["intent_accuracy"],
                "entity_accuracy": report["entity_accuracy"],
                "answer_accuracy": report["answer_accuracy"],
                "citation_accuracy": report["citation_accuracy"],
                "average_ms": report["average_ms"],
                "p50_ms": report["p50_ms"],
                "p95_ms": report["p95_ms"],
                "retrieval_recall_at_k": (
                    report["retrieval"]["recall_at_k"]
                    if report.get("retrieval")
                    else None
                ),
                "retrieval_mrr": (
                    report["retrieval"]["mrr"]
                    if report.get("retrieval")
                    else None
                ),
                "judge_average": (
                    report["judge"]["average"] if report.get("judge") else None
                ),
            }
            baseline_path.write_text(
                json.dumps(baseline_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            report["baseline_updated"] = str(baseline_path)
        else:
            report["baseline_updated"] = None
        self.service.state_store.finish_evaluation_run(
            run_id,
            self._persisted_summary(report),
        )
        return report

    @staticmethod
    def _persisted_summary(report: Dict) -> Dict:
        return {
            key: value
            for key, value in report.items()
            if key not in {"results", "judge_results"}
        }

    @staticmethod
    def _judge_summary(results: List[Dict]) -> Dict | None:
        if not results:
            return None
        dimensions = (
            "relevance",
            "completeness",
            "clarity",
            "actionability",
            "evidence_use",
            "average",
        )
        return {
            "cases": len(results),
            **{
                name: round(
                    sum(float(item[name]) for item in results) / len(results), 4
                )
                for name in dimensions
            },
            "policy_violations": sum(
                len(item["policy_violations"]) for item in results
            ),
        }

    @staticmethod
    def _conditional_accuracy(
        results: List[EvaluationResult],
        cases: List[EvaluationCase],
        field_name: str,
        predicate,
    ) -> float | None:
        applicable = [
            result
            for result, case in zip(results, cases)
            if predicate(case)
        ]
        if not applicable:
            return None
        return round(
            sum(bool(getattr(item, field_name)) for item in applicable)
            / len(applicable),
            4,
        )

    @staticmethod
    def _percentile(values: List[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(float(value) for value in values)
        position = (len(ordered) - 1) * percentile
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return round(
            ordered[lower] * (1 - weight) + ordered[upper] * weight,
            2,
        )

    @staticmethod
    def _load_baseline(path: Path | None) -> Dict | None:
        if path is None or not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _detect_regressions(current: Dict, baseline: Dict | None) -> List[str]:
        if not baseline:
            return []
        regressions = []
        for metric in (
            "pass_rate",
            "tool_accuracy",
            "agent_accuracy",
            "intent_accuracy",
            "entity_accuracy",
            "answer_accuracy",
            "citation_accuracy",
            "retrieval_recall_at_k",
            "retrieval_mrr",
            "judge_average",
        ):
            previous = float(baseline.get(metric, 0.0) or 0.0)
            if metric == "retrieval_recall_at_k":
                value = float(
                    (current.get("retrieval") or {}).get("recall_at_k", 0.0)
                )
            elif metric == "retrieval_mrr":
                value = float((current.get("retrieval") or {}).get("mrr", 0.0))
            elif metric == "judge_average":
                value = float((current.get("judge") or {}).get("average", 0.0))
            else:
                value = float(current.get(metric, 0.0) or 0.0)
            if previous > 0 and (value - previous) / previous < -0.05:
                regressions.append(
                    f"{metric}: {previous:.3f} -> {value:.3f} "
                    f"({abs((value - previous) / previous):.1%} regression)"
                )
        return regressions

    @staticmethod
    def _recommendations(report: Dict) -> List[str]:
        recommendations = []
        if report.get("judge") and report["judge"]["average"] < 4.0:
            recommendations.append(
                "LLM-as-Judge average is below 4.0; inspect relevance, completeness, "
                "clarity, actionability, and evidence use before promotion."
            )
        if report.get("judge") and report["judge"]["policy_violations"]:
            recommendations.append(
                "LLM-as-Judge reported policy violations; do not promote this version."
            )
        if report["tool_accuracy"] < 0.95:
            recommendations.append(
                "Tool accuracy is below 95%; inspect failed routing and tool arguments."
            )
        if report["agent_accuracy"] < 0.95:
            recommendations.append(
                "Agent accuracy is below 95%; inspect supervisor delegation and specialist boundaries."
            )
        if report.get("intent_accuracy") is not None and report["intent_accuracy"] < 0.95:
            recommendations.append(
                "Intent accuracy is below 95%; inspect structured task understanding."
            )
        if report.get("entity_accuracy") is not None and report["entity_accuracy"] < 0.95:
            recommendations.append(
                "Entity accuracy is below 95%; inspect language/domain/case extraction."
            )
        if report["answer_accuracy"] < 0.95:
            recommendations.append(
                "Answer accuracy is below 95%; inspect Composer evidence coverage."
            )
        if report["citation_accuracy"] < 1.0:
            recommendations.append(
                "Citation accuracy is below 100%; inspect Grounding and source paths."
            )
        if report.get("retrieval") and not report["retrieval"].get(
            "passed_threshold", False
        ):
            recommendations.append(
                "Hybrid RAG is below the Recall@K/MRR promotion threshold; "
                "inspect query rewrite, chunks, fusion, and reranking."
            )
        if report.get("regressions"):
            recommendations.append(
                "A metric regressed by more than 5%; do not promote this Agent version."
            )
        return recommendations
