from __future__ import annotations

import pytest

from data_insight.safety import HighRiskGuard


def test_high_risk_guard_blocks_execution_but_allows_explanation():
    guard = HighRiskGuard()

    blocked = guard.assess("请直接回滚到上一个数据版本")
    explanatory = guard.assess("数据版本回滚流程是什么？")

    assert blocked.blocked is True
    assert blocked.action == "rollback_version"
    assert explanatory.blocked is False
    assert explanatory.risk_level == "high"

    confirmation = guard.assess("请确认并应用这个变更")
    assert confirmation.blocked is True
    assert confirmation.action == "confirm_change"


@pytest.mark.requires_business_data
def test_service_blocks_high_risk_action_before_agent_planning(service):
    answer = service.ask("请直接发布这个数据版本")

    assert answer.grounded is True
    assert answer.agents_used == []
    assert answer.tools_used == []
    assert "操作已拦截" in answer.answer_markdown
    assert "publish_version" in answer.answer_markdown
    events = service.trace(answer.trace_id)
    assert events[0].name == "high_risk_guard"