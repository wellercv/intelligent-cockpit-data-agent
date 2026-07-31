"""Deterministic number and citation checks for generated answers."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import List, Sequence, Tuple

from data_insight.config import Settings
from data_insight.schemas import SourceRef, ToolObservation

_NUMBER_RE = re.compile(r"(?<![\w-])\d[\d,]*(?:\.\d+)?%?")
_CASE_ID_RE = re.compile(
    r"\b(?:arabic|english|french|german|italian|portuguese|spanish)-[a-z]+-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_ISSUE_ID_RE = re.compile(r"\bDQ-[0-9a-f]{16}\b", re.IGNORECASE)
_NLU_ID_RE = re.compile(r"\bNLU-(?:ERR|LABEL|GOV)-[0-9a-f]{12}\b", re.IGNORECASE)
_ENTITY_ALIASES = {
    "Arabic": ("arabic", "阿拉伯语"),
    "English": ("english", "英语", "英文"),
    "French": ("french", "法语"),
    "German": ("german", "德语"),
    "Italian": ("italian", "意大利语"),
    "Portuguese": ("portuguese", "葡萄牙语"),
    "Spanish": ("spanish", "西班牙语"),
    "carControl": ("carcontrol", "car control", "车辆控制"),
    "generalControl": ("generalcontrol", "general control", "通用控制"),
    "mediaControl": ("mediacontrol", "media control", "媒体控制"),
    "naviControl": ("navicontrol", "navi control", "导航控制"),
    "phone": ("phone domain", "phone control", "电话领域", "电话控制"),
    "systemControl": ("systemcontrol", "system control", "系统控制"),
    "Saudi_Arabic": ("saudi_arabic", "saudi arabic", "沙特阿拉伯语"),
}


class GroundingVerifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def verify(self, question: str, answer: str, observations: Sequence[ToolObservation]) -> Tuple[bool, List[str], List[str]]:
        payload = [item.model_dump(mode="json") for item in observations]
        evidence = json.dumps(payload, ensure_ascii=False, default=str)
        evidence = f"{evidence}\n{' '.join(self._scalar_values(payload))}"
        allowed = {self._normalize(item) for item in _NUMBER_RE.findall(evidence + " " + question)}
        unsupported = sorted({item for item in _NUMBER_RE.findall(answer) if self._normalize(item) not in allowed})
        citation_warnings = self._entity_warnings(
            answer,
            evidence + " " + question,
        )
        for source in self.sources(observations):
            if source.source_id == "warehouse":
                path = self.settings.project_root / source.path
            elif source.path.startswith(
                ("data/", "knowledge/", "config/", "eval/", "skills/")
            ):
                path = self.settings.project_root / source.path
            else:
                path = self.settings.data_root / source.path
            if not path.exists():
                citation_warnings.append(f"Source path does not exist: {source.path}")
            expected_scope = self._expected_scope(source.path)
            if expected_scope and source.scope != expected_scope:
                citation_warnings.append(
                    f"Source scope mismatch for {source.path}: "
                    f"expected {expected_scope}, got {source.scope}"
                )
        return not unsupported and not citation_warnings, unsupported, citation_warnings

    @classmethod
    def _scalar_values(cls, value) -> List[str]:
        if isinstance(value, dict):
            return [
                item
                for nested in value.values()
                for item in cls._scalar_values(nested)
            ]
        if isinstance(value, (list, tuple)):
            return [
                item
                for nested in value
                for item in cls._scalar_values(nested)
            ]
        return [] if value is None else [str(value)]

    @staticmethod
    def sources(observations: Sequence[ToolObservation]) -> List[SourceRef]:
        seen, result = set(), []
        for observation in observations:
            for source in observation.sources:
                key = (source.path, source.scope)
                if key in seen:
                    continue
                seen.add(key)
                result.append(source)
        return result

    @staticmethod
    def _normalize(value: str) -> str:
        cleaned = value.replace(",", "").removesuffix("%")
        try:
            return format(Decimal(cleaned).normalize(), "f")
        except InvalidOperation:
            return cleaned

    @classmethod
    def _entity_warnings(cls, answer: str, evidence: str) -> List[str]:
        allowed_entities = cls._entities(evidence)
        answer_entities = cls._entities(answer)
        warnings = [
            f"Unsupported entity in answer: {entity}"
            for entity in sorted(answer_entities - allowed_entities)
        ]
        allowed_ids = {
            item.casefold()
            for pattern in (_CASE_ID_RE, _ISSUE_ID_RE, _NLU_ID_RE)
            for item in pattern.findall(evidence)
        }
        answer_ids = {
            item.casefold()
            for pattern in (_CASE_ID_RE, _ISSUE_ID_RE, _NLU_ID_RE)
            for item in pattern.findall(answer)
        }
        warnings.extend(
            f"Unsupported identifier in answer: {identifier}"
            for identifier in sorted(answer_ids - allowed_ids)
        )
        return warnings

    @staticmethod
    def _entities(text: str) -> set[str]:
        lower = text.casefold()
        result: set[str] = set()
        for canonical, aliases in _ENTITY_ALIASES.items():
            for alias in aliases:
                folded = alias.casefold()
                if any("\u3400" <= char <= "\u9fff" for char in folded):
                    matched = folded in lower
                else:
                    matched = bool(
                        re.search(
                            rf"(?<![\w-]){re.escape(folded)}(?![\w-])",
                            lower,
                        )
                    )
                if matched:
                    result.add(canonical)
                    break
        return result

    @staticmethod
    def _expected_scope(path: str) -> str | None:
        if path.startswith("knowledge/"):
            return "business_knowledge"
        if path.startswith("config/contracts/"):
            return "data_contract"
        if path == "config/sources.yaml":
            return "platform_configuration"
        if path == "data/agent_state.db":
            return "governance_state"
        if path.casefold().endswith(".xlsx") and "nlu" in path.casefold():
            return "nlu_evaluation_report"
        return None
