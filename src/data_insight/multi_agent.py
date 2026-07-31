"""Supervisor-led specialist agents with isolated tools and synthesis."""

from __future__ import annotations

import json
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from data_insight.composer import AnswerComposer, OfflineComposer
from data_insight.llm import AzureLLMGateway
from data_insight.planner import OfflinePlanner, Planner
from data_insight.schemas import (
    AgentPlan,
    ConversationContext,
    MultiAgentPlan,
    PlanningDecision,
    SpecialistResult,
    SpecialistTask,
    TaskUnderstanding,
    ToolCall,
    ToolObservation,
)
from data_insight.skills import SkillManager
from data_insight.tool_runtime import ToolRuntime

TOOL_OWNERS: Dict[str, str] = {
    "platform_capabilities": "analysis_agent",
    "dataset_overview": "analysis_agent",
    "get_metrics": "analysis_agent",
    "compare_languages": "analysis_agent",
    "compare_domains": "analysis_agent",
    "rank_dimensions": "analysis_agent",
    "compare_source_scopes": "analysis_agent",
    "data_quality": "analysis_agent",
    "search_cases": "analysis_agent",
    "get_case_detail": "analysis_agent",
    "nlu_report_overview": "analysis_agent",
    "nlu_compare_accuracy": "analysis_agent",
    "nlu_error_breakdown": "analysis_agent",
    "search_nlu_errors": "analysis_agent",
    "get_nlu_error_detail": "analysis_agent",
    "search_knowledge": "analysis_agent",
    "nlu_label_quality": "data_governance_agent",
    "governance_scan": "data_governance_agent",
    "list_governance_issues": "data_governance_agent",
    "get_governance_issue": "data_governance_agent",
    "list_change_requests": "data_governance_agent",
    "preview_change": "data_governance_agent",
}

ROLE_PROMPTS = {
    "analysis_agent": (
        "You are the Analysis Agent. Complete read-only business data investigations using "
        "deterministic metrics, record search, source-scope comparison, and registered knowledge. "
        "Preserve filters and sources; never calculate values or invent records yourself."
    ),
    "data_governance_agent": (
        "You are the Data Governance Agent. Inspect contract violations, tracked quality issues, "
        "change-request status, and change previews. You may create findings and drafts through "
        "registered tools, but you must never confirm, publish, roll back, or overwrite raw data."
    ),
}


class SupervisorAgent:
    """Decomposes global plans into specialist-owned tasks."""

    name = "supervisor"

    def __init__(self, planner: Planner) -> None:
        self.planner = planner

    def next_step(
        self,
        question: str,
        context: ConversationContext,
        observations: Sequence[ToolObservation],
        round_number: int,
    ) -> tuple[PlanningDecision, MultiAgentPlan | None]:
        decision = self.planner.next_step(question, context, observations, round_number)
        decision = self._normalize_decision(question, context, decision)
        if decision.status != "execute" or decision.plan is None:
            if round_number == 0 and decision.understanding is not None:
                required = self._required_initial_call(
                    question,
                    decision.understanding,
                )
                if required is not None:
                    decision = PlanningDecision.execute(
                        AgentPlan(
                            goal=question,
                            calls=[required],
                            rationale="Deterministic business-boundary tool gate",
                        ),
                        understanding=decision.understanding,
                    )
                else:
                    return decision, None
            else:
                return decision, None

        calls = list(decision.plan.calls)
        if round_number == 0:
            required = self._required_initial_call(
                question,
                decision.understanding,
            )
            calls = (
                [required]
                if required is not None
                else self._augment_initial_calls(question, context, calls)
            )
        grouped: OrderedDict[str, List[ToolCall]] = OrderedDict()
        for call in calls:
            owner = TOOL_OWNERS.get(call.name)
            if owner is None:
                raise ValueError(f"No specialist owns tool: {call.name}")
            grouped.setdefault(owner, []).append(call)

        tasks = [
            SpecialistTask(
                agent=owner,
                objective="; ".join(call.purpose or call.name for call in owned_calls),
                context={
                    "question": question,
                    "suggested_calls": [call.model_dump(mode="json") for call in owned_calls],
                    "requires_specialist_planning": False,
                },
            )
            for owner, owned_calls in grouped.items()
        ]
        all_calls = [call for owned_calls in grouped.values() for call in owned_calls]
        expanded_decision = decision.model_copy(
            update={
                "plan": AgentPlan(
                    goal=decision.plan.goal,
                    calls=all_calls,
                    rationale=decision.plan.rationale,
                )
            }
        )
        return expanded_decision, MultiAgentPlan(
            goal=decision.plan.goal,
            tasks=tasks,
            rationale=(
                "Supervisor delegated each subtask to the specialist that owns its tools."
            ),
        )

    @staticmethod
    def _normalize_decision(
        question: str,
        context: ConversationContext,
        decision: PlanningDecision,
    ) -> PlanningDecision:
        deterministic = OfflinePlanner.understand(question, context)
        llm = decision.understanding
        if llm is None:
            normalized = deterministic
        else:
            normalized = TaskUnderstanding(
                intent=deterministic.intent,
                languages=deterministic.languages,
                domains=deterministic.domains,
                metric=deterministic.metric,
                metrics=deterministic.metrics,
                case_id=deterministic.case_id or llm.case_id,
                case_no=deterministic.case_no or llm.case_no,
                issue_id=deterministic.issue_id or llm.issue_id,
                source_scope=deterministic.source_scope or llm.source_scope,
                source_scopes=deterministic.source_scopes,
                risk_level=deterministic.risk_level,
                needs_clarification=llm.needs_clarification,
            )
        return decision.model_copy(update={"understanding": normalized})

    @staticmethod
    def _required_initial_call(
        question: str,
        understanding: TaskUnderstanding | None,
    ) -> ToolCall | None:
        if understanding is None:
            return None
        lower = question.casefold()
        if "nlu" in lower and understanding.intent == "data_governance":
            return ToolCall(
                name="nlu_label_quality",
                arguments={"limit": 20},
                purpose="Inspect NLU label-quality findings without modifying the report",
            )
        if understanding.intent == "out_of_scope":
            return ToolCall(
                name="platform_capabilities",
                purpose="Explain the supported platform scope without inventing facts",
            )
        if understanding.intent == "data_governance" and any(
            token in lower
            for token in (
                "有哪些质量问题",
                "扫描数据质量",
                "扫描当前数据质量",
                "治理扫描",
                "scan data quality",
            )
        ):
            return ToolCall(
                name="governance_scan",
                arguments={"provider": "multilingual_asr"},
                purpose="Scan the registered data contract and persist governance issues",
            )
        return None

    @staticmethod
    def _augment_initial_calls(
        question: str,
        context: ConversationContext,
        calls: List[ToolCall],
    ) -> List[ToolCall]:
        """Fan out independent explicit subtasks in the first supervisor round."""

        lower = question.casefold()
        names = {call.name for call in calls}
        languages = OfflinePlanner.extract_languages(question)
        domains = OfflinePlanner.extract_domains(question)
        if not languages and OfflinePlanner._uses_context(lower):
            languages = list(context.selected_languages)
        if not domains and OfflinePlanner._uses_context(lower):
            domains = list(context.selected_domains)

        mentions_nlu = "nlu" in lower or "语义理解" in lower
        mentions_asr = "asr" in lower or "语音识别" in lower
        if mentions_nlu and mentions_asr:
            if "nlu_report_overview" not in names:
                calls.append(
                    ToolCall(
                        name="nlu_report_overview",
                        purpose="Read the NLU report baseline for cross-provider comparison",
                    )
                )
                names.add("nlu_report_overview")
            if "dataset_overview" not in names:
                calls.append(
                    ToolCall(
                        name="dataset_overview",
                        purpose="Read the ASR baseline for cross-provider comparison",
                    )
                )
                names.add("dataset_overview")

        governance_calls = {
            "governance_scan",
            "list_governance_issues",
            "get_governance_issue",
            "list_change_requests",
            "preview_change",
        }
        wants_impact = any(
            token in lower
            for token in ("指标影响", "影响指标", "影响分析", "整体指标", "数据规模")
        )
        if names & governance_calls and wants_impact:
            calls.append(
                ToolCall(
                    name="get_metrics" if languages or domains else "dataset_overview",
                    arguments={
                        **(
                            {"language": languages[0]}
                            if len(languages) == 1
                            else {}
                        ),
                        **({"domain": domains[0]} if len(domains) == 1 else {}),
                        **(
                            {"source_scope": "csv_cases"}
                            if languages or domains
                            else {}
                        ),
                    },
                    purpose="Establish the current read-only metric baseline for impact review",
                )
            )
            names.add(calls[-1].name)

        wants_cases = any(
            token in lower
            for token in ("案例", "样本", "cases", "examples", "列出", "找出")
        )
        has_metric_call = bool(
            names
            & {
                "dataset_overview",
                "get_metrics",
                "compare_languages",
                "compare_domains",
                "rank_dimensions",
            }
        )
        if wants_cases and has_metric_call and languages and "search_cases" not in names:
            requested = OfflinePlanner._requested_limit(lower, 5)
            for language in languages[:4]:
                calls.append(
                    ToolCall(
                        name="search_cases",
                        arguments={
                            "languages": [language],
                            "domains": domains,
                            "result": "error",
                            "limit": requested,
                        },
                        purpose=f"Retrieve {requested} error examples for {language}",
                    )
                )

        asks_definition = any(
            token in lower
            for token in (
                "定义",
                "怎么算",
                "怎么计算",
                "如何计算",
                "含义",
                "为什么",
                "definition",
                "explain",
            )
        )
        if (
            "search_knowledge" in names
            and OfflinePlanner._is_metrics(lower)
            and (languages or domains)
            and not has_metric_call
        ):
            if OfflinePlanner._is_compare(lower) and len(languages) > 1:
                metric_call = ToolCall(
                    name="compare_languages",
                    arguments={
                        "languages": languages,
                        "domain": domains[0] if len(domains) == 1 else None,
                    },
                    purpose="Calculate the requested language comparison",
                )
            elif OfflinePlanner._is_compare(lower) and len(domains) > 1:
                metric_call = ToolCall(
                    name="compare_domains",
                    arguments={
                        "language": languages[0] if len(languages) == 1 else None
                    },
                    purpose="Calculate the requested domain comparison",
                )
            else:
                metric_call = ToolCall(
                    name="get_metrics",
                    arguments={
                        "language": languages[0] if len(languages) == 1 else None,
                        "domain": domains[0] if len(domains) == 1 else None,
                        "source_scope": "csv_cases",
                    },
                    purpose="Calculate the requested metrics",
                )
            calls.insert(0, metric_call)
            names.add(metric_call.name)
            has_metric_call = True
        if asks_definition and has_metric_call and "search_knowledge" not in names:
            calls.append(
                ToolCall(
                    name="search_knowledge",
                    arguments={"query": question, "limit": 5},
                    purpose="Retrieve the business definition and source-scope policy",
                )
            )
        return calls


class SpecialistAgent:
    """A role-scoped worker with an isolated tool set and optional LLM planning."""

    def __init__(
        self,
        role: str,
        allowed_tools: Sequence[str],
        runtime: ToolRuntime,
        tool_catalog: Sequence[Dict[str, Any]],
        skills: SkillManager,
        gateway: AzureLLMGateway | None = None,
    ) -> None:
        self.role = role
        self.allowed_tools = set(allowed_tools)
        self.runtime = runtime
        self.skills = skills
        self.gateway = gateway
        self.tool_specs = {
            item["name"]: item
            for item in tool_catalog
            if item["name"] in self.allowed_tools
        }

    def run(self, task: SpecialistTask) -> SpecialistResult:
        try:
            suggested = [
                ToolCall.model_validate(item)
                for item in task.context.get("suggested_calls", [])
            ]
            planning_warning = None
            used_llm_planning = bool(
                self.gateway and self._needs_llm_refinement(task, suggested)
            )
            if used_llm_planning:
                try:
                    calls = self._refine_with_llm(task, suggested)
                except Exception as error:
                    calls = suggested
                    planning_warning = (
                        f"{self.role} LLM planning failed; used supervisor calls: "
                        f"{type(error).__name__}"
                    )
            else:
                calls = suggested
            if not calls:
                raise ValueError(f"{self.role} received no executable calls")
            disallowed = [call.name for call in calls if call.name not in self.allowed_tools]
            if disallowed:
                raise PermissionError(
                    f"{self.role} cannot execute tools: {', '.join(disallowed)}"
                )
            with ThreadPoolExecutor(max_workers=min(len(calls), 4)) as pool:
                observations = list(pool.map(self.runtime.execute, calls))
            success = all(item.success for item in observations)
            warnings = list(
                dict.fromkeys(
                    ([planning_warning] if planning_warning else [])
                    + [
                        warning
                        for observation in observations
                        for warning in observation.warnings
                    ]
                )
            )
            return SpecialistResult(
                task_id=task.task_id,
                agent=self.role,
                objective=task.objective,
                success=success,
                observations=observations,
                summary=(
                    f"{self.role} completed {len(observations)} tool call(s); "
                    f"{sum(len(item.rows) for item in observations)} row(s) returned; "
                    f"planning={'llm' if used_llm_planning else 'direct'}."
                ),
                warnings=warnings,
                error="; ".join(
                    item.error for item in observations if item.error
                )
                or None,
            )
        except Exception as error:
            return SpecialistResult(
                task_id=task.task_id,
                agent=self.role,
                objective=task.objective,
                success=False,
                error=f"{type(error).__name__}: {error}",
            )

    def _needs_llm_refinement(
        self,
        task: SpecialistTask,
        suggested: Sequence[ToolCall],
    ) -> bool:
        if task.context.get("requires_specialist_planning") is True:
            return True
        if not suggested:
            return True
        for call in suggested:
            specification = self.tool_specs.get(call.name)
            if specification is None:
                return True
            required = specification.get("parameters", {}).get("required", [])
            if any(name not in call.arguments for name in required):
                return True
        return False

    def _refine_with_llm(
        self,
        task: SpecialistTask,
        suggested: Sequence[ToolCall],
    ) -> List[ToolCall]:
        assert self.gateway is not None
        payload = {
            "task": task.model_dump(mode="json"),
            "allowed_tools": list(self.tool_specs.values()),
            "suggested_calls": [item.model_dump(mode="json") for item in suggested],
            "output_schema": AgentPlan.model_json_schema(),
            "skills": self.skills.prompt_for(
                str(task.context.get("question", task.objective)), self.role
            ),
        }
        system = (
            ROLE_PROMPTS[self.role]
            + " Validate or refine the suggested calls within your tool boundary. "
            "Prefer the suggested deterministic calls when they already satisfy the task. "
            "Return JSON only matching AgentPlan."
        )
        content = self.gateway.chat(
            f"specialist_planning:{self.role}",
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        plan = AgentPlan.model_validate_json(content)
        return list(plan.calls)


class SpecialistPool:
    """Runs independent specialist tasks concurrently."""

    def __init__(self, agents: Sequence[SpecialistAgent]) -> None:
        self.agents = {agent.role: agent for agent in agents}

    def execute(self, plan: MultiAgentPlan) -> List[SpecialistResult]:
        missing = [task.agent for task in plan.tasks if task.agent not in self.agents]
        if missing:
            raise ValueError(f"No specialist registered for: {', '.join(missing)}")
        with ThreadPoolExecutor(max_workers=min(len(plan.tasks), len(self.agents))) as pool:
            return list(
                pool.map(
                    lambda task: self.agents[task.agent].run(task),
                    plan.tasks,
                )
            )

    def health(self) -> Dict[str, Any]:
        return {
            role: {"ready": True, "allowed_tools": sorted(agent.allowed_tools)}
            for role, agent in self.agents.items()
        }


@dataclass(frozen=True)
class SynthesisResult:
    answer: str
    fallback_used: bool = False
    fallback_reason: str | None = None


class AnswerSynthesizer:
    """Non-agent component that combines evidence into one grounded answer."""

    name = "answer_synthesizer"

    def __init__(self, composer: AnswerComposer) -> None:
        self.composer = composer

    def compose(
        self,
        question: str,
        context: ConversationContext,
        results: Sequence[SpecialistResult],
    ) -> str:
        return self.compose_result(question, context, results).answer

    def compose_result(
        self,
        question: str,
        context: ConversationContext,
        results: Sequence[SpecialistResult],
    ) -> SynthesisResult:
        observations = [
            observation
            for result in results
            for observation in result.observations
        ]
        if observations and all(
            observation.tool_name == "platform_capabilities"
            for observation in observations
        ):
            return SynthesisResult(
                OfflineComposer().compose(question, context, observations)
            )
        try:
            return SynthesisResult(
                self.composer.compose(question, context, observations)
            )
        except Exception as error:
            return SynthesisResult(
                answer=OfflineComposer().compose(
                    question,
                    context,
                    observations,
                ),
                fallback_used=True,
                fallback_reason=type(error).__name__,
            )


def build_specialist_pool(
    runtime: ToolRuntime,
    tool_catalog: Sequence[Dict[str, Any]],
    skills: SkillManager,
    gateway: AzureLLMGateway | None = None,
) -> SpecialistPool:
    role_tools: Dict[str, List[str]] = {
        "analysis_agent": [
            tool for tool, owner in TOOL_OWNERS.items() if owner == "analysis_agent"
        ],
        "data_governance_agent": [
            tool
            for tool, owner in TOOL_OWNERS.items()
            if owner == "data_governance_agent"
        ],
    }
    return SpecialistPool(
        [
            SpecialistAgent(
                role,
                allowed,
                runtime,
                tool_catalog,
                skills,
                gateway,
            )
            for role, allowed in role_tools.items()
        ]
    )
