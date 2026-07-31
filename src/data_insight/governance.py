"""Persistent governance issues, user confirmations, versions, and audit history."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from data_insight.schemas import (
    ChangeRequest,
    DatasetVersion,
    GovernanceFinding,
    GovernanceIssue,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GovernanceStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS governance_issues (
                    issue_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    contract_id TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    field_name TEXT,
                    current_value_json TEXT,
                    detail TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    owner TEXT,
                    resolution TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_governance_issue_status
                    ON governance_issues(provider, status, severity);

                CREATE TABLE IF NOT EXISTS change_requests (
                    change_id TEXT PRIMARY KEY,
                    issue_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    before_json TEXT,
                    proposed_json TEXT,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    reviewed_by TEXT,
                    review_comment TEXT,
                    base_version TEXT,
                    target_version TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(issue_id) REFERENCES governance_issues(issue_id)
                );
                CREATE INDEX IF NOT EXISTS idx_change_status
                    ON change_requests(provider, status);

                CREATE TABLE IF NOT EXISTS dataset_versions (
                    version_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    parent_version TEXT,
                    status TEXT NOT NULL,
                    patches_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_active_provider_version
                    ON dataset_versions(provider) WHERE status = 'ACTIVE';

                CREATE TABLE IF NOT EXISTS governance_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def finding_id(finding: GovernanceFinding) -> str:
        stable = "\x1f".join(
            (
                finding.provider,
                finding.contract_id,
                finding.rule_id,
                finding.entity_key,
                finding.field_name or "",
                finding.source_path,
            )
        )
        return "DQ-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]

    def sync_findings(
        self, findings: Iterable[GovernanceFinding], actor: str = "governance-scan"
    ) -> List[GovernanceIssue]:
        now = utc_now()
        issue_ids: List[str] = []
        with self._connect() as connection:
            for finding in findings:
                issue_id = self.finding_id(finding)
                issue_ids.append(issue_id)
                existing = connection.execute(
                    "SELECT issue_id FROM governance_issues WHERE issue_id = ?",
                    [issue_id],
                ).fetchone()
                if existing:
                    connection.execute(
                        """
                        UPDATE governance_issues
                        SET severity=?, current_value_json=?, detail=?, source_path=?,
                            updated_at=?
                        WHERE issue_id=?
                        """,
                        [
                            finding.severity,
                            json.dumps(finding.current_value, ensure_ascii=False),
                            finding.detail,
                            finding.source_path,
                            now,
                            issue_id,
                        ],
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO governance_issues VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            'OPEN', NULL, NULL, ?, ?
                        )
                        """,
                        [
                            issue_id,
                            finding.provider,
                            finding.contract_id,
                            finding.rule_id,
                            finding.severity,
                            finding.entity_key,
                            finding.field_name,
                            json.dumps(finding.current_value, ensure_ascii=False),
                            finding.detail,
                            finding.source_path,
                            now,
                            now,
                        ],
                    )
                    self._audit_with_connection(
                        connection,
                        "issue",
                        issue_id,
                        "CREATED",
                        actor,
                        finding.model_dump(mode="json"),
                    )
        return [self.get_issue(issue_id) for issue_id in issue_ids]

    def list_issues(
        self,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 100,
    ) -> List[GovernanceIssue]:
        clauses, params = [], []
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM governance_issues {where}
                ORDER BY CASE severity
                    WHEN 'critical' THEN 0 WHEN 'error' THEN 1
                    WHEN 'warning' THEN 2 ELSE 3 END,
                    updated_at DESC LIMIT ?
                """,
                [*params, max(1, min(limit, 1000))],
            ).fetchall()
        return [self._issue_from_row(row) for row in rows]

    def get_issue(self, issue_id: str) -> GovernanceIssue:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM governance_issues WHERE issue_id = ?", [issue_id]
            ).fetchone()
        if row is None:
            raise KeyError(f"Governance issue not found: {issue_id}")
        return self._issue_from_row(row)

    def create_change_request(
        self,
        issue_id: str,
        before_value: Any,
        proposed_value: Any,
        reason: str,
        requested_by: str,
        base_version: Optional[str] = None,
    ) -> ChangeRequest:
        issue = self.get_issue(issue_id)
        if not issue.finding.field_name:
            raise ValueError("This issue is not a field-level issue and cannot be patched directly")
        now = utc_now()
        stable = f"{issue_id}\x1f{requested_by}\x1f{now}"
        change_id = "CHG-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO change_requests VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?,
                    NULL, NULL, ?, NULL, ?, ?
                )
                """,
                [
                    change_id,
                    issue_id,
                    issue.finding.provider,
                    issue.finding.entity_key,
                    issue.finding.field_name,
                    json.dumps(before_value, ensure_ascii=False),
                    json.dumps(proposed_value, ensure_ascii=False),
                    reason,
                    requested_by,
                    base_version,
                    now,
                    now,
                ],
            )
            self._audit_with_connection(
                connection,
                "change",
                change_id,
                "CREATED_DRAFT",
                requested_by,
                {"issue_id": issue_id},
            )
        return self.get_change(change_id)

    def submit_change(self, change_id: str, actor: str) -> ChangeRequest:
        return self._transition_change(
            change_id,
            expected="DRAFT",
            target="PENDING_APPROVAL",
            actor=actor,
            action="SUBMITTED",
            issue_status="IN_REVIEW",
        )

    def confirm_change(
        self,
        change_id: str,
        actor: str,
        comment: str = "Diff and contract checks reviewed",
    ) -> ChangeRequest:
        change = self.get_change(change_id)
        if change.status not in {"DRAFT", "PENDING_APPROVAL"}:
            raise ValueError(
                f"Change {change_id} must be DRAFT or PENDING_APPROVAL, "
                f"current status is {change.status}"
            )
        return self._transition_change(
            change_id,
            expected=change.status,
            target="CONFIRMED",
            actor=actor,
            action="CONFIRMED",
            review_comment=comment,
            issue_status="IN_REVIEW",
        )

    def approve_change(
        self, change_id: str, reviewer: str, comment: str
    ) -> ChangeRequest:
        change = self.get_change(change_id)
        if reviewer == change.requested_by:
            raise ValueError("Requester cannot approve their own change")
        return self._transition_change(
            change_id,
            expected="PENDING_APPROVAL",
            target="APPROVED",
            actor=reviewer,
            action="APPROVED",
            review_comment=comment,
        )

    def reject_change(
        self, change_id: str, reviewer: str, comment: str
    ) -> ChangeRequest:
        change = self.get_change(change_id)
        if reviewer == change.requested_by:
            raise ValueError("Requester cannot reject their own change")
        return self._transition_change(
            change_id,
            expected="PENDING_APPROVAL",
            target="REJECTED",
            actor=reviewer,
            action="REJECTED",
            review_comment=comment,
            issue_status="OPEN",
        )

    def mark_published(
        self, change_id: str, version_id: str, actor: str
    ) -> ChangeRequest:
        current = self.get_change(change_id)
        if current.status not in {"CONFIRMED", "APPROVED"}:
            raise ValueError(
                f"Change {change_id} must be CONFIRMED, current status is "
                f"{current.status}"
            )
        change = self._transition_change(
            change_id,
            expected=current.status,
            target="PUBLISHED",
            actor=actor,
            action="PUBLISHED",
            target_version=version_id,
            issue_status="RESOLVED",
            resolution=f"Published in dataset version {version_id}",
        )
        return change

    def mark_rolled_back(self, change_id: str, actor: str) -> ChangeRequest:
        return self._transition_change(
            change_id,
            expected="PUBLISHED",
            target="ROLLED_BACK",
            actor=actor,
            action="ROLLED_BACK",
            issue_status="OPEN",
            resolution="Published change was rolled back",
        )

    def get_change(self, change_id: str) -> ChangeRequest:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM change_requests WHERE change_id = ?", [change_id]
            ).fetchone()
        if row is None:
            raise KeyError(f"Change request not found: {change_id}")
        return self._change_from_row(row)

    def list_changes(
        self,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[ChangeRequest]:
        clauses, params = [], []
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM change_requests {where} ORDER BY updated_at DESC LIMIT ?",
                [*params, max(1, min(limit, 1000))],
            ).fetchall()
        return [self._change_from_row(row) for row in rows]

    def save_version(self, version: DatasetVersion) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE dataset_versions SET status='ARCHIVED' WHERE provider=? AND status='ACTIVE'",
                [version.provider],
            )
            connection.execute(
                """
                INSERT INTO dataset_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    version.version_id,
                    version.provider,
                    version.parent_version,
                    version.status,
                    json.dumps(version.patches, ensure_ascii=False),
                    version.created_by,
                    version.approved_by,
                    version.created_at,
                ],
            )
            self._audit_with_connection(
                connection,
                "version",
                version.version_id,
                "ACTIVATED",
                version.created_by,
                version.model_dump(mode="json"),
            )

    def active_version(self, provider: str) -> Optional[DatasetVersion]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM dataset_versions WHERE provider=? AND status='ACTIVE'",
                [provider],
            ).fetchone()
        return self._version_from_row(row) if row else None

    def get_version(self, version_id: str) -> DatasetVersion:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM dataset_versions WHERE version_id=?", [version_id]
            ).fetchone()
        if row is None:
            raise KeyError(f"Dataset version not found: {version_id}")
        return self._version_from_row(row)

    def activate_version(self, version_id: str, actor: str) -> DatasetVersion:
        version = self.get_version(version_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE dataset_versions SET status='ARCHIVED' WHERE provider=? AND status='ACTIVE'",
                [version.provider],
            )
            connection.execute(
                "UPDATE dataset_versions SET status='ACTIVE' WHERE version_id=?",
                [version_id],
            )
            self._audit_with_connection(
                connection,
                "version",
                version_id,
                "REACTIVATED",
                actor,
                {},
            )
        return self.get_version(version_id)

    def audit_log(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM governance_audit ORDER BY id DESC LIMIT ?",
                [max(1, min(limit, 1000))],
            ).fetchall()
        return [
            {
                **dict(row),
                "detail": json.loads(row["detail_json"]),
            }
            for row in rows
        ]

    def _transition_change(
        self,
        change_id: str,
        expected: str,
        target: str,
        actor: str,
        action: str,
        review_comment: Optional[str] = None,
        target_version: Optional[str] = None,
        issue_status: Optional[str] = None,
        resolution: Optional[str] = None,
    ) -> ChangeRequest:
        change = self.get_change(change_id)
        if change.status != expected:
            raise ValueError(
                f"Change {change_id} must be {expected}, current status is {change.status}"
            )
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE change_requests
                SET status=?, reviewed_by=coalesce(?, reviewed_by),
                    review_comment=coalesce(?, review_comment),
                    target_version=coalesce(?, target_version), updated_at=?
                WHERE change_id=?
                """,
                [
                    target,
                    actor if target in {"CONFIRMED", "APPROVED", "REJECTED"} else None,
                    review_comment,
                    target_version,
                    now,
                    change_id,
                ],
            )
            if issue_status:
                connection.execute(
                    """
                    UPDATE governance_issues
                    SET status=?, resolution=coalesce(?, resolution), updated_at=?
                    WHERE issue_id=?
                    """,
                    [issue_status, resolution, now, change.issue_id],
                )
            self._audit_with_connection(
                connection,
                "change",
                change_id,
                action,
                actor,
                {"from": expected, "to": target, "comment": review_comment},
            )
        return self.get_change(change_id)

    def _audit_with_connection(
        self,
        connection: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        action: str,
        actor: str,
        detail: Dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO governance_audit VALUES (NULL, ?, ?, ?, ?, ?, ?)",
            [
                entity_type,
                entity_id,
                action,
                actor,
                json.dumps(detail, ensure_ascii=False),
                utc_now(),
            ],
        )

    @staticmethod
    def _issue_from_row(row: sqlite3.Row) -> GovernanceIssue:
        finding = GovernanceFinding(
            rule_id=row["rule_id"],
            severity=row["severity"],
            provider=row["provider"],
            contract_id=row["contract_id"],
            entity_key=row["entity_key"],
            field_name=row["field_name"],
            current_value=json.loads(row["current_value_json"]),
            detail=row["detail"],
            source_path=row["source_path"],
        )
        return GovernanceIssue(
            issue_id=row["issue_id"],
            finding=finding,
            status=row["status"],
            owner=row["owner"],
            resolution=row["resolution"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _change_from_row(row: sqlite3.Row) -> ChangeRequest:
        return ChangeRequest(
            change_id=row["change_id"],
            issue_id=row["issue_id"],
            provider=row["provider"],
            entity_key=row["entity_key"],
            field_name=row["field_name"],
            before_value=json.loads(row["before_json"]),
            proposed_value=json.loads(row["proposed_json"]),
            reason=row["reason"],
            status=row["status"],
            requested_by=row["requested_by"],
            reviewed_by=row["reviewed_by"],
            review_comment=row["review_comment"],
            base_version=row["base_version"],
            target_version=row["target_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _version_from_row(row: sqlite3.Row) -> DatasetVersion:
        return DatasetVersion(
            version_id=row["version_id"],
            provider=row["provider"],
            parent_version=row["parent_version"],
            status=row["status"],
            patches=json.loads(row["patches_json"]),
            created_by=row["created_by"],
            approved_by=row["approved_by"],
            created_at=row["created_at"],
        )


class DatasetVersionManager:
    """Publish user-confirmed changes as immutable overlay versions and roll them back."""

    def __init__(self, store: GovernanceStore) -> None:
        self.store = store

    def publish(
        self,
        provider: str,
        change_ids: List[str],
        publisher: str,
        rebuild: Any,
    ) -> DatasetVersion:
        if not change_ids:
            raise ValueError("At least one confirmed change is required")
        changes = [self.store.get_change(change_id) for change_id in change_ids]
        if any(change.provider != provider for change in changes):
            raise ValueError("All changes must belong to the selected provider")
        invalid = [
            change.change_id
            for change in changes
            if change.status not in {"CONFIRMED", "APPROVED"}
        ]
        if invalid:
            raise ValueError(
                "Only CONFIRMED changes can be published: " + ", ".join(invalid)
            )
        confirmers = {change.reviewed_by for change in changes}
        if None in confirmers:
            raise ValueError("Every change must have a confirmation actor")
        parent = self.store.active_version(provider)
        if parent is None:
            parent = DatasetVersion(
                version_id=f"{provider}-raw",
                provider=provider,
                parent_version=None,
                status="ACTIVE",
                patches=[],
                created_by="system",
                approved_by="system",
                created_at=utc_now(),
            )
            self.store.save_version(parent)
        patches = list(parent.patches) if parent else []
        patch_keys = {
            (change.entity_key, change.field_name) for change in changes
        }
        patches = [
            patch
            for patch in patches
            if (patch.get("entity_key"), patch.get("field_name")) not in patch_keys
        ]
        patches.extend(
            {
                "change_id": change.change_id,
                "issue_id": change.issue_id,
                "entity_key": change.entity_key,
                "field_name": change.field_name,
                "before_value": change.before_value,
                "proposed_value": change.proposed_value,
                "reason": change.reason,
            }
            for change in changes
        )
        now = utc_now()
        digest = hashlib.sha256(
            json.dumps(patches, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:10]
        version_id = f"{provider}-{now[:10].replace('-', '')}-{digest}"
        version = DatasetVersion(
            version_id=version_id,
            provider=provider,
            parent_version=parent.version_id if parent else None,
            status="ACTIVE",
            patches=patches,
            created_by=publisher,
            approved_by=", ".join(sorted(str(item) for item in confirmers)),
            created_at=now,
        )
        self.store.save_version(version)
        try:
            rebuild()
        except Exception:
            if parent:
                self.store.activate_version(parent.version_id, "system-rollback")
            raise
        for change in changes:
            self.store.mark_published(change.change_id, version_id, publisher)
        return version

    def rollback_active(
        self, provider: str, actor: str, rebuild: Any
    ) -> DatasetVersion:
        active = self.store.active_version(provider)
        if active is None or active.parent_version is None:
            raise ValueError("No parent dataset version is available for rollback")
        parent = self.store.activate_version(active.parent_version, actor)
        rebuild()
        active_change_ids = {
            patch.get("change_id")
            for patch in active.patches
            if patch.get("change_id")
        }
        parent_change_ids = {
            patch.get("change_id")
            for patch in parent.patches
            if patch.get("change_id")
        }
        for change_id in sorted(active_change_ids - parent_change_ids):
            try:
                self.store.mark_rolled_back(change_id, actor)
            except (KeyError, ValueError):
                continue
        return parent
