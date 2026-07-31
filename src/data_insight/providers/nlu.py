"""Provider for the offline NLU re-evaluation Excel report."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from data_insight.config import Settings
from data_insight.nlu_warehouse import NLUIngestReport, NLUReportWarehouse
from data_insight.providers.base import DataProvider
from data_insight.schemas import SourceRef, ToolCall, ToolObservation


class NLUEvaluationProvider(DataProvider):
    name = "nlu_evaluation"

    def __init__(
        self,
        settings: Settings,
        report_path: Path | None = None,
    ) -> None:
        self.settings = settings
        self.report_path = (report_path or settings.nlu_report_path)
        self.warehouse: NLUReportWarehouse | None = None
        self.ingest_report: NLUIngestReport | None = None
        self.error: str | None = None
        if self.report_path is None:
            self.error = "NLU report path is not configured"
            return
        self.report_path = self.report_path.resolve()
        if not self.report_path.exists():
            self.error = f"NLU report does not exist: {self.report_path}"
            return
        try:
            self.warehouse = NLUReportWarehouse(
                self.report_path,
                settings.project_root / "data" / "nlu_report.duckdb",
            )
            self.ingest_report = self.warehouse.ensure_ready()
        except Exception as error:
            self.error = f"{type(error).__name__}: {error}"

    @property
    def ready(self) -> bool:
        return self.warehouse is not None and self.ingest_report is not None

    def tool_catalog(self) -> list[dict[str, Any]]:
        languages = self.ingest_report.languages if self.ingest_report else []
        domains = self.ingest_report.domains if self.ingest_report else []
        return [
            self._spec(
                "nlu_report_overview",
                "NLU report totals, label-corrected exact-match accuracy, model errors, and source scope",
                {},
            ),
            self._spec(
                "nlu_compare_accuracy",
                "Compare label-corrected NLU exact-match accuracy by language or domain",
                {
                    "dimension": {
                        "type": "string",
                        "enum": ["language", "domain"],
                    },
                    "order": {
                        "type": "string",
                        "enum": ["worst", "best"],
                    },
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                required=["dimension"],
            ),
            self._spec(
                "nlu_error_breakdown",
                "Aggregate the model-error subset by error type, language, domain, or expected intent",
                {
                    "group_by": {
                        "type": "string",
                        "enum": ["error_type", "language", "domain", "intent"],
                    },
                    "language": {"type": "string", "enum": languages},
                    "domain": {"type": "string", "enum": domains},
                    "error_type": {
                        "type": "string",
                        "enum": [
                            "language",
                            "domain",
                            "intent",
                            "slots",
                            "parse_failure",
                        ],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                required=["group_by"],
            ),
            self._spec(
                "search_nlu_errors",
                "Search the 11,885-row NLU model-error subset by text and structured filters",
                {
                    "query": {"type": "string"},
                    "language": {"type": "string", "enum": languages},
                    "domain": {"type": "string", "enum": domains},
                    "error_type": {
                        "type": "string",
                        "enum": [
                            "language",
                            "domain",
                            "intent",
                            "slots",
                            "parse_failure",
                        ],
                    },
                    "intent": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            ),
            self._spec(
                "get_nlu_error_detail",
                "Get one NLU model error by stable error ID",
                {"error_id": {"type": "string"}},
                required=["error_id"],
            ),
            self._spec(
                "nlu_label_quality",
                "Inspect NLU language-naming and numeric-slot label issues without modifying the report",
                {
                    "issue_kind": {
                        "type": "string",
                        "enum": ["language_naming", "numeric_slot_type"],
                    },
                    "language": {"type": "string", "enum": languages},
                    "domain": {"type": "string", "enum": domains},
                    "changed_slot": {
                        "type": "string",
                        "enum": ["language", "ratio", "exact_grade"],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            ),
        ]

    def execute(self, call: ToolCall) -> ToolObservation:
        if not self.ready:
            return ToolObservation(
                call_id=call.call_id,
                tool_name=call.name,
                success=False,
                error=self.error or "NLU report provider is unavailable",
            )
        handler = getattr(self, f"_tool_{call.name}", None)
        if handler is None:
            return ToolObservation(
                call_id=call.call_id,
                tool_name=call.name,
                success=False,
                error=f"Unknown NLU tool: {call.name}",
            )
        try:
            return handler(call)
        except (KeyError, TypeError, ValueError) as error:
            return ToolObservation(
                call_id=call.call_id,
                tool_name=call.name,
                success=False,
                error=str(error),
            )

    def health(self) -> dict[str, Any]:
        if not self.ready:
            return {
                "provider": self.name,
                "ready": False,
                "optional": True,
                "source": self._source_path(),
                "error": self.error,
            }
        assert self.ingest_report is not None
        return {
            "provider": self.name,
            "ready": True,
            "optional": True,
            "source": self._source_path(),
            "samples": self.ingest_report.sample_count,
            "model_errors": self.ingest_report.model_error_count,
            "corrected_accuracy_pct": self.ingest_report.corrected_accuracy_pct,
            "languages": self.ingest_report.languages,
            "domains": self.ingest_report.domains,
            "intents": self.ingest_report.intent_count,
            "source_files": self.ingest_report.source_files,
            "scope": "summary plus label issues and model-error details",
        }

    def reload(self) -> None:
        if self.warehouse is None:
            raise ValueError(self.error or "NLU report provider is unavailable")
        self.ingest_report = self.warehouse.rebuild()

    def governance_records(self) -> list[dict[str, Any]]:
        warehouse = self._require_warehouse()
        records: list[dict[str, Any]] = []
        with warehouse.connect(read_only=True) as connection:
            language_rows = warehouse.rows_as_dicts(
                connection.execute(
                    """
                    SELECT source_file, language, domain,
                           sum(affected_count) affected_count
                    FROM nlu_label_issues
                    WHERE issue_kind='language_naming'
                    GROUP BY source_file, language, domain
                    ORDER BY source_file
                    """
                )
            )
            numeric_rows = warehouse.rows_as_dicts(
                connection.execute(
                    """
                    SELECT source_file, language, domain, intent, changed_slot,
                           sum(affected_count) affected_count
                    FROM nlu_label_issues
                    WHERE issue_kind='numeric_slot_type'
                    GROUP BY source_file, language, domain, intent, changed_slot
                    ORDER BY source_file, changed_slot
                    """
                )
            )
            parse_rows = warehouse.rows_as_dicts(
                connection.execute(
                    """
                    SELECT source_file, language, expected_domain AS "domain",
                           count(*) affected_count
                    FROM nlu_model_errors
                    WHERE error_type='parse_failure'
                    GROUP BY source_file, language, expected_domain
                    ORDER BY source_file
                    """
                )
            )
        for item in language_rows:
            records.append(
                self._governance_record(
                    "LANGUAGE_NAMING_MISMATCH",
                    item,
                    "Expected language label uses Arabic while the protocol/model uses Saudi_Arabic.",
                )
            )
        for item in numeric_rows:
            records.append(
                self._governance_record(
                    "NUMERIC_SLOT_TYPE_MISMATCH",
                    item,
                    f"Numeric slot `{item['changed_slot']}` is labeled as a string.",
                )
            )
        for item in parse_rows:
            records.append(
                self._governance_record(
                    "PREDICTION_JSON_PARSE_FAILURE",
                    item,
                    "Model prediction is not valid JSON.",
                )
            )
        assert self.ingest_report is not None
        for rule_id, count, detail in (
            (
                "DUPLICATE_LABEL_DETAIL",
                self.ingest_report.duplicate_label_rows,
                "The numeric-label detail sheet contains exact duplicate rows.",
            ),
            (
                "DUPLICATE_MODEL_ERROR_DETAIL",
                self.ingest_report.duplicate_error_rows,
                "The model-error detail sheet contains exact duplicate rows.",
            ),
        ):
            if count:
                records.append(
                    self._governance_record(
                        rule_id,
                        {
                            "source_file": self._source_path(),
                            "language": None,
                            "domain": None,
                            "affected_count": count,
                        },
                        detail,
                    )
                )
        return records

    def _tool_nlu_report_overview(self, call: ToolCall) -> ToolObservation:
        warehouse = self._require_warehouse()
        assert self.ingest_report is not None
        with warehouse.connect(read_only=True) as connection:
            metadata = {
                row[0]: json.loads(row[1])
                for row in connection.execute(
                    "SELECT key, value_json FROM nlu_report_meta"
                ).fetchall()
            }
            by_language = warehouse.rows_as_dicts(
                connection.execute(
                    """
                    SELECT name AS "language", total, model_errors,
                           raw_accuracy_pct, corrected_accuracy_pct,
                           improvement_pct
                    FROM nlu_dimension_metrics
                    WHERE dimension='language'
                    ORDER BY corrected_accuracy_pct
                    """
                )
            )
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            data={
                "sample_count": self.ingest_report.sample_count,
                "raw_correct": self.ingest_report.raw_correct,
                "raw_accuracy_pct": self.ingest_report.raw_accuracy_pct,
                "corrected_correct": self.ingest_report.corrected_correct,
                "corrected_accuracy_pct": self.ingest_report.corrected_accuracy_pct,
                "model_errors": self.ingest_report.model_error_count,
                "numeric_label_issues": self.ingest_report.numeric_label_issue_count,
                "language_naming_affected": self.ingest_report.language_naming_affected,
                "parse_failures": self.ingest_report.parse_failure_count,
                "languages": self.ingest_report.languages,
                "domains": self.ingest_report.domains,
                "intent_count": self.ingest_report.intent_count,
                "source_files": self.ingest_report.source_files,
                "model": metadata.get("model"),
                "protocol": metadata.get("protocol"),
                "by_language": by_language,
                "source_scope": "nlu_evaluation_report",
            },
            sources=[self._source()],
            warnings=self._scope_warnings(),
        )

    def _tool_nlu_compare_accuracy(self, call: ToolCall) -> ToolObservation:
        warehouse = self._require_warehouse()
        dimension = str(call.arguments.get("dimension", ""))
        if dimension not in {"language", "domain"}:
            raise ValueError("dimension must be language or domain")
        order = str(call.arguments.get("order", "worst"))
        if order not in {"worst", "best"}:
            raise ValueError("order must be worst or best")
        limit = max(1, min(int(call.arguments.get("limit", 20)), 20))
        direction = "ASC" if order == "worst" else "DESC"
        names = call.arguments.get("names") or []
        if isinstance(names, str):
            names = [names]
        clauses = ["dimension=?"]
        params: list[Any] = [dimension]
        if names:
            clauses.append(f"name IN ({','.join('?' for _ in names)})")
            params.extend(names)
        with warehouse.connect(read_only=True) as connection:
            rows = warehouse.rows_as_dicts(
                connection.execute(
                    f"""
                    SELECT name AS "{dimension}", total, corrected_correct, model_errors,
                           raw_accuracy_pct, corrected_accuracy_pct, improvement_pct
                    FROM nlu_dimension_metrics
                    WHERE {" AND ".join(clauses)}
                    ORDER BY corrected_accuracy_pct {direction}, name
                    LIMIT ?
                    """,
                    [*params, limit],
                )
            )
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            rows=rows,
            data={
                "dimension": dimension,
                "order": order,
                "count": len(rows),
                "metric": "corrected_exact_match_accuracy",
                "source_scope": "nlu_evaluation_report",
            },
            sources=[self._source()],
            warnings=self._scope_warnings(),
        )

    def _tool_nlu_error_breakdown(self, call: ToolCall) -> ToolObservation:
        warehouse = self._require_warehouse()
        group_by = str(call.arguments.get("group_by", ""))
        expressions = {
            "error_type": "error_type",
            "language": "language",
            "domain": "expected_domain",
            "intent": "expected_intent",
        }
        if group_by not in expressions:
            raise ValueError("group_by must be error_type, language, domain, or intent")
        where, params = self._error_filters(call.arguments)
        limit = max(1, min(int(call.arguments.get("limit", 50)), 200))
        expression = expressions[group_by]
        with warehouse.connect(read_only=True) as connection:
            rows = warehouse.rows_as_dicts(
                connection.execute(
                    f"""
                    SELECT {expression} AS "{group_by}", count(*) count,
                           round(100.0 * count(*) / sum(count(*)) OVER (), 2) share_pct
                    FROM nlu_model_errors {where}
                    GROUP BY {expression}
                    ORDER BY count DESC, {expression}
                    LIMIT ?
                    """,
                    [*params, limit],
                )
            )
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            rows=rows,
            data={
                "group_by": group_by,
                "count": len(rows),
                "subset": "model_errors_only",
                "source_scope": "nlu_evaluation_report",
            },
            sources=[self._source()],
            warnings=self._scope_warnings(),
        )

    def _tool_search_nlu_errors(self, call: ToolCall) -> ToolObservation:
        warehouse = self._require_warehouse()
        where, params = self._error_filters(call.arguments, include_query=True)
        limit = max(1, min(int(call.arguments.get("limit", 20)), 100))
        with warehouse.connect(read_only=True) as connection:
            total = connection.execute(
                f"SELECT count(*) FROM nlu_model_errors {where}",
                params,
            ).fetchone()[0]
            rows = warehouse.rows_as_dicts(
                connection.execute(
                    f"""
                    SELECT error_id, source_file, language, query_text, error_type,
                           expected_domain, expected_intent,
                           predicted_domain, predicted_intent, predicted_parse_ok
                    FROM nlu_model_errors {where}
                    ORDER BY language, source_file, source_row
                    LIMIT ?
                    """,
                    [*params, limit],
                )
            )
        warnings = self._scope_warnings()
        if total > limit:
            warnings.append(f"Matched {total} NLU errors; returned the first {limit}.")
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            rows=rows,
            data={
                "total_matches": total,
                "returned": len(rows),
                "subset": "model_errors_only",
                "source_scope": "nlu_evaluation_report",
            },
            sources=[self._source()],
            warnings=warnings,
        )

    def _tool_get_nlu_error_detail(self, call: ToolCall) -> ToolObservation:
        warehouse = self._require_warehouse()
        error_id = str(call.arguments.get("error_id", "")).strip().upper()
        if not error_id:
            raise ValueError("error_id is required")
        with warehouse.connect(read_only=True) as connection:
            rows = warehouse.rows_as_dicts(
                connection.execute(
                    "SELECT * FROM nlu_model_errors WHERE upper(error_id)=?",
                    [error_id],
                )
            )
        if not rows:
            raise KeyError(f"NLU error not found: {error_id}")
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            data={**rows[0], "source_scope": "nlu_evaluation_report"},
            sources=[self._source()],
            warnings=self._scope_warnings(),
        )

    def _tool_nlu_label_quality(self, call: ToolCall) -> ToolObservation:
        warehouse = self._require_warehouse()
        clauses: list[str] = []
        params: list[Any] = []
        for argument, column in (
            ("issue_kind", "issue_kind"),
            ("language", "language"),
            ("domain", "domain"),
            ("changed_slot", "changed_slot"),
        ):
            value = call.arguments.get(argument)
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        limit = max(1, min(int(call.arguments.get("limit", 20)), 100))
        with warehouse.connect(read_only=True) as connection:
            total, affected = connection.execute(
                f"SELECT count(*), coalesce(sum(affected_count), 0) FROM nlu_label_issues {where}",
                params,
            ).fetchone()
            rows = warehouse.rows_as_dicts(
                connection.execute(
                    f"""
                    SELECT issue_id, issue_kind, source_file, language, domain,
                           intent, changed_slot, affected_count, recommendation
                    FROM nlu_label_issues {where}
                    ORDER BY affected_count DESC, source_file, source_row
                    LIMIT ?
                    """,
                    [*params, limit],
                )
            )
        assert self.ingest_report is not None
        warnings = self._scope_warnings()
        if total > limit:
            warnings.append(f"Matched {total} label-issue rows; returned the first {limit}.")
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            rows=rows,
            data={
                "detail_rows": total,
                "affected_samples": affected,
                "numeric_label_issues": self.ingest_report.numeric_label_issue_count,
                "language_naming_affected": self.ingest_report.language_naming_affected,
                "duplicate_label_rows": self.ingest_report.duplicate_label_rows,
                "duplicate_error_rows": self.ingest_report.duplicate_error_rows,
                "prediction_parse_failures": self.ingest_report.parse_failure_count,
                "source_scope": "nlu_evaluation_report",
            },
            sources=[self._source()],
            warnings=warnings,
        )

    def _error_filters(
        self,
        arguments: dict[str, Any],
        *,
        include_query: bool = False,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if include_query and (query := str(arguments.get("query", "")).strip()):
            clauses.append(
                "(query_text ILIKE ? OR source_file ILIKE ? OR expected_intent ILIKE ?)"
            )
            params.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        for argument, column in (
            ("language", "language"),
            ("domain", "expected_domain"),
            ("error_type", "error_type"),
            ("intent", "expected_intent"),
        ):
            value = arguments.get(argument)
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        return ("WHERE " + " AND ".join(clauses) if clauses else ""), params

    def _require_warehouse(self) -> NLUReportWarehouse:
        if self.warehouse is None or self.ingest_report is None:
            raise ValueError(self.error or "NLU report provider is unavailable")
        return self.warehouse

    def _source(self) -> SourceRef:
        return SourceRef(
            source_id="nlu-offline-report",
            label="Offline NLU re-evaluation report",
            path=self._source_path(),
            scope="nlu_evaluation_report",
        )

    def _source_path(self) -> str:
        if self.report_path is None:
            return "unconfigured NLU report"
        return self.settings.display_path(self.report_path)

    @staticmethod
    def _scope_warnings() -> list[str]:
        return [
            "The Excel source contains complete summary metrics, label-issue details, "
            "and model-error details, but not every correct NLU sample.",
            "NLU accuracy is exact-match accuracy after the report's documented label corrections.",
        ]

    def _governance_record(
        self,
        rule_id: str,
        item: dict[str, Any],
        detail: str,
    ) -> dict[str, Any]:
        fingerprint = "\x1f".join(
            str(item.get(key) or "")
            for key in ("source_file", "language", "domain", "intent", "changed_slot")
        )
        finding_id = "NLU-GOV-" + hashlib.sha256(
            f"{rule_id}\x1f{fingerprint}".encode("utf-8")
        ).hexdigest()[:12]
        return {
            "finding_id": finding_id,
            "rule_id": rule_id,
            "source_file": str(item.get("source_file") or self._source_path()),
            "language": item.get("language"),
            "domain": item.get("domain"),
            "intent": item.get("intent"),
            "affected_count": int(item.get("affected_count") or 0),
            "detail": detail,
            "_source_path": self._source_path(),
        }

    @staticmethod
    def _spec(
        name: str,
        description: str,
        properties: dict[str, Any],
        required: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        }