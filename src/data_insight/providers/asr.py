"""Multilingual ASR provider and deterministic analytical tools."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from data_insight.config import Settings
from data_insight.providers.base import DataProvider
from data_insight.schemas import SourceRef, ToolCall, ToolObservation
from data_insight.warehouse import ASRWarehouse


class MultilingualASRProvider(DataProvider):
    name = "multilingual_asr"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.warehouse = ASRWarehouse(settings)
        self.ingest_report = self.warehouse.ensure_ready()
        self.languages = self.ingest_report.languages
        self.domains = self.ingest_report.domains

    def tool_catalog(self) -> List[Dict[str, Any]]:
        return [
            self._spec("platform_capabilities", "Explain supported data questions and reject unsupported tasks", {}),
            self._spec("dataset_overview", "Dataset totals, languages, domains, source quality issues", {}),
            self._spec("get_metrics", "Metrics for optional language/domain filters", {
                "language": {"type": "string", "enum": self.languages},
                "domain": {"type": "string", "enum": self.domains},
                "source_scope": {"type": "string", "enum": ["csv_cases", "json_summary"]},
            }),
            self._spec("compare_languages", "Compare all or selected languages, optionally within one domain", {
                "languages": {"type": "array", "items": {"type": "string", "enum": self.languages}},
                "domain": {"type": "string", "enum": self.domains},
            }),
            self._spec("compare_domains", "Compare all domains, optionally within one language", {
                "language": {"type": "string", "enum": self.languages},
            }),
            self._spec("rank_dimensions", "Rank languages or domains by errors, error_rate, accuracy or total", {
                "dimension": {"type": "string", "enum": ["language", "domain"]},
                "metric": {"type": "string", "enum": ["errors", "error_rate", "accuracy", "total"]},
                "language": {"type": "string", "enum": self.languages},
                "domain": {"type": "string", "enum": self.domains},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            }, required=["dimension", "metric"]),
            self._spec("search_cases", "Search REF/HYP/case number with language, domain and result filters", {
                "query": {"type": "string"},
                "languages": {"type": "array", "items": {"type": "string", "enum": self.languages}},
                "domains": {"type": "array", "items": {"type": "string", "enum": self.domains}},
                "result": {"type": "string", "enum": ["all", "correct", "error", "unknown"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            }),
            self._spec("get_case_detail", "Get one exact case by stable case_id or ASR number plus scope", {
                "case_id": {"type": "string"},
                "case_no": {"type": "string"},
                "language": {"type": "string", "enum": self.languages},
                "domain": {"type": "string", "enum": self.domains},
            }),
            self._spec("compare_source_scopes", "Compare CSV case statistics with raw output JSON summaries", {
                "language": {"type": "string", "enum": self.languages},
                "domain": {"type": "string", "enum": self.domains},
            }),
            self._spec("data_quality", "List source inconsistencies and ingestion warnings", {
                "language": {"type": "string", "enum": self.languages},
                "severity": {"type": "string", "enum": ["warning", "error"]},
            }),
        ]

    def execute(self, call: ToolCall) -> ToolObservation:
        handler = getattr(self, f"_tool_{call.name}", None)
        if handler is None:
            return ToolObservation(call_id=call.call_id, tool_name=call.name, success=False, error=f"Unknown ASR tool: {call.name}")
        try:
            return handler(call)
        except (ValueError, TypeError) as error:
            return ToolObservation(call_id=call.call_id, tool_name=call.name, success=False, error=str(error))

    def health(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "ready": True,
            "synthetic_demo": self.settings.demo_mode,
            "cases": self.ingest_report.case_count,
            "correct": self.ingest_report.correct_count,
            "errors": self.ingest_report.error_count,
            "unknown": self.ingest_report.unknown_count,
            "summaries": self.ingest_report.summary_count,
            "quality_issues": self.ingest_report.issue_count,
            "languages": self.languages,
            "domains": self.domains,
        }

    def _tool_platform_capabilities(self, call: ToolCall) -> ToolObservation:
        examples = [
            "七种语言中哪个错误率最高？",
            "比较德语和法语 mediaControl 的错误率",
            "找出英语中包含 TuneIn 的错误案例",
            "法语 carControl 的 CSV 和 JSON 口径有什么差异？",
            "准确率指标是怎么计算的？",
            "NLU 修正标签口径后的整体准确率是多少？",
            "比较 ASR 和 NLU 的整体准确率及数据口径",
        ]
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            data={
                "supported": [
                    "multilingual ASR metrics and rankings",
                    "language/domain comparisons",
                    "case search and detail",
                    "CSV/JSON source-scope comparison",
                    "data-quality inspection",
                    "metric and data-scope knowledge",
                    "offline NLU report metrics, model errors, and label quality",
                ],
                "unsupported": [
                    "weather and general web knowledge",
                    "unrelated code generation",
                    "facts absent from registered providers",
                    "automatic TN triage or root-cause claims",
                ],
                "examples": examples,
            },
            sources=[
                SourceRef(
                    source_id="platform-scope",
                    label="Registered provider catalog",
                    path="config/sources.yaml",
                    scope="platform_configuration",
                )
            ],
            warnings=[
                "The request is outside the registered business-data scope; no unsupported fact was generated."
            ]
            + (
                ["Synthetic demo data is active; figures are not business results."]
                if self.settings.demo_mode
                else []
            ),
        )

    def _tool_dataset_overview(self, call: ToolCall) -> ToolObservation:
        with self.warehouse.connect(read_only=True) as connection:
            cursor = connection.execute(
                """
                SELECT language, count(*) total,
                       sum(CASE WHEN is_correct THEN 1 ELSE 0 END) correct,
                       sum(CASE WHEN result = 'error' THEN 1 ELSE 0 END) errors,
                       sum(CASE WHEN result = 'unknown' THEN 1 ELSE 0 END) unknown_count,
                       round(100.0 * avg(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END), 2) accuracy_pct
                FROM asr_cases GROUP BY language ORDER BY language
                """
            )
            by_language = self.warehouse.rows_as_dicts(cursor)
            total = sum(row["total"] for row in by_language)
            correct = sum(row["correct"] for row in by_language)
            errors = sum(row["errors"] for row in by_language)
            unknown = sum(row["unknown_count"] for row in by_language)
            issues = connection.execute("SELECT count(*) FROM data_quality_issues").fetchone()[0]
            source_rows = self._source_rows(connection, "csv_cases", None, None)
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            data={
                "total": total,
                "correct": correct,
                "errors": errors,
                "unknown_count": unknown,
                "accuracy_pct": round(correct / total * 100, 2) if total else 0.0,
                "languages": self.languages,
                "domains": self.domains,
                "language_count": len(self.languages),
                "domain_count": len(self.domains),
                "by_language": by_language,
                "quality_issue_count": issues,
                "source_scope": "csv_cases",
            },
            sources=self._sources_for_rows(source_rows),
            warnings=self._quality_warning(),
        )

    def _tool_get_metrics(self, call: ToolCall) -> ToolObservation:
        args = call.arguments
        language = self._language(args.get("language"))
        domain = self._domain(args.get("domain"))
        scope = args.get("source_scope", "csv_cases")
        if scope == "json_summary":
            table, correct_expression = "asr_summaries", "sum(correct)"
            filters, params = self._filters(language, domain)
            sql = f"""
                SELECT sum(total) total, {correct_expression} correct, sum(errors) errors,
                       round(100.0 * sum(correct) / nullif(sum(total), 0), 2) accuracy_pct
                FROM {table} {filters}
            """
        else:
            filters, params = self._filters(language, domain)
            sql = f"""
                SELECT count(*) total,
                       sum(CASE WHEN is_correct THEN 1 ELSE 0 END) correct,
                       sum(CASE WHEN result = 'error' THEN 1 ELSE 0 END) errors,
                       sum(CASE WHEN result = 'unknown' THEN 1 ELSE 0 END) unknown_count,
                       round(100.0 * avg(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END), 2) accuracy_pct
                FROM asr_cases {filters}
            """
        with self.warehouse.connect(read_only=True) as connection:
            row = self.warehouse.rows_as_dicts(connection.execute(sql, params))[0]
            source_rows = self._source_rows(connection, scope, language, domain)
        row.update({"language": language, "domain": domain, "source_scope": scope})
        return ToolObservation(
            call_id=call.call_id, tool_name=call.name, data=row,
            sources=self._sources_for_rows(source_rows, scope), warnings=self._scope_warnings(scope, language, domain),
        )

    def _tool_compare_languages(self, call: ToolCall) -> ToolObservation:
        args = call.arguments
        languages = self._languages(args.get("languages"))
        domain = self._domain(args.get("domain"))
        clauses, params = [], []
        if languages:
            clauses.append(f"language IN ({','.join('?' for _ in languages)})")
            params.extend(languages)
        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.warehouse.connect(read_only=True) as connection:
            cursor = connection.execute(
                f"""
                SELECT language, count(*) total,
                       sum(CASE WHEN is_correct THEN 1 ELSE 0 END) correct,
                       sum(CASE WHEN result = 'error' THEN 1 ELSE 0 END) errors,
                       sum(CASE WHEN result = 'unknown' THEN 1 ELSE 0 END) unknown_count,
                       round(100.0 * avg(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END), 2) accuracy_pct,
                       round(100.0 * avg(CASE WHEN result = 'error' THEN 1.0 ELSE 0.0 END), 2) error_rate_pct
                FROM asr_cases {where} GROUP BY language ORDER BY error_rate_pct DESC
                """, params,
            )
            rows = self.warehouse.rows_as_dicts(cursor)
            compared_languages = [row["language"] for row in rows]
            placeholders = ",".join("?" for _ in compared_languages)
            source_clauses = [f"language IN ({placeholders})"]
            source_params: List[Any] = list(compared_languages)
            if domain:
                source_clauses.append("domain = ?")
                source_params.append(domain)
            source_rows = self.warehouse.rows_as_dicts(
                connection.execute(
                    "SELECT DISTINCT language, domain, source_path FROM asr_cases WHERE "
                    + " AND ".join(source_clauses),
                    source_params,
                )
            )
        return ToolObservation(call_id=call.call_id, tool_name=call.name, rows=rows,
            data={"domain": domain, "count": len(rows), "source_scope": "csv_cases"},
            sources=self._sources_for_rows(source_rows), warnings=self._quality_warning())

    def _tool_compare_domains(self, call: ToolCall) -> ToolObservation:
        language = self._language(call.arguments.get("language"))
        where, params = self._filters(language, None)
        with self.warehouse.connect(read_only=True) as connection:
            cursor = connection.execute(
                f"""
                SELECT domain, count(*) total,
                       sum(CASE WHEN is_correct THEN 1 ELSE 0 END) correct,
                       sum(CASE WHEN result = 'error' THEN 1 ELSE 0 END) errors,
                       sum(CASE WHEN result = 'unknown' THEN 1 ELSE 0 END) unknown_count,
                       round(100.0 * avg(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END), 2) accuracy_pct,
                       round(100.0 * avg(CASE WHEN result = 'error' THEN 1.0 ELSE 0.0 END), 2) error_rate_pct
                FROM asr_cases {where} GROUP BY domain ORDER BY error_rate_pct DESC
                """, params,
            )
            rows = self.warehouse.rows_as_dicts(cursor)
            source_rows = self._source_rows(connection, "csv_cases", language, None)
        return ToolObservation(call_id=call.call_id, tool_name=call.name, rows=rows,
            data={"language": language, "count": len(rows), "source_scope": "csv_cases"},
            sources=self._sources_for_rows(source_rows), warnings=self._quality_warning())

    def _tool_rank_dimensions(self, call: ToolCall) -> ToolObservation:
        args = call.arguments
        dimension = args.get("dimension")
        metric = args.get("metric")
        if dimension not in {"language", "domain"}:
            raise ValueError("dimension must be language or domain")
        if metric not in {"errors", "error_rate", "accuracy", "total"}:
            raise ValueError("Unsupported ranking metric")
        language = self._language(args.get("language"))
        domain = self._domain(args.get("domain"))
        where, params = self._filters(language, domain)
        metric_sql = {"errors": "errors", "error_rate": "error_rate_pct", "accuracy": "accuracy_pct", "total": "total"}[metric]
        direction = "ASC" if metric == "accuracy" else "DESC"
        limit = max(1, min(int(args.get("limit", 10)), 20))
        with self.warehouse.connect(read_only=True) as connection:
            cursor = connection.execute(
                f"""
                SELECT {dimension}, count(*) total,
                       sum(CASE WHEN result = 'error' THEN 1 ELSE 0 END) errors,
                       sum(CASE WHEN result = 'unknown' THEN 1 ELSE 0 END) unknown_count,
                       round(100.0 * avg(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END), 2) accuracy_pct,
                       round(100.0 * avg(CASE WHEN result = 'error' THEN 1.0 ELSE 0.0 END), 2) error_rate_pct
                FROM asr_cases {where} GROUP BY {dimension}
                ORDER BY {metric_sql} {direction} LIMIT ?
                """, [*params, limit],
            )
            rows = self.warehouse.rows_as_dicts(cursor)
            source_rows = self._source_rows(connection, "csv_cases", language, domain)
        return ToolObservation(call_id=call.call_id, tool_name=call.name, rows=rows,
            data={"dimension": dimension, "metric": metric, "language": language, "domain": domain},
            sources=self._sources_for_rows(source_rows), warnings=self._quality_warning())

    def _tool_search_cases(self, call: ToolCall) -> ToolObservation:
        args = call.arguments
        query = str(args.get("query", "")).strip()
        languages = self._languages(args.get("languages"))
        domains = self._domains(args.get("domains"))
        result = args.get("result", "error")
        if result not in {"all", "correct", "error", "unknown"}:
            raise ValueError("result must be all, correct, error, or unknown")
        limit = max(1, min(int(args.get("limit", 20)), 100))
        clauses, params = [], []
        if query:
            clauses.append("(case_no ILIKE ? OR reference_text ILIKE ? OR hypothesis_text ILIKE ? OR case_id = ?)")
            params.extend([f"%{query}%", f"%{query}%", f"%{query}%", query])
        if languages:
            clauses.append(f"language IN ({','.join('?' for _ in languages)})")
            params.extend(languages)
        if domains:
            clauses.append(f"domain IN ({','.join('?' for _ in domains)})")
            params.extend(domains)
        if result != "all":
            clauses.append("result = ?")
            params.append(result)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.warehouse.connect(read_only=True) as connection:
            total = connection.execute(f"SELECT count(*) FROM asr_cases {where}", params).fetchone()[0]
            cursor = connection.execute(
                f"""
                SELECT case_id, language, domain, case_no, result, reference_text, hypothesis_text, source_path, source_row
                FROM asr_cases {where} ORDER BY language, domain, case_index LIMIT ?
                """, [*params, limit],
            )
            rows = self.warehouse.rows_as_dicts(cursor)
        warnings = []
        if total > limit:
            warnings.append(f"Matched {total} cases; returned the first {limit}.")
        return ToolObservation(call_id=call.call_id, tool_name=call.name, rows=rows,
            data={"query": query, "total_matches": total, "returned": len(rows), "result": result},
            sources=self._sources_for_rows(rows), warnings=warnings)

    def _tool_get_case_detail(self, call: ToolCall) -> ToolObservation:
        args = call.arguments
        case_id = str(args.get("case_id", "")).strip()
        case_no = str(args.get("case_no", "")).strip()
        language = self._language(args.get("language"))
        domain = self._domain(args.get("domain"))
        if not case_id and not case_no:
            raise ValueError("case_id or case_no is required")
        clauses, params = [], []
        if case_id:
            clauses.append("case_id = ?")
            params.append(case_id)
        else:
            clauses.append("case_no = ?")
            params.append(case_no)
        if language:
            clauses.append("language = ?")
            params.append(language)
        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        with self.warehouse.connect(read_only=True) as connection:
            cursor = connection.execute(
                "SELECT * FROM asr_cases WHERE " + " AND ".join(clauses) + " ORDER BY language, domain LIMIT 50", params
            )
            rows = self.warehouse.rows_as_dicts(cursor)
        warnings = ["The identifier matched multiple cases; add language/domain or use stable case_id."] if len(rows) > 1 else []
        return ToolObservation(call_id=call.call_id, tool_name=call.name, rows=rows,
            data={"match_count": len(rows)}, sources=self._sources_for_rows(rows), warnings=warnings)

    def _tool_compare_source_scopes(self, call: ToolCall) -> ToolObservation:
        language = self._language(call.arguments.get("language"))
        domain = self._domain(call.arguments.get("domain"))
        where, params = self._filters(language, domain)
        with self.warehouse.connect(read_only=True) as connection:
            cursor = connection.execute(
                f"""
                WITH c AS (
                    SELECT language, domain, count(*) csv_total,
                           sum(CASE WHEN is_correct THEN 1 ELSE 0 END) csv_correct,
                           sum(CASE WHEN result = 'error' THEN 1 ELSE 0 END) csv_errors,
                           min(source_path) csv_source
                    FROM asr_cases {where} GROUP BY language, domain
                )
                SELECT c.*, s.total json_total, s.correct json_correct, s.errors json_errors,
                       c.csv_total - s.total total_delta, c.csv_errors - s.errors error_delta,
                       s.source_path json_source
                FROM c JOIN asr_summaries s USING(language, domain)
                ORDER BY c.language, c.domain
                """, params,
            )
            rows = self.warehouse.rows_as_dicts(cursor)
        warnings = ["CSV case rows and JSON run summaries are separate source scopes; differences are reported, not merged."]
        return ToolObservation(call_id=call.call_id, tool_name=call.name, rows=rows,
            data={"count": len(rows)}, sources=self._sources_for_rows(rows, "both"), warnings=warnings)

    def _tool_data_quality(self, call: ToolCall) -> ToolObservation:
        language = self._language(call.arguments.get("language"))
        severity = call.arguments.get("severity")
        clauses, params = [], []
        if language:
            clauses.append("language = ?")
            params.append(language)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.warehouse.connect(read_only=True) as connection:
            cursor = connection.execute(f"SELECT * FROM data_quality_issues {where} ORDER BY severity, language, domain", params)
            rows = self.warehouse.rows_as_dicts(cursor)
        return ToolObservation(call_id=call.call_id, tool_name=call.name, rows=rows,
            data={"count": len(rows)}, sources=self._sources_for_rows(rows, "quality"),
            warnings=["Quality issues describe source inconsistencies; the platform does not modify original files."] if rows else [])

    def _filters(self, language: str | None, domain: str | None) -> tuple[str, List[Any]]:
        clauses, params = [], []
        if language:
            clauses.append("language = ?")
            params.append(language)
        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        return ("WHERE " + " AND ".join(clauses) if clauses else ""), params

    def _source_rows(self, connection, scope: str, language: str | None, domain: str | None) -> List[Dict[str, Any]]:
        table = "asr_summaries" if scope == "json_summary" else "asr_cases"
        where, params = self._filters(language, domain)
        return self.warehouse.rows_as_dicts(connection.execute(f"SELECT DISTINCT language, domain, source_path FROM {table} {where}", params))

    def _sources_for_rows(self, rows: Sequence[Dict[str, Any]], scope: str = "csv_cases") -> List[SourceRef]:
        seen, sources = set(), []
        for row in rows:
            paths = []
            if row.get("source_path"):
                paths.append(row["source_path"])
            if row.get("csv_source"):
                paths.append(row["csv_source"])
            if row.get("json_source"):
                paths.append(row["json_source"])
            for path in paths:
                if path in seen:
                    continue
                seen.add(path)
                sources.append(SourceRef(source_id=f"src-{len(sources)+1}", label="ASR source", path=path, scope=scope))
        if not sources:
            sources.append(SourceRef(source_id="warehouse", label="Multilingual ASR warehouse", path="data/warehouse.duckdb", scope=scope))
        return sources[:50]

    def _quality_warning(self) -> List[str]:
        warnings = (
            [f"The source catalog contains {self.ingest_report.issue_count} data-quality issue(s); use data_quality for details."]
            if self.ingest_report.issue_count
            else []
        )
        if self.settings.demo_mode:
            warnings.append(
                "Synthetic demo data is active; figures are not business results."
            )
        return warnings

    def _scope_warnings(self, scope: str, language: str | None, domain: str | None) -> List[str]:
        warnings = self._quality_warning()
        if scope == "json_summary":
            warnings.append("Metrics use raw *_output.json run summaries, not CSV case-row counts.")
        return warnings

    def _language(self, value: Any) -> str | None:
        if value in (None, "", "all"):
            return None
        match = next((item for item in self.languages if item.casefold() == str(value).casefold()), None)
        if not match:
            raise ValueError(f"Unknown language: {value}. Available: {', '.join(self.languages)}")
        return match

    def _languages(self, values: Any) -> List[str]:
        if not values:
            return []
        if isinstance(values, str):
            values = [values]
        return [self._language(value) for value in values if self._language(value)]

    def _domain(self, value: Any) -> str | None:
        if value in (None, "", "all"):
            return None
        match = next((item for item in self.domains if item.casefold() == str(value).casefold()), None)
        if not match:
            raise ValueError(f"Unknown domain: {value}. Available: {', '.join(self.domains)}")
        return match

    def _domains(self, values: Any) -> List[str]:
        if not values:
            return []
        if isinstance(values, str):
            values = [values]
        return [self._domain(value) for value in values if self._domain(value)]

    @staticmethod
    def _spec(name: str, description: str, properties: Dict[str, Any], required: List[str] | None = None) -> Dict[str, Any]:
        return {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}}
