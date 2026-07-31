"""MCP server exposing approved intelligent-cockpit quality data tools."""

from __future__ import annotations

from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP

from data_insight.schemas import ToolCall
from data_insight.service import AgentService


def create_mcp_server(
    service: AgentService | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8001,
) -> FastMCP:
    agent_service = service or AgentService(mode="offline")
    server = FastMCP(
        "智能座舱语音质量数据工具",
        instructions=(
            "提供只读指标、案例、知识和治理查询。确认、发布和回滚不通过 MCP 暴露。"
        ),
        host=host,
        port=port,
        streamable_http_path="/mcp",
        json_response=True,
    )

    def execute(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        observation = agent_service.runtime.execute(
            ToolCall(name=name, arguments=arguments)
        )
        return observation.model_dump(mode="json")

    @server.tool(description="查看七语种智能座舱语音质量数据概览")
    def dataset_overview() -> Dict[str, Any]:
        return execute("dataset_overview", {})

    @server.tool(description="按语言、业务领域和数据口径查询指标")
    def get_metrics(
        language: str | None = None,
        domain: str | None = None,
        source_scope: str = "csv_cases",
    ) -> Dict[str, Any]:
        return execute(
            "get_metrics",
            {
                "language": language,
                "domain": domain,
                "source_scope": source_scope,
            },
        )

    @server.tool(description="比较多个语言的语音质量指标")
    def compare_languages(
        languages: List[str],
        domain: str | None = None,
    ) -> Dict[str, Any]:
        return execute(
            "compare_languages", {"languages": languages, "domain": domain}
        )

    @server.tool(description="比较多个智能座舱业务领域的指标")
    def compare_domains(language: str | None = None) -> Dict[str, Any]:
        return execute("compare_domains", {"language": language})

    @server.tool(description="按错误率、准确率、错误数或总数排名")
    def rank_dimensions(
        dimension: str,
        metric: str,
        language: str | None = None,
        domain: str | None = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        return execute(
            "rank_dimensions",
            {
                "dimension": dimension,
                "metric": metric,
                "language": language,
                "domain": domain,
                "limit": limit,
            },
        )

    @server.tool(description="搜索真实 ASR 测试案例")
    def search_cases(
        query: str = "",
        languages: List[str] | None = None,
        domains: List[str] | None = None,
        result: str | None = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        return execute(
            "search_cases",
            {
                "query": query,
                "languages": languages or [],
                "domains": domains or [],
                "result": result,
                "limit": limit,
            },
        )

    @server.tool(description="按稳定案例 ID 或原始编号查询案例详情")
    def get_case_detail(
        case_id: str | None = None,
        case_no: str | None = None,
        language: str | None = None,
        domain: str | None = None,
    ) -> Dict[str, Any]:
        return execute(
            "get_case_detail",
            {
                "case_id": case_id,
                "case_no": case_no,
                "language": language,
                "domain": domain,
            },
        )

    @server.tool(description="比较 CSV 明细与 JSON 汇总的数据口径")
    def compare_source_scopes(
        language: str | None = None,
        domain: str | None = None,
    ) -> Dict[str, Any]:
        return execute(
            "compare_source_scopes", {"language": language, "domain": domain}
        )

    @server.tool(description="查看离线 NLU 重测报告整体指标和数据口径")
    def nlu_report_overview() -> Dict[str, Any]:
        return execute("nlu_report_overview", {})

    @server.tool(description="按语言或 Domain 比较修正标签口径后的 NLU 准确率")
    def nlu_compare_accuracy(
        dimension: str,
        order: str = "worst",
        names: List[str] | None = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        return execute(
            "nlu_compare_accuracy",
            {
                "dimension": dimension,
                "order": order,
                "names": names or [],
                "limit": limit,
            },
        )

    @server.tool(description="聚合 NLU 模型错误类型、语言、Domain 或 Intent 分布")
    def nlu_error_breakdown(
        group_by: str,
        language: str | None = None,
        domain: str | None = None,
        error_type: str | None = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        return execute(
            "nlu_error_breakdown",
            {
                "group_by": group_by,
                "language": language,
                "domain": domain,
                "error_type": error_type,
                "limit": limit,
            },
        )

    @server.tool(description="检索 NLU 模型错误明细子集")
    def search_nlu_errors(
        query: str = "",
        language: str | None = None,
        domain: str | None = None,
        error_type: str | None = None,
        intent: str | None = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        return execute(
            "search_nlu_errors",
            {
                "query": query,
                "language": language,
                "domain": domain,
                "error_type": error_type,
                "intent": intent,
                "limit": limit,
            },
        )

    @server.tool(description="按稳定 ID 查询一条 NLU 模型错误详情")
    def get_nlu_error_detail(error_id: str) -> Dict[str, Any]:
        return execute("get_nlu_error_detail", {"error_id": error_id})

    @server.tool(description="查询 NLU 标签命名和数值槽位标注质量，不修改报告")
    def nlu_label_quality(
        issue_kind: str | None = None,
        language: str | None = None,
        domain: str | None = None,
        changed_slot: str | None = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        return execute(
            "nlu_label_quality",
            {
                "issue_kind": issue_kind,
                "language": language,
                "domain": domain,
                "changed_slot": changed_slot,
                "limit": limit,
            },
        )

    @server.tool(description="通过 Hybrid RAG 检索指标口径、规范和历史知识")
    def search_knowledge(query: str, limit: int = 5) -> Dict[str, Any]:
        return execute("search_knowledge", {"query": query, "limit": limit})

    @server.tool(description="按数据契约扫描并登记质量问题，不修改原始数据")
    def governance_scan(provider: str = "multilingual_asr") -> Dict[str, Any]:
        return execute("governance_scan", {"provider": provider})

    @server.tool(description="查询数据治理问题及其状态")
    def list_governance_issues(
        provider: str = "multilingual_asr",
        status: str | None = None,
        severity: str | None = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        return execute(
            "list_governance_issues",
            {
                "provider": provider,
                "status": status,
                "severity": severity,
                "limit": limit,
            },
        )

    @server.tool(description="查询单个数据治理问题")
    def get_governance_issue(issue_id: str) -> Dict[str, Any]:
        return execute("get_governance_issue", {"issue_id": issue_id})

    @server.tool(description="查询变更建议及确认状态")
    def list_change_requests(
        provider: str = "multilingual_asr",
        status: str | None = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        return execute(
            "list_change_requests",
            {"provider": provider, "status": status, "limit": limit},
        )

    @server.tool(description="预览一个变更建议，不确认也不发布")
    def preview_change(change_id: str) -> Dict[str, Any]:
        return execute("preview_change", {"change_id": change_id})

    return server