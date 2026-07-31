from data_insight.export import answer_csv_bytes
from data_insight.schemas import AgentAnswer, ToolObservation


def test_answer_csv_export_flattens_observations():
    answer = AgentAnswer(
        question="比较英语结果",
        answer_markdown="ok",
        conversation_id="conversation",
        trace_id="trace-123",
        observations=[
            ToolObservation(
                call_id="1",
                tool_name="table_tool",
                rows=[{"language": "English", "errors": 2}],
            ),
            ToolObservation(
                call_id="2",
                tool_name="object_tool",
                data={"summary": {"total": 3}},
            ),
        ],
    )

    exported = answer_csv_bytes(answer).decode("utf-8-sig")

    assert "trace-123" in exported
    assert "比较英语结果" in exported
    assert "table_tool" in exported
    assert "English" in exported
    assert "object_tool" in exported
    assert "summary.total" in exported