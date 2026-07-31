"""Stable contracts shared by planners, tools, graph, API, and UI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

AgentRole = Literal[
    "analysis_agent",
    "data_governance_agent",
]
TaskIntent = Literal[
    "metric_analysis",
    "case_investigation",
    "knowledge_qa",
    "data_governance",
    "mixed",
    "out_of_scope",
]

GovernanceIssueStatus = Literal[
    "OPEN",
    "IN_REVIEW",
    "RESOLVED",
    "WAIVED",
]
ChangeStatus = Literal[
    "DRAFT",
    "CONFIRMED",
    "PENDING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "PUBLISHED",
    "ROLLED_BACK",
]


class SourceRef(BaseModel):
    source_id: str
    label: str
    path: str
    scope: str
    warning: Optional[str] = None


class ToolCall(BaseModel):
    call_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    purpose: str = ""


class AgentPlan(BaseModel):
    goal: str
    calls: List[ToolCall] = Field(min_length=1, max_length=8)
    rationale: str = ""


class TaskUnderstanding(BaseModel):
    intent: TaskIntent
    languages: List[str] = Field(default_factory=list)
    domains: List[str] = Field(default_factory=list)
    metric: Optional[str] = None
    metrics: List[str] = Field(default_factory=list)
    case_id: Optional[str] = None
    case_no: Optional[str] = None
    issue_id: Optional[str] = None
    source_scope: Optional[str] = None
    source_scopes: List[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    needs_clarification: bool = False


class PlanningDecision(BaseModel):
    status: Literal["execute", "answer"]
    plan: Optional[AgentPlan] = None
    reason: str = ""
    understanding: Optional[TaskUnderstanding] = None

    @classmethod
    def execute(
        cls,
        plan: AgentPlan,
        reason: str = "",
        understanding: Optional[TaskUnderstanding] = None,
    ) -> "PlanningDecision":
        return cls(
            status="execute",
            plan=plan,
            reason=reason,
            understanding=understanding,
        )

    @classmethod
    def answer(
        cls,
        reason: str = "",
        understanding: Optional[TaskUnderstanding] = None,
    ) -> "PlanningDecision":
        return cls(status="answer", reason=reason, understanding=understanding)


class SpecialistTask(BaseModel):
    task_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    agent: AgentRole
    objective: str
    depends_on: List[str] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)


class MultiAgentPlan(BaseModel):
    goal: str
    tasks: List[SpecialistTask] = Field(min_length=1, max_length=8)
    rationale: str = ""


class SpecialistResult(BaseModel):
    task_id: str
    agent: AgentRole
    objective: str
    success: bool
    observations: List["ToolObservation"] = Field(default_factory=list)
    summary: str = ""
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class ContractField(BaseModel):
    name: str
    data_type: Literal["string", "integer", "number", "boolean", "datetime"] = "string"
    required: bool = False
    allow_blank: bool = True
    unique: bool = False
    allowed_values: List[Any] = Field(default_factory=list)
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    mutable: bool = False
    description: str = ""


class DataContract(BaseModel):
    contract_id: str
    version: str
    provider: str
    entity: str
    primary_key: str
    owner: str = "unassigned"
    authoritative_source: str = ""
    fields: List[ContractField] = Field(min_length=1)

    def field(self, name: str) -> Optional[ContractField]:
        return next((item for item in self.fields if item.name == name), None)


class GovernanceFinding(BaseModel):
    rule_id: str
    severity: Literal["info", "warning", "error", "critical"]
    provider: str
    contract_id: str
    entity_key: str
    field_name: Optional[str] = None
    current_value: Any = None
    detail: str
    source_path: str = ""


class GovernanceIssue(BaseModel):
    issue_id: str
    finding: GovernanceFinding
    status: GovernanceIssueStatus = "OPEN"
    owner: Optional[str] = None
    resolution: Optional[str] = None
    created_at: str
    updated_at: str


class ChangeRequest(BaseModel):
    change_id: str
    issue_id: str
    provider: str
    entity_key: str
    field_name: str
    before_value: Any = None
    proposed_value: Any = None
    reason: str
    status: ChangeStatus = "DRAFT"
    requested_by: str
    reviewed_by: Optional[str] = None
    review_comment: Optional[str] = None
    base_version: Optional[str] = None
    target_version: Optional[str] = None
    created_at: str
    updated_at: str


class DatasetVersion(BaseModel):
    version_id: str
    provider: str
    parent_version: Optional[str] = None
    status: Literal["ACTIVE", "ARCHIVED"]
    patches: List[Dict[str, Any]] = Field(default_factory=list)
    created_by: str
    approved_by: str
    created_at: str


class ToolObservation(BaseModel):
    call_id: str
    tool_name: str
    success: bool = True
    data: Dict[str, Any] = Field(default_factory=dict)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    sources: List[SourceRef] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    elapsed_ms: float = 0.0
    cached: bool = False


class ConversationContext(BaseModel):
    conversation_id: str = Field(default_factory=lambda: uuid4().hex)
    selected_languages: List[str] = Field(default_factory=list)
    selected_domains: List[str] = Field(default_factory=list)
    last_case_ids: List[str] = Field(default_factory=list)
    last_question: Optional[str] = None
    summary: str = ""


class TraceEvent(BaseModel):
    event_type: Literal[
        "plan",
        "dispatch",
        "specialist",
        "tool",
        "replan",
        "synthesis",
        "verify",
        "answer",
        "error",
    ]
    name: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AgentAnswer(BaseModel):
    question: str
    answer_markdown: str
    conversation_id: str
    trace_id: str
    agents_used: List[str] = Field(default_factory=list)
    tools_used: List[str] = Field(default_factory=list)
    specialist_results: List[SpecialistResult] = Field(default_factory=list)
    observations: List[ToolObservation] = Field(default_factory=list)
    sources: List[SourceRef] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    context: ConversationContext = Field(default_factory=ConversationContext)
    grounded: bool = True
    unsupported_numbers: List[str] = Field(default_factory=list)


class EvaluationCase(BaseModel):
    case_id: str
    question: str
    label_source: Literal["human_confirmed", "synthetic_template", "imported"] = (
        "human_confirmed"
    )
    template_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    expected_tools: List[str] = Field(default_factory=list)
    expected_agents: List[str] = Field(default_factory=list)
    expected_intent: Optional[TaskIntent] = None
    expected_entities: Dict[str, Any] = Field(default_factory=dict)
    answer_contains: List[str] = Field(default_factory=list)
    min_sources: int = 1


class EvaluationResult(BaseModel):
    case_id: str
    passed: bool
    tool_match: bool
    agent_match: bool = True
    intent_match: bool = True
    entity_match: bool = True
    answer_match: bool
    citation_match: bool
    elapsed_ms: float
    details: Dict[str, Any] = Field(default_factory=dict)
