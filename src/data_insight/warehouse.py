"""DuckDB warehouse for normalized multilingual ASR business data."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import duckdb

from data_insight.config import Settings

_CASE_NUMBER_RE = re.compile(r"#\s*(\d+)\s*/\s*(\d+)")
_WAREHOUSE_SCHEMA_VERSION = "3"


@dataclass(frozen=True)
class IngestReport:
    case_count: int
    correct_count: int
    error_count: int
    unknown_count: int
    summary_count: int
    issue_count: int
    languages: List[str]
    domains: List[str]
    fingerprint: str


class ASRWarehouse:
    """Owns ingestion and read-only analytical queries over ASR source files."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def connect(self, read_only: bool = False) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.settings.warehouse_path), read_only=read_only)

    def source_files(self) -> List[Path]:
        files: List[Path] = []
        for language, folder in self.settings.languages.items():
            language_root = self.settings.data_root / folder
            files.extend(language_root.glob(self.settings.case_pattern.format(language=language)))
            files.extend(language_root.glob(self.settings.summary_pattern.format(language=language)))
        return sorted({path.resolve() for path in files})

    def source_fingerprint(self) -> str:
        digest = hashlib.sha256()
        digest.update(_WAREHOUSE_SCHEMA_VERSION.encode("ascii"))
        for path in self.source_files():
            stat = path.stat()
            digest.update(str(path).encode("utf-8"))
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
        version = self._active_governance_version()
        if version is not None:
            digest.update(version.model_dump_json().encode("utf-8"))
        return digest.hexdigest()

    def ensure_ready(self) -> IngestReport:
        fingerprint = self.source_fingerprint()
        if self.settings.warehouse_path.exists():
            try:
                with self.connect(read_only=True) as connection:
                    saved = connection.execute(
                        "SELECT value FROM warehouse_meta WHERE key = 'source_fingerprint'"
                    ).fetchone()
                    if saved and saved[0] == fingerprint:
                        return self._report(connection, fingerprint)
            except duckdb.Error:
                pass
        return self.rebuild()

    def rebuild(self) -> IngestReport:
        self.settings.warehouse_path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.warehouse_path.exists():
            self.settings.warehouse_path.unlink()
        fingerprint = self.source_fingerprint()
        with self.connect() as connection:
            self._create_schema(connection)
            case_batch: List[Sequence[Any]] = []
            summary_rows: List[Sequence[Any]] = []
            issues: List[Sequence[Any]] = []

            for language, folder in self.settings.languages.items():
                language_root = self.settings.data_root / folder
                csv_paths = sorted(
                    language_root.glob(self.settings.case_pattern.format(language=language))
                )
                json_paths = sorted(
                    language_root.glob(self.settings.summary_pattern.format(language=language))
                )
                json_by_domain = {
                    path.parent.name.rsplit("_", 1)[0]: path for path in json_paths
                }
                for csv_path in csv_paths:
                    domain = csv_path.parent.name.rsplit("_", 1)[0]
                    rows, csv_issues = self._read_case_csv(csv_path, language, domain)
                    case_batch.extend(rows)
                    issues.extend(csv_issues)
                    summary_path = json_by_domain.get(domain)
                    if summary_path:
                        summary, summary_issues = self._read_summary(
                            summary_path, language, domain, len(rows)
                        )
                        summary_rows.append(summary)
                        issues.extend(summary_issues)
                    else:
                        issues.append(
                            self._issue(
                                "MISSING_SUMMARY",
                                "warning",
                                language,
                                domain,
                                csv_path,
                                "No matching *_output.json was found.",
                            )
                        )

            connection.executemany(
                """
                INSERT INTO asr_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                case_batch,
            )
            connection.executemany(
                """
                INSERT INTO asr_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                summary_rows,
            )
            self._apply_active_patches(connection)
            if issues:
                connection.executemany(
                    "INSERT INTO data_quality_issues VALUES (?, ?, ?, ?, ?, ?, ?)", issues
                )
            connection.execute(
                "INSERT INTO warehouse_meta VALUES ('source_fingerprint', ?)", [fingerprint]
            )
            connection.execute(
                "INSERT INTO warehouse_meta VALUES ('provider', 'multilingual_asr')"
            )
            return self._report(connection, fingerprint)

    def _active_governance_version(self):
        from data_insight.governance import GovernanceStore

        return GovernanceStore(self.settings.state_db_path).active_version(
            "multilingual_asr"
        )

    def _apply_active_patches(self, connection: duckdb.DuckDBPyConnection) -> None:
        version = self._active_governance_version()
        if version is None:
            return
        allowed = {"result_raw", "reference_text", "hypothesis_text"}
        for patch in version.patches:
            field_name = patch.get("field_name")
            entity_key = patch.get("entity_key")
            if field_name not in allowed:
                raise ValueError(f"Unsupported ASR governance patch field: {field_name}")
            exists = connection.execute(
                "SELECT 1 FROM asr_cases WHERE case_id = ?", [entity_key]
            ).fetchone()
            if exists is None:
                raise ValueError(f"Governance patch target not found: {entity_key}")
            proposed = patch.get("proposed_value")
            if field_name == "result_raw":
                normalized = (
                    "correct"
                    if proposed == "✓"
                    else "error"
                    if proposed == "✗"
                    else "unknown"
                )
                connection.execute(
                    """
                    UPDATE asr_cases
                    SET result_raw=?, result=?, is_correct=?, dataset_version=?
                    WHERE case_id=?
                    """,
                    [
                        proposed,
                        normalized,
                        normalized == "correct",
                        version.version_id,
                        entity_key,
                    ],
                )
            else:
                connection.execute(
                    f"UPDATE asr_cases SET {field_name}=?, dataset_version=? WHERE case_id=?",
                    [proposed, version.version_id, entity_key],
                )

    def _create_schema(self, connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            """
            CREATE TABLE asr_cases (
                case_id VARCHAR PRIMARY KEY,
                language VARCHAR NOT NULL,
                domain VARCHAR NOT NULL,
                case_no VARCHAR NOT NULL,
                case_index INTEGER,
                case_total INTEGER,
                result_raw VARCHAR NOT NULL,
                result VARCHAR NOT NULL,
                is_correct BOOLEAN NOT NULL,
                reference_text VARCHAR NOT NULL,
                hypothesis_text VARCHAR NOT NULL,
                source_path VARCHAR NOT NULL,
                source_row INTEGER NOT NULL,
                dataset_version VARCHAR NOT NULL
            );
            CREATE TABLE asr_summaries (
                language VARCHAR NOT NULL,
                domain VARCHAR NOT NULL,
                total INTEGER,
                correct INTEGER,
                errors INTEGER,
                accuracy_pct DOUBLE,
                cumulative_wer DOUBLE,
                cumulative_cer DOUBLE,
                csr DOUBLE,
                expected_total INTEGER,
                total_matched BOOLEAN,
                generated_at VARCHAR,
                source_path VARCHAR NOT NULL
            );
            CREATE TABLE data_quality_issues (
                issue_code VARCHAR NOT NULL,
                severity VARCHAR NOT NULL,
                language VARCHAR,
                domain VARCHAR,
                source_path VARCHAR NOT NULL,
                detail VARCHAR NOT NULL,
                count_delta INTEGER
            );
            CREATE TABLE warehouse_meta (key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL);
            CREATE INDEX idx_cases_language_domain ON asr_cases(language, domain);
            CREATE INDEX idx_cases_result ON asr_cases(result);
            """
        )

    def _read_case_csv(
        self, path: Path, language: str, domain: str
    ) -> tuple[List[Sequence[Any]], List[Sequence[Any]]]:
        rows: List[Sequence[Any]] = []
        issues: List[Sequence[Any]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.reader(file)
            header = next(reader, None)
            if header is None:
                issues.append(
                    self._issue(
                        "EMPTY_CSV", "error", language, domain, path, "CSV has no header."
                    )
                )
                return rows, issues
            if len(header) != 4:
                issues.append(
                    self._issue(
                        "EXTRA_COLUMNS",
                        "warning",
                        language,
                        domain,
                        path,
                        f"CSV has {len(header)} columns; only the first four are used.",
                    )
                )
            for source_row, raw in enumerate(reader, start=2):
                if not raw or not any(cell.strip() for cell in raw[:4]):
                    continue
                if len(raw) < 4:
                    issues.append(
                        self._issue(
                            "SHORT_ROW",
                            "warning",
                            language,
                            domain,
                            path,
                            f"Row {source_row} has fewer than four columns and was skipped.",
                        )
                    )
                    continue
                case_no, result_symbol, reference, hypothesis = (
                    raw[0].strip(),
                    raw[1].strip(),
                    raw[2].strip(),
                    raw[3].strip(),
                )
                match = _CASE_NUMBER_RE.search(case_no)
                case_index = int(match.group(1)) if match else None
                case_total = int(match.group(2)) if match else None
                normalized_result = (
                    "correct"
                    if result_symbol == "✓"
                    else "error"
                    if result_symbol == "✗"
                    else "unknown"
                )
                stable = "\x1f".join((language, domain, case_no, reference, hypothesis))
                case_id = f"{language.lower()}-{domain.lower()}-{hashlib.sha256(stable.encode('utf-8')).hexdigest()[:12]}"
                rows.append(
                    (
                        case_id,
                        language,
                        domain,
                        case_no,
                        case_index,
                        case_total,
                        result_symbol,
                        normalized_result,
                        normalized_result == "correct",
                        reference,
                        hypothesis,
                        self.settings.display_path(path),
                        source_row,
                        "raw",
                    )
                )
        return rows, issues

    def _read_summary(
        self, path: Path, language: str, domain: str, csv_total: int
    ) -> tuple[Sequence[Any], List[Sequence[Any]]]:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        language_payload = payload.get("languages", {}).get(language)
        if language_payload is None and payload.get("languages"):
            language_payload = next(iter(payload["languages"].values()))
        asr = (language_payload or {}).get("asr", {})
        total = int(asr.get("total", 0) or 0)
        correct = int(asr.get("correct", 0) or 0)
        issues: List[Sequence[Any]] = []
        if total != csv_total:
            issues.append(
                self._issue(
                    "CSV_SUMMARY_TOTAL_MISMATCH",
                    "warning",
                    language,
                    domain,
                    path,
                    f"CSV contains {csv_total} cases but output summary declares {total}.",
                    csv_total - total,
                )
            )
        return (
            language,
            domain,
            total,
            correct,
            max(total - correct, 0),
            float(asr.get("accuracy_pct", 0.0) or 0.0),
            float(asr.get("cumulative_wer", 0.0) or 0.0),
            float(asr.get("cumulative_cer", 0.0) or 0.0),
            float(asr.get("csr", 0.0) or 0.0),
            int(asr.get("expected_total", 0) or 0),
            bool(asr.get("total_matched", False)),
            str(payload.get("generated_at", "")),
            self.settings.display_path(path),
        ), issues

    def _issue(
        self,
        code: str,
        severity: str,
        language: str,
        domain: str,
        path: Path,
        detail: str,
        count_delta: int | None = None,
    ) -> Sequence[Any]:
        return (
            code,
            severity,
            language,
            domain,
            self.settings.display_path(path),
            detail,
            count_delta,
        )

    def _report(
        self, connection: duckdb.DuckDBPyConnection, fingerprint: str
    ) -> IngestReport:
        case_count = connection.execute("SELECT count(*) FROM asr_cases").fetchone()[0]
        result_counts = dict(
            connection.execute(
                "SELECT result, count(*) FROM asr_cases GROUP BY result"
            ).fetchall()
        )
        summary_count = connection.execute("SELECT count(*) FROM asr_summaries").fetchone()[0]
        issue_count = connection.execute(
            "SELECT count(*) FROM data_quality_issues"
        ).fetchone()[0]
        languages = [row[0] for row in connection.execute(
            "SELECT DISTINCT language FROM asr_cases ORDER BY language"
        ).fetchall()]
        domains = [row[0] for row in connection.execute(
            "SELECT DISTINCT domain FROM asr_cases ORDER BY domain"
        ).fetchall()]
        return IngestReport(
            case_count=case_count,
            correct_count=int(result_counts.get("correct", 0)),
            error_count=int(result_counts.get("error", 0)),
            unknown_count=int(result_counts.get("unknown", 0)),
            summary_count=summary_count,
            issue_count=issue_count,
            languages=languages,
            domains=domains,
            fingerprint=fingerprint,
        )

    @staticmethod
    def rows_as_dicts(
        cursor: duckdb.DuckDBPyConnection, rows: Iterable[Sequence[Any]] | None = None
    ) -> List[Dict[str, Any]]:
        values = list(rows if rows is not None else cursor.fetchall())
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row)) for row in values]
