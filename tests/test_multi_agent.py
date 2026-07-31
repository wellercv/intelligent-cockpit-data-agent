import pytest

from data_insight.composer import AnswerComposer
from data_insight.multi_agent import (
    AnswerSynthesizer,
    SupervisorAgent,
    build_specialist_pool,
)
from data_insight.planner import OfflinePlanner
from data_insight.schemas import (
    AgentPlan,
    ConversationContext,
    PlanningDecision,
    SpecialistResult,
    SpecialistTask,
    TaskUnderstanding,
    ToolCall,
    ToolObservation,
)

pytestmark = pytest.mark.requires_business_data


def test_supervisor_delegates_compound_question(service):
    supervisor = SupervisorAgent(OfflinePlanner(service.provider.tool_catalog()))
    decision, plan = supervisor.next_step(
        "比较德语和法语 mediaControl 的错误率，并分别列出各自 3 条错误案例",
        ConversationContext(),
        [],
        0,
    )
    assert plan is not None
    assert [task.agent for task in plan.tasks] == ["analysis_agent"]
    assert len(plan.tasks[0].context["suggested_calls"]) == 3
    assert plan.tasks[0].context["requires_specialist_planning"] is False
    assert decision.understanding.intent == "mixed"
    assert decision.understanding.languages == ["French", "German"]
    assert decision.understanding.domains == ["mediaControl"]
    assert decision.understanding.metric == "error_rate"
    assert decision.understanding.metrics == ["error_rate"]


def test_task_understanding_preserves_multiple_metrics_and_scopes():
    understanding = OfflinePlanner.understand(
        "比较英语 CSV 和 JSON 的错误数与准确率",
        ConversationContext(),
    )

    assert understanding.intent == "metric_analysis"
    assert understanding.languages == ["English"]
    assert understanding.metric == "accuracy"
    assert understanding.metrics == ["accuracy", "errors"]
    assert understanding.source_scope == "csv_cases"
    assert understanding.source_scopes == ["csv_cases", "json_summary"]


def test_supervisor_marks_single_tool_task_for_direct_execution(service):
    supervisor = SupervisorAgent(OfflinePlanner(service.provider.tool_catalog()))
    _, plan = supervisor.next_step(
        "英语准确率是多少？",
        ConversationContext(),
        [],
        0,
    )

    assert plan is not None
    assert len(plan.tasks[0].context["suggested_calls"]) == 1
    assert plan.tasks[0].context["requires_specialist_planning"] is False


def test_supervisor_enforces_scope_and_governance_tool_gates(service):
    class AnsweringPlanner:
        def next_step(self, question, context, observations, round_number):
            return PlanningDecision.answer("No tool selected")

    supervisor = SupervisorAgent(AnsweringPlanner())

    weather, weather_plan = supervisor.next_step(
        "北京今天天气怎么样？", ConversationContext(), [], 0
    )
    assert weather.plan.calls[0].name == "platform_capabilities"
    assert weather_plan.tasks[0].agent == "analysis_agent"

    governance, governance_plan = supervisor.next_step(
        "当前数据有哪些质量问题？", ConversationContext(), [], 0
    )
    assert [call.name for call in governance.plan.calls] == ["governance_scan"]
    assert governance_plan.tasks[0].agent == "data_governance_agent"


def test_supervisor_normalizes_llm_entities(service):
    class ReorderedPlanner:
        def next_step(self, question, context, observations, round_number):
            return PlanningDecision.execute(
                AgentPlan(
                    goal=question,
                    calls=[
                        ToolCall(
                            name="compare_languages",
                            arguments={
                                "languages": ["German", "French"],
                                "domain": "mediaControl",
                            },
                        )
                    ],
                ),
                understanding=TaskUnderstanding(
                    intent="metric_analysis",
                    languages=["German", "French"],
                    domains=["mediaControl"],
                    metrics=["error_rate"],
                ),
            )

    decision, _ = SupervisorAgent(ReorderedPlanner()).next_step(
        "比较德语和法语 mediaControl 的错误率",
        ConversationContext(),
        [],
        0,
    )
    assert decision.understanding.intent == "metric_analysis"
    assert decision.understanding.languages == ["French", "German"]
    assert decision.understanding.metric == "error_rate"
    assert decision.understanding.metrics == ["error_rate"]


def test_out_of_scope_answer_uses_deterministic_template():
    class UnexpectedComposer(AnswerComposer):
        def compose(self, question, context, observations):
            raise AssertionError("LLM composer must not rewrite scope rejection")

    observation = ToolObservation(
        call_id="scope",
        tool_name="platform_capabilities",
        data={"supported": ["metrics"], "examples": ["查询准确率"]},
    )
    result = SpecialistResult(
        task_id="scope-task",
        agent="analysis_agent",
        objective="Explain scope",
        success=True,
        observations=[observation],
    )

    answer = AnswerSynthesizer(UnexpectedComposer()).compose(
        "北京今天天气怎么样？",
        ConversationContext(),
        [result],
    )

    assert "超出了当前已注册的数据范围" in answer
    assert "没有生成无来源答案" in answer


def test_trace_records_structured_task_understanding(service):
    answer = service.ask("英语 mediaControl 的错误率是多少？")

    plan = next(event for event in service.trace(answer.trace_id) if event.event_type == "plan")
    understanding = plan.payload["understanding"]
    assert understanding["intent"] == "metric_analysis"
    assert understanding["languages"] == ["English"]
    assert understanding["domains"] == ["mediaControl"]
    assert understanding["metric"] == "error_rate"
    assert understanding["metrics"] == ["error_rate"]


def test_specialist_tool_permissions_are_enforced(service):
    pool = build_specialist_pool(
        service.runtime,
        service.provider.tool_catalog(),
        service.skills,
    )
    analysis_task = SpecialistTask(
        agent="analysis_agent",
        objective="Attempt governance scan",
        context={
            "suggested_calls": [
                ToolCall(name="governance_scan").model_dump(mode="json")
            ]
        },
    )
    analysis_result = pool.agents["analysis_agent"].run(analysis_task)
    assert analysis_result.success is False
    assert "cannot execute tools: governance_scan" in analysis_result.error

    governance_task = SpecialistTask(
        agent="data_governance_agent",
        objective="Attempt metric query",
        context={
            "suggested_calls": [
                ToolCall(name="get_metrics").model_dump(mode="json")
            ]
        },
    )
    governance_result = pool.agents["data_governance_agent"].run(
        governance_task
    )
    assert governance_result.success is False
    assert "cannot execute tools: get_metrics" in governance_result.error


def test_analysis_agent_combines_metrics_and_knowledge(service):
    answer = service.ask("英语准确率是多少，并解释准确率怎么计算？")
    assert answer.agents_used == ["supervisor", "analysis_agent"]
    assert answer.tools_used == ["get_metrics", "search_knowledge"]
    assert "94.02" in answer.answer_markdown
    assert "correct / total * 100" in answer.answer_markdown


def test_specialist_skips_llm_when_supervisor_call_is_complete(service):
    class CountingGateway:
        calls = 0

        def chat(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("simple task should not call the specialist LLM")

    gateway = CountingGateway()
    pool = build_specialist_pool(
        service.runtime,
        service.provider.tool_catalog(),
        service.skills,
        gateway,
    )
    task = SpecialistTask(
        agent="analysis_agent",
        objective="Read English metrics",
        context={
            "suggested_calls": [
                ToolCall(
                    name="get_metrics", arguments={"language": "English"}
                ).model_dump(mode="json")
            ]
        },
    )
    result = pool.agents["analysis_agent"].run(task)
    assert result.success is True
    assert result.observations[0].data["errors"] == 893
    assert gateway.calls == 0
    assert "planning=direct" in result.summary


def test_specialist_llm_failure_falls_back_to_supervisor_call(service):
    class FailingGateway:
        def chat(self, *args, **kwargs):
            raise RuntimeError("model unavailable")

    pool = build_specialist_pool(
        service.runtime,
        service.provider.tool_catalog(),
        service.skills,
        FailingGateway(),
    )
    task = SpecialistTask(
        agent="analysis_agent",
        objective="Investigate English metrics and supporting evidence",
        context={
            "requires_specialist_planning": True,
            "suggested_calls": [
                ToolCall(
                    name="get_metrics", arguments={"language": "English"}
                ).model_dump(mode="json")
            ],
        },
    )
    result = pool.agents["analysis_agent"].run(task)
    assert result.success is True
    assert result.observations[0].data["errors"] == 893
    assert any("used supervisor calls" in item for item in result.warnings)
    assert "planning=llm" in result.summary


def test_supervisor_replans_between_specialists(service):
    answer = service.ask("找出错误率最高的两个语言，并分别列出 2 条错误案例")
    assert answer.agents_used == ["supervisor", "analysis_agent"]
    assert answer.tools_used == [
        "rank_dimensions",
        "search_cases",
        "search_cases",
    ]
    events = service.trace(answer.trace_id)
    assert [event.event_type for event in events].count("dispatch") == 2
    assert [event.event_type for event in events].count("replan") == 2
    assert [event.event_type for event in events].count("specialist") == 2
    assert "Portuguese" in answer.answer_markdown
    assert "Arabic" in answer.answer_markdown


def test_supervisor_coordinates_analysis_and_governance(service):
    answer = service.ask("扫描当前数据质量问题，并分析整体指标影响")
    assert answer.agents_used == [
        "supervisor",
        "data_governance_agent",
        "analysis_agent",
    ]
    assert answer.tools_used == ["governance_scan", "dataset_overview"]
    assert "44" in answer.answer_markdown
    assert "92,301" in answer.answer_markdown