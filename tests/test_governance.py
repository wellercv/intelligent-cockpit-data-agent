from __future__ import annotations

import json
from pathlib import Path

import yaml

from data_insight.config import Settings
from data_insight.data_contracts import ContractScanner
from data_insight.providers.asr import MultilingualASRProvider
from data_insight.providers.governance import DataGovernanceProvider
from data_insight.schemas import DataContract, ToolCall


def test_generic_contract_scanner_supports_non_asr_sales_data():
    contract = DataContract.model_validate(
        {
            "contract_id": "sales-order",
            "version": "1.0",
            "provider": "sales",
            "entity": "order",
            "primary_key": "order_id",
            "fields": [
                {
                    "name": "order_id",
                    "required": True,
                    "allow_blank": False,
                    "unique": True,
                },
                {
                    "name": "customer_id",
                    "required": True,
                    "allow_blank": False,
                },
                {
                    "name": "status",
                    "required": True,
                    "allowed_values": ["created", "paid", "cancelled"],
                },
                {
                    "name": "amount",
                    "data_type": "number",
                    "required": True,
                    "minimum": 0,
                },
            ],
        }
    )
    records = [
        {
            "order_id": "O-1",
            "customer_id": "C-1",
            "status": "paid",
            "amount": 10.0,
        },
        {
            "order_id": "O-1",
            "customer_id": "",
            "status": "unknown",
            "amount": -2.0,
        },
    ]
    findings = ContractScanner().scan(contract, records)
    assert {item.rule_id for item in findings} == {
        "DUPLICATE_VALUE",
        "BLANK_NOT_ALLOWED",
        "VALUE_NOT_ALLOWED",
        "VALUE_BELOW_MINIMUM",
    }


def _write_minimal_asr_project(tmp_path: Path) -> Settings:
    data_root = tmp_path / "sources"
    source_dir = data_root / "English" / "phone_English"
    source_dir.mkdir(parents=True)
    csv_path = source_dir / "demo_English_asr.csv"
    csv_path.write_text(
        "NO（序号）,Result（结果）,REF（参考）,HYP（假设）\n"
        "ASR #1/2,✓,Answer,Answer\n"
        "ASR #2/2,,Play TuneIn,Play tune in\n",
        encoding="utf-8-sig",
    )
    (source_dir / "demo_output.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-01-01",
                "languages": {
                    "English": {
                        "asr": {
                            "total": 2,
                            "correct": 1,
                            "accuracy_pct": 50.0,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    project_root = tmp_path / "project"
    contracts_dir = project_root / "config" / "contracts"
    contracts_dir.mkdir(parents=True)
    contract = {
        "contract_id": "multilingual-asr-case",
        "version": "1.0",
        "provider": "multilingual_asr",
        "entity": "asr_case",
        "primary_key": "case_id",
        "fields": [
            {
                "name": "case_id",
                "required": True,
                "allow_blank": False,
                "unique": True,
            },
            {
                "name": "language",
                "required": True,
                "allowed_values": ["English"],
            },
            {
                "name": "domain",
                "required": True,
                "allowed_values": ["phone"],
            },
            {"name": "case_no", "required": True, "allow_blank": False},
            {
                "name": "result_raw",
                "required": True,
                "allow_blank": False,
                "allowed_values": ["✓", "✗"],
                "mutable": True,
            },
            {
                "name": "reference_text",
                "required": True,
                "allow_blank": False,
                "mutable": True,
            },
            {
                "name": "hypothesis_text",
                "required": True,
                "allow_blank": True,
                "mutable": True,
            },
        ],
    }
    (contracts_dir / "multilingual_asr.yaml").write_text(
        yaml.safe_dump(contract, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    data_dir = project_root / "data"
    governance_dir = data_dir / "governance"
    governance_dir.mkdir(parents=True)
    knowledge_dir = project_root / "knowledge"
    skills_dir = project_root / "skills"
    knowledge_dir.mkdir()
    skills_dir.mkdir()
    return Settings(
        project_root=project_root,
        data_root=data_root,
        warehouse_path=data_dir / "warehouse.duckdb",
        state_db_path=data_dir / "agent_state.db",
        knowledge_dir=knowledge_dir,
        skills_dir=skills_dir,
        contracts_dir=contracts_dir,
        governance_dir=governance_dir,
        languages={"English": "English"},
        case_pattern="*_{language}/*_asr.csv",
        summary_pattern="*_{language}/*_output.json",
    )


def test_governance_publish_and_rollback_do_not_modify_raw_data(tmp_path):
    settings = _write_minimal_asr_project(tmp_path)
    asr = MultilingualASRProvider(settings)
    governance = DataGovernanceProvider(settings, asr)

    before = asr.execute(ToolCall(name="dataset_overview"))
    assert before.data["errors"] == 0
    assert before.data["unknown_count"] == 1

    scan = governance.execute(
        ToolCall(name="governance_scan", arguments={"provider": "multilingual_asr"})
    )
    blank_issue = next(
        row for row in scan.rows if row["rule_id"] == "BLANK_NOT_ALLOWED"
    )
    change = governance.create_change_draft(
        blank_issue["issue_id"], "✗", "Confirmed missing result as error", "alice"
    )
    preview = governance.execute(
        ToolCall(name="preview_change", arguments={"change_id": change.change_id})
    )
    repeated_preview = governance.execute(
        ToolCall(name="preview_change", arguments={"change_id": change.change_id})
    )
    assert preview.data["diff"] == {
        "operation": "replace",
        "entity_key": change.entity_key,
        "field_name": "result_raw",
        "before": "",
        "after": "✗",
        "changed": True,
    }
    assert preview.data["contract_check"]["mutable"] is True
    assert preview.data["contract_check"]["valid"] is True
    assert repeated_preview.data["diff"] == preview.data["diff"]
    confirmed = governance.confirm_change(
        change.change_id,
        "local-user",
        "Reviewed structured diff and contract checks",
    )
    assert confirmed.status == "CONFIRMED"
    assert confirmed.reviewed_by == "local-user"
    version = governance.publish_changes(
        "multilingual_asr", [change.change_id], "local-user"
    )

    after = asr.execute(ToolCall(name="dataset_overview"))
    assert after.data["errors"] == 1
    assert after.data["unknown_count"] == 0
    assert version.parent_version == "multilingual_asr-raw"

    raw_text = next(settings.data_root.rglob("*_asr.csv")).read_text(
        encoding="utf-8-sig"
    )
    assert "ASR #2/2,,Play TuneIn" in raw_text

    rolled_back = governance.rollback_active("multilingual_asr", "rollback-owner")
    assert rolled_back.version_id == "multilingual_asr-raw"
    restored = asr.execute(ToolCall(name="dataset_overview"))
    assert restored.data["errors"] == 0
    assert restored.data["unknown_count"] == 1
