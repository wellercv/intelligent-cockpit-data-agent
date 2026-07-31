"""Generic governance tools and the multilingual ASR governance adapter."""

from __future__ import annotations

import csv
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

from data_insight.config import Settings
from data_insight.data_contracts import ContractRegistry, ContractScanner
from data_insight.governance import DatasetVersionManager, GovernanceStore
from data_insight.providers.asr import MultilingualASRProvider
from data_insight.providers.base import DataProvider
from data_insight.providers.nlu import NLUEvaluationProvider
from data_insight.schemas import (
    ChangeRequest,
    DataContract,
    GovernanceFinding,
    GovernanceIssue,
    SourceRef,
    ToolCall,
    ToolObservation,
)


class ASRGovernanceAdapter:
    name = "multilingual_asr"

    def __init__(
        self,
        settings: Settings,
        provider: MultilingualASRProvider,
        contract: DataContract,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.warehouse = provider.warehouse
        self.contract = contract
        self.scanner = ContractScanner()

    def records(self) -> List[Dict[str, Any]]:
        with self.warehouse.connect(read_only=True) as connection:
            return self.warehouse.rows_as_dicts(
                connection.execute(
                    """
                    SELECT case_id, language, domain, case_no, result_raw,
                           reference_text, hypothesis_text,
                           source_path AS _source_path
                    FROM asr_cases ORDER BY language, domain, case_index
                    """
                )
            )

    def scan(self) -> List[GovernanceFinding]:
        findings = self.scanner.scan(self.contract, self.records())
        findings.extend(self._warehouse_findings())
        findings.extend(self._scope_findings())
        findings.extend(self._numbering_findings())
        findings.extend(self._blank_hypothesis_findings())
        findings.extend(self._extra_column_findings())
        deduped: Dict[tuple, GovernanceFinding] = {}
        for finding in findings:
            key = (
                finding.rule_id,
                finding.entity_key,
                finding.field_name,
                finding.source_path,
            )
            deduped[key] = finding
        return list(deduped.values())

    def get_record(self, entity_key: str) -> Dict[str, Any]:
        with self.warehouse.connect(read_only=True) as connection:
            cursor = connection.execute(
                """
                SELECT case_id, language, domain, case_no, result_raw, result,
                       reference_text, hypothesis_text, source_path, source_row,
                       dataset_version
                FROM asr_cases WHERE case_id=?
                """,
                [entity_key],
            )
            rows = self.warehouse.rows_as_dicts(cursor)
        if not rows:
            raise KeyError(f"ASR case not found: {entity_key}")
        return rows[0]

    def preview_change(
        self, entity_key: str, field_name: str, proposed_value: Any
    ) -> Dict[str, Any]:
        field = self.contract.field(field_name)
        if field is None:
            raise ValueError(f"Field is not declared in the data contract: {field_name}")
        if not field.mutable:
            raise PermissionError(f"Field is immutable by contract: {field_name}")
        before = self.get_record(entity_key)
        candidate = {
            key: before.get(key)
            for key in (
                "case_id",
                "language",
                "domain",
                "case_no",
                "result_raw",
                "reference_text",
                "hypothesis_text",
            )
        }
        candidate[field_name] = proposed_value
        validation = self.scanner.scan(self.contract, [candidate])
        field_errors = [
            item.model_dump(mode="json")
            for item in validation
            if item.field_name == field_name and item.severity in {"error", "critical"}
        ]
        before_value = before.get(field_name)
        valid = not field_errors
        return {
            "provider": self.name,
            "entity_key": entity_key,
            "field_name": field_name,
            "before_value": before_value,
            "proposed_value": proposed_value,
            "valid": valid,
            "validation_errors": field_errors,
            "dataset_version": before.get("dataset_version"),
            "source_path": before.get("source_path"),
            "diff": {
                "operation": "replace",
                "entity_key": entity_key,
                "field_name": field_name,
                "before": before_value,
                "after": proposed_value,
                "changed": before_value != proposed_value,
            },
            "contract_check": {
                "contract_id": self.contract.contract_id,
                "contract_version": self.contract.version,
                "mutable": field.mutable,
                "valid": valid,
                "errors": field_errors,
            },
        }

    def rebuild(self) -> None:
        self.warehouse.rebuild()
        self.provider.ingest_report = self.warehouse.ensure_ready()
        self.provider.languages = self.provider.ingest_report.languages
        self.provider.domains = self.provider.ingest_report.domains

    def _warehouse_findings(self) -> List[GovernanceFinding]:
        with self.warehouse.connect(read_only=True) as connection:
            rows = self.warehouse.rows_as_dicts(
                connection.execute("SELECT * FROM data_quality_issues")
            )
        return [
            GovernanceFinding(
                rule_id=row["issue_code"],
                severity=row["severity"],
                provider=self.name,
                contract_id=self.contract.contract_id,
                entity_key=f"{row['language']}:{row['domain']}",
                current_value=row.get("count_delta"),
                detail=row["detail"],
                source_path=row["source_path"],
            )
            for row in rows
        ]

    def _scope_findings(self) -> List[GovernanceFinding]:
        with self.warehouse.connect(read_only=True) as connection:
            rows = self.warehouse.rows_as_dicts(
                connection.execute(
                    """
                    WITH c AS (
                      SELECT language, domain, count(*) csv_total,
                             sum(CASE WHEN result='correct' THEN 1 ELSE 0 END) csv_correct,
                             sum(CASE WHEN result='error' THEN 1 ELSE 0 END) csv_errors
                      FROM asr_cases GROUP BY language, domain
                    )
                    SELECT c.*, s.total json_total, s.correct json_correct,
                           s.errors json_errors, s.source_path
                    FROM c JOIN asr_summaries s USING(language, domain)
                    WHERE c.csv_total<>s.total OR c.csv_correct<>s.correct
                    ORDER BY language, domain
                    """
                )
            )
        return [
            GovernanceFinding(
                rule_id="CSV_JSON_SCOPE_MISMATCH",
                severity="warning",
                provider=self.name,
                contract_id=self.contract.contract_id,
                entity_key=f"{row['language']}:{row['domain']}",
                current_value={
                    "csv_total": row["csv_total"],
                    "json_total": row["json_total"],
                    "csv_errors": row["csv_errors"],
                    "json_errors": row["json_errors"],
                },
                detail=(
                    f"CSV and JSON scopes disagree: total {row['csv_total']} vs "
                    f"{row['json_total']}; errors {row['csv_errors']} vs {row['json_errors']}."
                ),
                source_path=row["source_path"],
            )
            for row in rows
        ]

    def _numbering_findings(self) -> List[GovernanceFinding]:
        with self.warehouse.connect(read_only=True) as connection:
            rows = self.warehouse.rows_as_dicts(
                connection.execute(
                    """
                    SELECT language, domain, count(*) total_rows,
                           min(case_index) min_idx, max(case_index) max_idx,
                           min(case_total) min_declared,
                           max(case_total) max_declared,
                           count(distinct case_index) distinct_idx,
                           min(source_path) source_path
                    FROM asr_cases GROUP BY language, domain
                    HAVING total_rows<>max_idx OR min_idx<>1
                        OR min_declared<>max_declared OR distinct_idx<>total_rows
                    """
                )
            )
        return [
            GovernanceFinding(
                rule_id="CASE_NUMBERING_INCONSISTENT",
                severity="warning",
                provider=self.name,
                contract_id=self.contract.contract_id,
                entity_key=f"{row['language']}:{row['domain']}",
                current_value={
                    key: row[key]
                    for key in (
                        "total_rows",
                        "min_idx",
                        "max_idx",
                        "min_declared",
                        "max_declared",
                        "distinct_idx",
                    )
                },
                detail="Case numbering is incomplete or declared totals are inconsistent.",
                source_path=row["source_path"],
            )
            for row in rows
        ]

    def _blank_hypothesis_findings(self) -> List[GovernanceFinding]:
        with self.warehouse.connect(read_only=True) as connection:
            rows = self.warehouse.rows_as_dicts(
                connection.execute(
                    """
                    SELECT case_id, hypothesis_text, source_path
                    FROM asr_cases WHERE trim(hypothesis_text)=''
                    """
                )
            )
        return [
            GovernanceFinding(
                rule_id="BLANK_HYPOTHESIS_REVIEW",
                severity="info",
                provider=self.name,
                contract_id=self.contract.contract_id,
                entity_key=row["case_id"],
                field_name="hypothesis_text",
                current_value=row["hypothesis_text"],
                detail=(
                    "Hypothesis is blank. This may be a valid no-recognition outcome; "
                    "review it instead of auto-filling text."
                ),
                source_path=row["source_path"],
            )
            for row in rows
        ]

    def _extra_column_findings(self) -> List[GovernanceFinding]:
        findings: List[GovernanceFinding] = []
        for language, folder in self.settings.languages.items():
            root = self.settings.data_root / folder
            for path in root.glob(
                self.settings.case_pattern.format(language=language)
            ):
                domain = path.parent.name.rsplit("_", 1)[0]
                with path.open("r", encoding="utf-8-sig", newline="") as file:
                    rows = csv.reader(file)
                    next(rows, None)
                    for row_number, row in enumerate(rows, start=2):
                        extras = [item for item in row[4:] if item.strip()]
                        if not extras:
                            continue
                        case_no = row[0].strip() if row else f"row:{row_number}"
                        findings.append(
                            GovernanceFinding(
                                rule_id="UNMAPPED_EXTRA_VALUE",
                                severity="warning",
                                provider=self.name,
                                contract_id=self.contract.contract_id,
                                entity_key=f"{language}:{domain}:{case_no}",
                                current_value=extras,
                                detail=(
                                    f"CSV row {row_number} contains non-empty values in "
                                    "unnamed extra columns; the analysis schema does not map them."
                                ),
                                source_path=self.settings.display_path(path),
                            )
                        )
        return findings


class NLUGovernanceAdapter:
    """Read-only governance adapter for report-level NLU findings."""

    name = "nlu_evaluation"

    def __init__(
        self,
        provider: NLUEvaluationProvider,
        contract: DataContract,
    ) -> None:
        self.provider = provider
        self.contract = contract
        self.scanner = ContractScanner()

    def records(self) -> List[Dict[str, Any]]:
        return self.provider.governance_records()

    def scan(self) -> List[GovernanceFinding]:
        records = self.records()
        findings = self.scanner.scan(self.contract, records)
        severity_by_rule = {
            "LANGUAGE_NAMING_MISMATCH": "warning",
            "NUMERIC_SLOT_TYPE_MISMATCH": "warning",
            "PREDICTION_JSON_PARSE_FAILURE": "error",
            "DUPLICATE_LABEL_DETAIL": "warning",
            "DUPLICATE_MODEL_ERROR_DETAIL": "warning",
        }
        findings.extend(
            GovernanceFinding(
                rule_id=record["rule_id"],
                severity=severity_by_rule[record["rule_id"]],
                provider=self.name,
                contract_id=self.contract.contract_id,
                entity_key=record["finding_id"],
                current_value={
                    "affected_count": record["affected_count"],
                    "language": record.get("language"),
                    "domain": record.get("domain"),
                    "intent": record.get("intent"),
                },
                detail=record["detail"],
                source_path=record["_source_path"],
            )
            for record in records
        )
        return findings

    def get_record(self, entity_key: str) -> Dict[str, Any]:
        record = next(
            (item for item in self.records() if item["finding_id"] == entity_key),
            None,
        )
        if record is None:
            raise KeyError(f"NLU report finding not found: {entity_key}")
        return record

    def preview_change(
        self,
        entity_key: str,
        field_name: str,
        proposed_value: Any,
    ) -> Dict[str, Any]:
        raise PermissionError(
            "The NLU Excel report is an immutable evaluation artifact; "
            "correct labels in the authoritative test-set source instead."
        )

    def rebuild(self) -> None:
        self.provider.reload()


class DataGovernanceProvider(DataProvider):
    name = "data_governance"

    def __init__(
        self,
        settings: Settings,
        asr_provider: MultilingualASRProvider,
        nlu_provider: NLUEvaluationProvider | None = None,
    ) -> None:
        self.settings = settings
        self.contracts = ContractRegistry(settings.contracts_dir)
        self.store = GovernanceStore(settings.state_db_path)
        self.versions = DatasetVersionManager(self.store)
        self.adapters = {
            "multilingual_asr": ASRGovernanceAdapter(
                settings,
                asr_provider,
                self.contracts.get("multilingual_asr"),
            )
        }
        if nlu_provider is not None and nlu_provider.ready:
            self.adapters["nlu_evaluation"] = NLUGovernanceAdapter(
                nlu_provider,
                self.contracts.get("nlu_evaluation"),
            )

    def tool_catalog(self) -> List[Dict[str, Any]]:
        providers = sorted(self.adapters)
        return [
            self._spec(
                "governance_scan",
                "Scan one provider against its data contract and persist quality issues",
                {"provider": {"type": "string", "enum": providers}},
            ),
            self._spec(
                "list_governance_issues",
                "List tracked data quality issues and workflow status",
                {
                    "provider": {"type": "string", "enum": providers},
                    "status": {
                        "type": "string",
                        "enum": ["OPEN", "IN_REVIEW", "RESOLVED", "WAIVED"],
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["info", "warning", "error", "critical"],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
            ),
            self._spec(
                "get_governance_issue",
                "Get one governance issue",
                {"issue_id": {"type": "string"}},
                required=["issue_id"],
            ),
            self._spec(
                "list_change_requests",
                "List data change requests and confirmation status",
                {
                    "provider": {"type": "string", "enum": providers},
                    "status": {
                        "type": "string",
                        "enum": [
                            "DRAFT",
                            "CONFIRMED",
                            "PENDING_APPROVAL",
                            "APPROVED",
                            "REJECTED",
                            "PUBLISHED",
                            "ROLLED_BACK",
                        ],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
            ),
            self._spec(
                "preview_change",
                "Validate and preview a draft change without modifying data",
                {"change_id": {"type": "string"}},
                required=["change_id"],
            ),
        ]

    def execute(self, call: ToolCall) -> ToolObservation:
        handler = getattr(self, f"_tool_{call.name}", None)
        if handler is None:
            return ToolObservation(
                call_id=call.call_id,
                tool_name=call.name,
                success=False,
                error=f"Unknown governance tool: {call.name}",
            )
        try:
            return handler(call)
        except (KeyError, ValueError, PermissionError) as error:
            return ToolObservation(
                call_id=call.call_id,
                tool_name=call.name,
                success=False,
                error=f"{type(error).__name__}: {error}",
            )

    def health(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "ready": True,
            "contracts": self.contracts.summary(),
            "governed_providers": sorted(self.adapters),
            "open_issues": len(self.store.list_issues(status="OPEN", limit=1000)),
            "pending_confirmations": sum(
                len(self.store.list_changes(status=status, limit=1000))
                for status in ("DRAFT", "PENDING_APPROVAL")
            ),
        }

    def create_change_draft(
        self,
        issue_id: str,
        proposed_value: Any,
        reason: str,
        requested_by: str,
    ) -> ChangeRequest:
        issue = self.store.get_issue(issue_id)
        adapter = self._adapter(issue.finding.provider)
        preview = adapter.preview_change(
            issue.finding.entity_key,
            str(issue.finding.field_name),
            proposed_value,
        )
        if not preview["valid"]:
            raise ValueError(f"Proposed value violates the data contract: {preview['validation_errors']}")
        active = self.store.active_version(issue.finding.provider)
        return self.store.create_change_request(
            issue_id,
            preview["before_value"],
            proposed_value,
            reason,
            requested_by,
            active.version_id if active else f"{issue.finding.provider}-raw",
        )

    def confirm_change(
        self, change_id: str, actor: str, comment: str = "Diff reviewed"
    ) -> ChangeRequest:
        return self.store.confirm_change(change_id, actor, comment)

    def publish_changes(
        self, provider: str, change_ids: List[str], publisher: str
    ):
        adapter = self._adapter(provider)
        return self.versions.publish(provider, change_ids, publisher, adapter.rebuild)

    def rollback_active(self, provider: str, actor: str):
        adapter = self._adapter(provider)
        return self.versions.rollback_active(provider, actor, adapter.rebuild)

    def _tool_governance_scan(self, call: ToolCall) -> ToolObservation:
        provider = str(call.arguments.get("provider") or "multilingual_asr")
        adapter = self._adapter(provider)
        findings = adapter.scan()
        issues = self.store.sync_findings(findings)
        rows = [self._issue_row(item) for item in issues]
        counts = Counter(item.finding.severity for item in issues)
        rules = Counter(item.finding.rule_id for item in issues)
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            data={
                "provider": provider,
                "contract_id": adapter.contract.contract_id,
                "finding_count": len(issues),
                "by_severity": dict(counts),
                "by_rule": dict(rules),
            },
            rows=rows[:200],
            sources=[self._contract_source(adapter.contract)],
            warnings=[
                "Findings are governance candidates. Business-ambiguous records require human review; no raw data was modified."
            ],
        )

    def _tool_list_governance_issues(self, call: ToolCall) -> ToolObservation:
        args = call.arguments
        issues = self.store.list_issues(
            provider=args.get("provider"),
            status=args.get("status"),
            severity=args.get("severity"),
            limit=int(args.get("limit", 100)),
        )
        rows = [self._issue_row(item) for item in issues]
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            data={"count": len(rows)},
            rows=rows,
            sources=self._sources_for_issues(issues),
        )

    def _tool_get_governance_issue(self, call: ToolCall) -> ToolObservation:
        issue = self.store.get_issue(str(call.arguments["issue_id"]))
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            data=self._issue_row(issue),
            sources=self._sources_for_issues([issue]),
        )

    def _tool_list_change_requests(self, call: ToolCall) -> ToolObservation:
        args = call.arguments
        changes = self.store.list_changes(
            provider=args.get("provider"),
            status=args.get("status"),
            limit=int(args.get("limit", 100)),
        )
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            data={"count": len(changes)},
            rows=[item.model_dump(mode="json") for item in changes],
            sources=[
                SourceRef(
                    source_id="governance-state",
                    label="Governance workflow state",
                    path="data/agent_state.db",
                    scope="governance_state",
                )
            ],
        )

    def _tool_preview_change(self, call: ToolCall) -> ToolObservation:
        change = self.store.get_change(str(call.arguments["change_id"]))
        preview = self._adapter(change.provider).preview_change(
            change.entity_key,
            change.field_name,
            change.proposed_value,
        )
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            data={**preview, "change_id": change.change_id, "status": change.status},
            sources=[
                SourceRef(
                    source_id="change-preview",
                    label="Change request preview",
                    path="data/agent_state.db",
                    scope="governance_state",
                )
            ],
            warnings=["Preview only; no raw or published data was modified."],
        )

    def _adapter(self, provider: str) -> ASRGovernanceAdapter:
        if provider not in self.adapters:
            raise KeyError(f"No governance adapter registered for provider: {provider}")
        return self.adapters[provider]

    def _contract_source(self, contract: DataContract) -> SourceRef:
        return SourceRef(
            source_id=f"contract-{contract.provider}",
            label=f"Data contract {contract.contract_id}",
            path=f"config/contracts/{contract.provider}.yaml",
            scope="data_contract",
        )

    @staticmethod
    def _issue_row(issue: GovernanceIssue) -> Dict[str, Any]:
        finding = issue.finding
        return {
            "issue_id": issue.issue_id,
            "provider": finding.provider,
            "rule_id": finding.rule_id,
            "severity": finding.severity,
            "entity_key": finding.entity_key,
            "field_name": finding.field_name,
            "current_value": finding.current_value,
            "detail": finding.detail,
            "source_path": finding.source_path,
            "status": issue.status,
            "owner": issue.owner,
            "updated_at": issue.updated_at,
        }

    @staticmethod
    def _sources_for_issues(issues: Iterable[GovernanceIssue]) -> List[SourceRef]:
        paths = list(dict.fromkeys(item.finding.source_path for item in issues if item.finding.source_path))
        return [
            SourceRef(
                source_id=f"issue-source-{index}",
                label="Governance issue source",
                path=path,
                scope="source_data",
            )
            for index, path in enumerate(paths[:50], start=1)
        ] or [
            SourceRef(
                source_id="governance-state",
                label="Governance workflow state",
                path="data/agent_state.db",
                scope="governance_state",
            )
        ]

    @staticmethod
    def _spec(
        name: str,
        description: str,
        properties: Dict[str, Any],
        required: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
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
