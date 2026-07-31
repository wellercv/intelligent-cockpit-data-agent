"""Optional LLM-as-Judge for open-ended answer quality."""

from __future__ import annotations

import json
from typing import Protocol, Sequence

from pydantic import BaseModel, Field

from data_insight.llm import AzureLLMGateway
from data_insight.schemas import SourceRef, ToolObservation


class JudgeResult(BaseModel):
    relevance: float = Field(ge=0, le=5)
    completeness: float = Field(ge=0, le=5)
    clarity: float = Field(ge=0, le=5)
    actionability: float = Field(ge=0, le=5)
    evidence_use: float = Field(ge=0, le=5)
    rationale: str
    policy_violations: list[str] = Field(default_factory=list)

    @property
    def average(self) -> float:
        return round(
            (
                self.relevance
                + self.completeness
                + self.clarity
                + self.actionability
                + self.evidence_use
            )
            / 5,
            4,
        )


class AnswerJudge(Protocol):
    def evaluate(
        self,
        *,
        question: str,
        answer: str,
        observations: Sequence[ToolObservation],
        sources: Sequence[SourceRef],
    ) -> JudgeResult: ...


class AzureAnswerJudge:
    """Structured Azure OpenAI judge; deterministic facts remain separately scored."""

    def __init__(self, gateway: AzureLLMGateway) -> None:
        self.gateway = gateway

    def evaluate(
        self,
        *,
        question: str,
        answer: str,
        observations: Sequence[ToolObservation],
        sources: Sequence[SourceRef],
    ) -> JudgeResult:
        payload = {
            "question": question,
            "answer": answer,
            "tool_observations": [
                item.model_dump(mode="json") for item in observations
            ],
            "sources": [item.model_dump(mode="json") for item in sources],
            "rubric": {
                "relevance": (
                    "Directly addresses the user's goal. If evidence marks the request "
                    "outside platform scope, a direct refusal is relevant."
                ),
                "completeness": (
                    "Covers the requested analysis and limitations. For out-of-scope "
                    "requests, clearly state the unavailable capability and do not invent facts."
                ),
                "clarity": "Is organized and understandable for a business user.",
                "actionability": (
                    "Provides useful next steps when evidence supports them. A safe pointer "
                    "to an approved authoritative service is actionable for out-of-scope requests."
                ),
                "evidence_use": "Uses supplied evidence and distinguishes facts from inference.",
            },
            "output_schema": JudgeResult.model_json_schema(),
        }
        system = (
            "You evaluate an intelligent-cockpit data agent. Score only open-ended "
            "answer quality from 0 to 5. Do not override deterministic fact, tool, or "
            "citation checks. Penalize unsupported claims and unsafe governance advice. "
            "When the platform_capabilities evidence says a request is out of scope, score a "
            "clear grounded refusal against that policy; do not penalize it for declining to "
            "invent or fetch an unavailable external fact. "
            "Return JSON only."
        )
        content = self.gateway.chat(
            "answer_judge",
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        return JudgeResult.model_validate_json(content)