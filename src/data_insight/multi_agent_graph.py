"""LangGraph workflow for supervisor, specialists, synthesis, and verification."""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph

from data_insight.grounding import GroundingVerifier
from data_insight.multi_agent import AnswerSynthesizer, SpecialistPool, SupervisorAgent
from data_insight.schemas import (
    ConversationContext,
    MultiAgentPlan,
    PlanningDecision,
    SpecialistResult,
    ToolCall,
    ToolObservation,
    TraceEvent,
)


class MultiAgentState(TypedDict, total=False):
    question: str
    context: Dict[str, Any]
    observations: List[Dict[str, Any]]
    specialist_results: List[Dict[str, Any]]
    decision: Dict[str, Any]
    multi_agent_plan: Dict[str, Any]
    events: List[Dict[str, Any]]
    rounds: int
    answer: str
    grounded: bool
    unsupported_numbers: List[str]
    verification_warnings: List[str]


def build_multi_agent_graph(
    supervisor: SupervisorAgent,
    specialists: SpecialistPool,
    synthesis: AnswerSynthesizer,
    verifier: GroundingVerifier,
):
    def supervisor_node(state: MultiAgentState) -> MultiAgentState:
        context = ConversationContext.model_validate(state["context"])
        observations = [
            ToolObservation.model_validate(item)
            for item in state.get("observations", [])
        ]
        rounds = state.get("rounds", 0)
        try:
            decision, plan = supervisor.next_step(
                state["question"], context, observations, rounds
            )
        except Exception as error:
            if observations:
                decision = PlanningDecision.answer(
                    f"Supervisor failed after evidence collection: {error}"
                )
                plan = None
            else:
                raise
        event_type = "plan" if rounds == 0 else "replan"
        events = [
            *state.get("events", []),
            TraceEvent(
                event_type=event_type,
                name="supervisor",
                payload=decision.model_dump(mode="json"),
            ).model_dump(mode="json"),
        ]
        update: MultiAgentState = {
            "decision": decision.model_dump(mode="json"),
            "events": events,
        }
        if plan is not None:
            update["multi_agent_plan"] = plan.model_dump(mode="json")
            update["events"] = [
                *events,
                TraceEvent(
                    event_type="dispatch",
                    name="supervisor",
                    payload=plan.model_dump(mode="json"),
                ).model_dump(mode="json"),
            ]
        return update

    def route_supervisor(state: MultiAgentState) -> str:
        decision = PlanningDecision.model_validate(state["decision"])
        return (
            "specialists"
            if decision.status == "execute" and state.get("multi_agent_plan")
            else "synthesis"
        )

    def specialists_node(state: MultiAgentState) -> MultiAgentState:
        plan = MultiAgentPlan.model_validate(state["multi_agent_plan"])
        results = specialists.execute(plan)
        events = list(state.get("events", []))
        call_lookup = {
            call.call_id: call
            for task in plan.tasks
            for call in [
                ToolCall.model_validate(item)
                for item in task.context.get("suggested_calls", [])
            ]
        }
        for result in results:
            events.append(
                TraceEvent(
                    event_type="specialist",
                    name=result.agent,
                    payload={
                        "task_id": result.task_id,
                        "objective": result.objective,
                        "success": result.success,
                        "summary": result.summary,
                        "warnings": result.warnings,
                        "error": result.error,
                    },
                ).model_dump(mode="json")
            )
            for observation in result.observations:
                call = call_lookup.get(observation.call_id)
                events.append(
                    TraceEvent(
                        event_type="tool",
                        name=observation.tool_name,
                        payload={
                            "agent": result.agent,
                            "call": call.model_dump(mode="json") if call else None,
                            "observation": observation.model_dump(mode="json"),
                        },
                    ).model_dump(mode="json")
                )
        observations = [
            observation
            for result in results
            for observation in result.observations
        ]
        return {
            "observations": [
                *state.get("observations", []),
                *[item.model_dump(mode="json") for item in observations],
            ],
            "specialist_results": [
                *state.get("specialist_results", []),
                *[item.model_dump(mode="json") for item in results],
            ],
            "events": events,
            "rounds": state.get("rounds", 0) + 1,
            "multi_agent_plan": {},
        }

    def synthesis_node(state: MultiAgentState) -> MultiAgentState:
        context = ConversationContext.model_validate(state["context"])
        results = [
            SpecialistResult.model_validate(item)
            for item in state.get("specialist_results", [])
        ]
        composition = synthesis.compose_result(state["question"], context, results)
        answer = composition.answer
        observations = [
            ToolObservation.model_validate(item)
            for item in state.get("observations", [])
        ]
        grounded, unsupported, warnings = verifier.verify(
            state["question"], answer, observations
        )
        events = [
            *state.get("events", []),
            TraceEvent(
                event_type="synthesis",
                name="answer_synthesizer",
                payload={
                    "specialist_count": len({item.agent for item in results}),
                    "result_count": len(results),
                    "fallback_used": composition.fallback_used,
                    "fallback_reason": composition.fallback_reason,
                },
            ).model_dump(mode="json"),
            TraceEvent(
                event_type="verify",
                name="grounding_verifier",
                payload={
                    "grounded": grounded,
                    "unsupported_numbers": unsupported,
                    "warnings": warnings,
                },
            ).model_dump(mode="json"),
            TraceEvent(
                event_type="answer",
                name=(
                    "deterministic_fallback"
                    if composition.fallback_used
                    else "answer_synthesizer"
                ),
                payload={
                    "grounded": grounded,
                    "fallback_used": composition.fallback_used,
                },
            ).model_dump(mode="json"),
        ]
        return {
            "answer": answer,
            "grounded": grounded,
            "unsupported_numbers": unsupported,
            "verification_warnings": warnings,
            "events": events,
        }

    graph = StateGraph(MultiAgentState)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("specialists", specialists_node)
    graph.add_node("synthesis", synthesis_node)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_supervisor,
        {"specialists": "specialists", "synthesis": "synthesis"},
    )
    graph.add_edge("specialists", "supervisor")
    graph.add_edge("synthesis", END)
    return graph.compile()
