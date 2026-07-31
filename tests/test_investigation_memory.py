from __future__ import annotations

from data_insight.memory import InvestigationMemory


def test_investigation_memory_recalls_evidence_backed_history(tmp_path):
    memory = InvestigationMemory(tmp_path / "investigations")
    try:
        memory_id = memory.remember(
            question="英语准确率是多少？",
            answer="英语准确率为 94.02%，数据来自 CSV case rows。",
            conversation_id="conversation-1",
            trace_id="trace-1",
            source_paths=["English/example.csv"],
        )

        rows = memory.recall("英语准确率", limit=1)

        assert rows[0]["memory_id"] == memory_id
        assert rows[0]["trace_id"] == "trace-1"
        assert rows[0]["source_paths"] == ["English/example.csv"]
        assert memory.health()["records"] == 1
    finally:
        memory.close()