from data_insight.composer import AnswerComposer
from data_insight.multi_agent import AnswerSynthesizer
from data_insight.multi_agent_graph import build_multi_agent_graph
from data_insight.planner import AzurePlanner
from data_insight.schemas import (
    ConversationContext,
    PlanningDecision,
    SpecialistResult,
    ToolObservation,
    TraceEvent,
)


class FailingGateway:
    def chat(self, *args, **kwargs):
        raise RuntimeError("model unavailable")


class EmptySkills:
    def prompt_for(self, *args, **kwargs):
        return ""


class FailingComposer(AnswerComposer):
    def compose(self, question, context, observations):
        raise RuntimeError("composer unavailable")


def test_azure_planner_falls_back_to_deterministic_plan():
    planner = AzurePlanner(
        [
            {"name": "get_metrics"},
            {"name": "platform_capabilities"},
        ],
        EmptySkills(),
        FailingGateway(),
    )

    decision = planner.next_step(
        "英语准确率是多少？",
        ConversationContext(),
        [],
        0,
    )

    assert decision.status == "execute"
    assert decision.plan.calls[0].name == "get_metrics"
    assert decision.plan.calls[0].arguments["language"] == "English"
    assert decision.reason == (
        "Azure planning failed; deterministic fallback used: RuntimeError"
    )


def test_composer_failure_uses_fallback_and_records_trace():
    observation = ToolObservation(
        call_id="metrics",
        tool_name="get_metrics",
        data={
            "language": "English",
            "domain": None,
            "total": 100,
            "correct": 90,
            "errors": 10,
            "accuracy_pct": 90.0,
            "source_scope": "csv_cases",
        },
    )
    specialist_result = SpecialistResult(
        task_id="analysis",
        agent="analysis_agent",
        objective="Read metrics",
        success=True,
        observations=[observation],
    )

    class AnsweringSupervisor:
        def next_step(self, question, context, observations, round_number):
            return PlanningDecision.answer("Evidence is sufficient"), None

    class UnusedSpecialists:
        def execute(self, plan):
            raise AssertionError("specialists should not execute")

    class AcceptingVerifier:
        def verify(self, question, answer, observations):
            return True, [], []

    graph = build_multi_agent_graph(
        AnsweringSupervisor(),
        UnusedSpecialists(),
        AnswerSynthesizer(FailingComposer()),
        AcceptingVerifier(),
    )
    result = graph.invoke(
        {
            "question": "英语准确率是多少？",
            "context": ConversationContext().model_dump(mode="json"),
            "observations": [observation.model_dump(mode="json")],
            "specialist_results": [specialist_result.model_dump(mode="json")],
            "events": [],
            "rounds": 1,
        }
    )
    events = [TraceEvent.model_validate(item) for item in result["events"]]

    assert "90.00%" in result["answer"]
    synthesis = next(item for item in events if item.event_type == "synthesis")
    assert synthesis.payload["fallback_used"] is True
    assert synthesis.payload["fallback_reason"] == "RuntimeError"
    assert events[-1].name == "deterministic_fallback"