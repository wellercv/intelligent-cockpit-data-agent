"""Deterministic guard for high-risk governance actions."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Dict, Iterable


@dataclass(frozen=True)
class SafetyDecision:
    risk_level: str = "low"
    blocked: bool = False
    action: str | None = None
    reason: str = "No high-risk action detected."

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


class HighRiskGuard:
    """Block mutation requests while allowing explanatory policy questions."""

    _EXPLANATION_TOKENS = (
        "如何",
        "怎么",
        "为什么",
        "是什么",
        "流程",
        "说明",
        "规则",
        "能否",
        "可以吗",
        "how",
        "why",
        "what",
        "policy",
        "process",
    )
    _ACTION_PATTERNS = {
        "confirm_change": (
            r"(?:请|帮我|立即|直接|现在)?\s*(?:确认|批准|审批通过|同意)\S{0,12}(?:变更|申请|change)",
            r"(?:confirm|approve|accept)\s+(?:the\s+)?(?:change|request)",
        ),
        "publish_version": (
            r"(?:请|帮我|立即|直接|现在)?\s*(?:发布|上线)\S{0,12}(?:版本|变更|数据)",
            r"publish\s+(?:the\s+)?(?:version|change|dataset)",
        ),
        "rollback_version": (
            r"(?:请|帮我|立即|直接|现在)?\s*(?:回滚|恢复)\S{0,12}(?:版本|数据|上一个|上一版)",
            r"(?:数据|版本|数据版本)\S{0,8}(?:回滚|恢复)",
            r"roll\s*back\s+(?:the\s+)?(?:version|dataset|change)",
        ),
        "modify_raw_data": (
            r"(?:请|帮我|立即|直接|现在)?\s*(?:修改|覆盖|删除|写入)\S{0,16}(?:原始|源文件|raw|csv|json)",
            r"(?:modify|overwrite|delete|write)\S{0,12}(?:raw|csv|json|source)",
        ),
    }

    def assess(self, text: str) -> SafetyDecision:
        normalized = text.strip().casefold()
        if not normalized:
            return SafetyDecision()
        for action, patterns in self._ACTION_PATTERNS.items():
            if not self._matches_any(normalized, patterns):
                continue
            if any(token in normalized for token in self._EXPLANATION_TOKENS):
                return SafetyDecision(
                    risk_level="high",
                    blocked=False,
                    action=action,
                    reason=(
                        "High-risk governance topic detected, but the request is explanatory."
                    ),
                )
            return SafetyDecision(
                risk_level="high",
                blocked=True,
                action=action,
                reason=(
                    "High-risk governance actions require explicit confirmation in the governance workbench."
                ),
            )
        return SafetyDecision()

    @staticmethod
    def _matches_any(text: str, patterns: Iterable[str]) -> bool:
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)