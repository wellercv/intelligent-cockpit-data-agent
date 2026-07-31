import pytest

pytestmark = pytest.mark.requires_business_data


def test_overview_answer_is_grounded(service):
    answer = service.ask("当前一共接入多少条数据？")
    assert answer.grounded
    assert answer.agents_used == ["supervisor", "analysis_agent"]
    assert answer.tools_used == ["dataset_overview"]
    assert "92,301" in answer.answer_markdown
    assert answer.sources
    assert service.trace(answer.trace_id)


def test_multistep_question_replans_and_runs_parallel_tools(service):
    answer = service.ask("比较德语和法语 mediaControl 的错误率，并分别列出各自 3 条错误案例")
    assert answer.grounded
    assert answer.agents_used == [
        "supervisor",
        "analysis_agent",
    ]
    assert answer.tools_used == ["compare_languages", "search_cases", "search_cases"]
    assert answer.answer_markdown.count("ASR #") == 6
    events = service.trace(answer.trace_id)
    assert [event.event_type for event in events].count("dispatch") == 1
    assert [event.event_type for event in events].count("specialist") == 1
    assert [event.event_type for event in events].count("replan") == 1
    assert [event.event_type for event in events].count("tool") == 3
    assert [event.event_type for event in events].count("synthesis") == 1


def test_conversation_context_inherits_language(service):
    first = service.ask("法语的准确率是多少？")
    followup = service.ask("这个语言哪个 domain 错误率最高？", first.conversation_id)
    assert followup.context.selected_languages == ["French"]
    assert followup.tools_used == ["rank_dimensions"]
    assert "carControl" in followup.answer_markdown


def test_knowledge_retrieval_uses_query_rewrite(service):
    answer = service.ask("准确率指标是怎么计算的？")
    assert answer.grounded
    assert answer.agents_used == [
        "supervisor",
        "analysis_agent",
    ]
    assert answer.tools_used == ["search_knowledge"]
    assert "correct / total * 100" in answer.answer_markdown
    assert any(source.scope == "business_knowledge" for source in answer.sources)
