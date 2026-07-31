"""Provider-neutral data contracts and deterministic quality scanning."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml

from data_insight.schemas import ContractField, DataContract, GovernanceFinding


class ContractRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.contracts: Dict[str, DataContract] = {}
        self.reload()

    def reload(self) -> None:
        self.contracts = {}
        if not self.root.exists():
            return
        for path in sorted(self.root.glob("*.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            contract = DataContract.model_validate(payload)
            if contract.provider in self.contracts:
                raise ValueError(f"Duplicate contract provider: {contract.provider}")
            self.contracts[contract.provider] = contract

    def get(self, provider: str) -> DataContract:
        if provider not in self.contracts:
            raise KeyError(f"No data contract registered for provider: {provider}")
        return self.contracts[provider]

    def summary(self) -> List[Dict[str, Any]]:
        return [
            {
                "provider": contract.provider,
                "contract_id": contract.contract_id,
                "version": contract.version,
                "entity": contract.entity,
                "owner": contract.owner,
                "fields": len(contract.fields),
            }
            for contract in self.contracts.values()
        ]


class ContractScanner:
    """Apply generic required/type/enum/range/uniqueness rules to records."""

    def scan(
        self,
        contract: DataContract,
        records: Iterable[Dict[str, Any]],
    ) -> List[GovernanceFinding]:
        findings: List[GovernanceFinding] = []
        seen: Dict[str, Dict[str, List[str]]] = {
            field.name: defaultdict(list)
            for field in contract.fields
            if field.unique
        }
        for row_number, record in enumerate(records, start=1):
            key = str(record.get(contract.primary_key) or f"row:{row_number}")
            source_path = str(record.get("_source_path", ""))
            for field in contract.fields:
                value = record.get(field.name)
                missing = value is None or (isinstance(value, str) and not value.strip())
                if field.required and value is None:
                    findings.append(
                        self._finding(
                            contract,
                            key,
                            field,
                            value,
                            "REQUIRED_MISSING",
                            "error",
                            f"Required field `{field.name}` is missing.",
                            source_path,
                        )
                    )
                    continue
                if (
                    field.required
                    and not field.allow_blank
                    and isinstance(value, str)
                    and not value.strip()
                ):
                    findings.append(
                        self._finding(
                            contract,
                            key,
                            field,
                            value,
                            "BLANK_NOT_ALLOWED",
                            "error",
                            f"Required field `{field.name}` is blank.",
                            source_path,
                        )
                    )
                    continue
                if missing:
                    continue
                if not self._type_matches(field, value):
                    findings.append(
                        self._finding(
                            contract,
                            key,
                            field,
                            value,
                            "TYPE_MISMATCH",
                            "error",
                            f"Field `{field.name}` expected {field.data_type}, got {type(value).__name__}.",
                            source_path,
                        )
                    )
                    continue
                if field.allowed_values and value not in field.allowed_values:
                    findings.append(
                        self._finding(
                            contract,
                            key,
                            field,
                            value,
                            "VALUE_NOT_ALLOWED",
                            "error",
                            f"Field `{field.name}` value is outside the allowed set.",
                            source_path,
                        )
                    )
                if field.data_type in {"integer", "number"}:
                    number = float(value)
                    if field.minimum is not None and number < field.minimum:
                        findings.append(
                            self._finding(
                                contract,
                                key,
                                field,
                                value,
                                "VALUE_BELOW_MINIMUM",
                                "error",
                                f"Field `{field.name}` is below minimum {field.minimum}.",
                                source_path,
                            )
                        )
                    if field.maximum is not None and number > field.maximum:
                        findings.append(
                            self._finding(
                                contract,
                                key,
                                field,
                                value,
                                "VALUE_ABOVE_MAXIMUM",
                                "error",
                                f"Field `{field.name}` exceeds maximum {field.maximum}.",
                                source_path,
                            )
                        )
                if field.unique:
                    seen[field.name][self._unique_key(value)].append(key)

        for field_name, values in seen.items():
            for serialized, keys in values.items():
                if len(keys) < 2:
                    continue
                findings.append(
                    GovernanceFinding(
                        rule_id="DUPLICATE_VALUE",
                        severity="error",
                        provider=contract.provider,
                        contract_id=contract.contract_id,
                        entity_key=keys[0],
                        field_name=field_name,
                        current_value=serialized,
                        detail=f"Field `{field_name}` is duplicated across {len(keys)} records: {', '.join(keys[:10])}.",
                    )
                )
        return findings

    @staticmethod
    def _type_matches(field: ContractField, value: Any) -> bool:
        if field.data_type == "string":
            return isinstance(value, str)
        if field.data_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if field.data_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if field.data_type == "boolean":
            return isinstance(value, bool)
        if field.data_type == "datetime":
            if isinstance(value, datetime):
                return True
            if isinstance(value, str):
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                    return True
                except ValueError:
                    return False
        return False

    @staticmethod
    def _unique_key(value: Any) -> str:
        return repr(value)

    @staticmethod
    def _finding(
        contract: DataContract,
        entity_key: str,
        field: ContractField,
        value: Any,
        rule_id: str,
        severity: str,
        detail: str,
        source_path: str,
    ) -> GovernanceFinding:
        return GovernanceFinding(
            rule_id=rule_id,
            severity=severity,
            provider=contract.provider,
            contract_id=contract.contract_id,
            entity_key=entity_key,
            field_name=field.name,
            current_value=value,
            detail=detail,
            source_path=source_path,
        )
