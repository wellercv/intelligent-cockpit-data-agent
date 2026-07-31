"""Application service assembling providers, agent graph, memory, and traces."""

from __future__ import annotations

from typing import List
from uuid import uuid4

from data_insight.composer import AzureComposer, OfflineComposer
from data_insight.config import Settings
from data_insight.grounding import GroundingVerifier
from data_insight.judge import AzureAnswerJudge
from data_insight.llm import AzureLLMConfig, AzureLLMGateway, LLMConfigurationError, LLMMonitor
from data_insight.memory import AgentStateStore, InvestigationMemory
from data_insight.multi_agent import (
    AnswerSynthesizer,
    SupervisorAgent,
    build_specialist_pool,
)
from data_insight.multi_agent_graph import build_multi_agent_graph
from data_insight.planner import AzurePlanner, OfflinePlanner
from data_insight.providers.asr import MultilingualASRProvider
from data_insight.providers.composite import CompositeProvider
from data_insight.providers.governance import DataGovernanceProvider
from data_insight.providers.knowledge import KnowledgeProvider
from data_insight.providers.nlu import NLUEvaluationProvider
from data_insight.retrieval import AzureEmbedding, FeatureHashEmbedding
from data_insight.safety import HighRiskGuard, SafetyDecision
from data_insight.schemas import (
    AgentAnswer,
    SourceRef,
    SpecialistResult,
    ToolCall,
    ToolObservation,
    TraceEvent,
)
from data_insight.skills import SkillManager
from data_insight.tool_runtime import ToolRuntime


class AgentService:
    def __init__(self, settings: Settings | None = None, mode: str = "offline") -> None:
        if mode not in {"offline", "azure", "auto"}:
            raise ValueError("mode must be offline, azure, or auto")
        self.settings = settings or Settings.load()
        self.safety_guard = HighRiskGuard()
        self.skills = SkillManager(self.settings.skills_dir)
        self.state_store = AgentStateStore(self.settings.state_db_path)
        self.llm_config = AzureLLMConfig.load(self.settings.project_root)
        self.llm_monitor = LLMMonitor(sink=self.state_store.save_llm_call)
        if mode == "azure" and not self.llm_config.configured:
            raise LLMConfigurationError("; ".join(self.llm_config.errors))
        self.mode = (
            "azure"
            if mode == "azure" or (mode == "auto" and self.llm_config.configured)
            else "offline"
        )
        self.llm_gateway = (
            AzureLLMGateway(self.llm_config, self.llm_monitor)
            if self.mode == "azure"
            else None
        )
        self.embedding = (
            AzureEmbedding(
                self.llm_gateway,
                self.llm_config.embedding_deployment,
            )
            if self.llm_gateway
            and self.llm_config.safe_status()["embedding_configured"]
            else FeatureHashEmbedding()
        )
        self.investigation_memory = InvestigationMemory(
            self.settings.runtime_path("investigation_chroma"),
            self.embedding,
        )
        self.asr_provider = MultilingualASRProvider(self.settings)
        self.nlu_provider = NLUEvaluationProvider(self.settings)
        self.knowledge_provider = KnowledgeProvider(
            self.settings.knowledge_dir,
            self.settings.runtime_path("knowledge.db"),
            self.settings.runtime_path("knowledge_chroma"),
            self.embedding,
        )
        self.governance_provider = DataGovernanceProvider(
            self.settings,
            self.asr_provider,
            self.nlu_provider,
        )
        self.provider = CompositeProvider(
            [
                self.asr_provider,
                self.nlu_provider,
                self.knowledge_provider,
                self.governance_provider,
            ]
        )
        self.runtime = ToolRuntime(self.provider)
        self.answer_judge = (
            AzureAnswerJudge(self.llm_gateway) if self.llm_gateway else None
        )
        if self.mode == "azure":
            assert self.llm_gateway is not None
            self.planner = AzurePlanner(
                self.provider.tool_catalog(), self.skills, self.llm_gateway
            )
            self.composer = AzureComposer(self.skills, self.llm_gateway)
        else:
            self.planner = OfflinePlanner(self.provider.tool_catalog())
            self.composer = OfflineComposer()
        self.verifier = GroundingVerifier(self.settings)
        self.supervisor = SupervisorAgent(self.planner)
        self.specialists = build_specialist_pool(
            self.runtime,
            self.provider.tool_catalog(),
            self.skills,
            self.llm_gateway,
        )
        self.synthesis = AnswerSynthesizer(self.composer)
        self.graph = build_multi_agent_graph(
            self.supervisor,
            self.specialists,
            self.synthesis,
            self.verifier,
        )

    def close(self) -> None:
        self.runtime.close()
        self.provider.close()
        self.investigation_memory.close()

    def ask(
        self,
        question: str,
        conversation_id: str | None = None,
        *,
        use_investigation_memory: bool = True,
    ) -> AgentAnswer:
        text = question.strip()
        if not text:
            raise ValueError("question cannot be empty")
        safety = self.safety_guard.assess(text)
        if safety.blocked:
            return self._blocked_answer(text, conversation_id, safety)
        context = self.state_store.load_context(conversation_id)
        recalled = (
            self.investigation_memory.recall(text, limit=3)
            if use_investigation_memory
            else []
        )
        graph_context = context.model_copy(
            update={"summary": self._memory_context(recalled)}
        )
        result = self.graph.invoke(
            {
                "question": text,
                "context": graph_context.model_dump(mode="json"),
                "observations": [],
                "specialist_results": [],
                "events": [],
                "rounds": 0,
            },
            {"recursion_limit": 12},
        )
        observations = [ToolObservation.model_validate(item) for item in result.get("observations", [])]
        specialist_results = [
            SpecialistResult.model_validate(item)
            for item in result.get("specialist_results", [])
        ]
        events = [TraceEvent.model_validate(item) for item in result.get("events", [])]
        answer_markdown = result["answer"]
        grounded = bool(result.get("grounded", False))
        unsupported = list(result.get("unsupported_numbers", []))
        verification_warnings = list(result.get("verification_warnings", []))
        if not grounded and self.mode == "azure":
            answer_markdown = OfflineComposer().compose(text, context, observations)
            grounded, unsupported, verification_warnings = self.verifier.verify(
                text, answer_markdown, observations
            )
            events.append(
                TraceEvent(
                    event_type="answer",
                    name="deterministic_fallback",
                    payload={"reason": "Azure answer failed grounding verification"},
                )
            )

        languages = OfflinePlanner.extract_languages(text)
        domains = OfflinePlanner.extract_domains(text)
        case_ids = [
            str(row["case_id"])
            for observation in observations
            for row in observation.rows
            if row.get("case_id")
        ]
        new_context = context.model_copy(
            update={
                "selected_languages": languages or context.selected_languages,
                "selected_domains": domains or context.selected_domains,
                "last_case_ids": case_ids,
                "last_question": text,
            }
        )
        self.state_store.save_context(new_context)
        trace_id = uuid4().hex
        self.state_store.save_trace(trace_id, events)
        self.state_store.add_message(new_context.conversation_id, "user", text)
        self.state_store.add_message(
            new_context.conversation_id,
            "assistant",
            answer_markdown,
            {"trace_id": trace_id, "grounded": grounded},
        )
        sources = self.verifier.sources(observations)
        if use_investigation_memory and grounded and sources:
            self.investigation_memory.remember(
                question=text,
                answer=answer_markdown,
                conversation_id=new_context.conversation_id,
                trace_id=trace_id,
                source_paths=[source.path for source in sources],
            )
        worker_agents = list(dict.fromkeys(item.agent for item in specialist_results))
        agents_used = ["supervisor", *worker_agents]
        warnings = list(
            dict.fromkeys(
                [warning for observation in observations for warning in observation.warnings]
                + verification_warnings
            )
        )
        return AgentAnswer(
            question=text,
            answer_markdown=answer_markdown,
            conversation_id=new_context.conversation_id,
            trace_id=trace_id,
            agents_used=agents_used,
            tools_used=[observation.tool_name for observation in observations],
            specialist_results=specialist_results,
            observations=observations,
            sources=sources,
            warnings=warnings,
            context=new_context,
            grounded=grounded,
            unsupported_numbers=unsupported,
        )

    def health(self) -> dict:
        current_llm_config = AzureLLMConfig.load(self.settings.project_root)
        llm_usage = self.state_store.llm_usage_summary(
            current_llm_config.input_price_per_million,
            current_llm_config.output_price_per_million,
        )
        successful_operations = llm_usage.get("successful_by_operation", {})
        chat_online_validated = any(
            successful_operations.get(operation, 0) > 0
            for operation in ("connection_test", "planning", "answer_composition")
        )
        return {
            "status": "ok",
            "mode": self.mode,
            "provider": self.provider.health(),
            "agents": {
                "supervisor": {"ready": True},
                **self.specialists.health(),
            },
            "components": {
                "answer_synthesizer": {"ready": True},
                "grounding_verifier": {"ready": True},
                "human_confirmation": {
                    "ready": True,
                    "mode": "local explicit diff confirmation",
                },
                "high_risk_guard": {
                    "ready": True,
                    "implementation": "deterministic patterns before LLM planning",
                },
                "working_memory": {
                    "ready": True,
                    "store": "SQLite WAL",
                },
                "azure_chat": {
                    "ready": current_llm_config.configured,
                    "online_validated": chat_online_validated,
                    "deployment": current_llm_config.deployment or None,
                    "auth_mode": current_llm_config.auth_mode,
                },
                "investigation_memory": {
                    "ready": True,
                    **self.investigation_memory.health(),
                },
                "embedding": {
                    "ready": True,
                    "provider": self.embedding.name,
                    "dimensions": self.embedding.dimensions,
                    "azure_online_validated": (
                        self.embedding.name.startswith("azure-openai:")
                        and successful_operations.get("knowledge_embedding", 0) > 0
                    ),
                },
                "mcp_server": {
                    "ready": True,
                    "implementation": "FastMCP",
                    "transports": ["stdio", "sse", "streamable-http"],
                    "high_risk_mutations_exposed": False,
                },
                "llm_as_judge": {
                    "configured": current_llm_config.configured,
                    "enabled_in_current_mode": self.answer_judge is not None,
                    "online_validated": successful_operations.get(
                        "answer_judge", 0
                    )
                    > 0,
                    "deterministic_eval_independent": True,
                },
            },
            "skills": self.skills.summary(),
            "tool_stats": self.runtime.summary(),
            "llm": {
                "configuration": current_llm_config.safe_status(),
                "usage": llm_usage,
            },
        }

    def test_llm_connection(self) -> dict:
        current_config = AzureLLMConfig.load(self.settings.project_root)
        gateway = AzureLLMGateway(current_config, self.llm_monitor)
        return gateway.test_connection()

    def llm_status(self) -> dict:
        return AzureLLMConfig.load(self.settings.project_root).safe_status()

    def reload_skills(self) -> dict:
        self.skills.reload()
        return self.skills.summary()

    def trace(self, trace_id: str) -> List[TraceEvent]:
        return self.state_store.load_trace(trace_id)

    def governance_scan(self, provider: str = "multilingual_asr") -> ToolObservation:
        return self.governance_provider.execute(
            ToolCall(name="governance_scan", arguments={"provider": provider})
        )

    def governance_issues(
        self,
        provider: str = "multilingual_asr",
        status: str | None = None,
        severity: str | None = None,
        limit: int = 500,
    ):
        return self.governance_provider.store.list_issues(
            provider=provider,
            status=status,
            severity=severity,
            limit=limit,
        )

    def governance_changes(
        self,
        provider: str = "multilingual_asr",
        status: str | None = None,
        limit: int = 500,
    ):
        return self.governance_provider.store.list_changes(
            provider=provider, status=status, limit=limit
        )

    def create_change_draft(
        self,
        issue_id: str,
        proposed_value,
        reason: str,
        requested_by: str,
    ):
        return self.governance_provider.create_change_draft(
            issue_id, proposed_value, reason, requested_by
        )

    def preview_change(self, change_id: str) -> ToolObservation:
        return self.governance_provider.execute(
            ToolCall(name="preview_change", arguments={"change_id": change_id})
        )

    def confirm_change(self, change_id: str, actor: str, comment: str = "Diff reviewed"):
        return self.governance_provider.confirm_change(change_id, actor, comment)

    def publish_changes(
        self, change_ids: List[str], publisher: str, provider: str = "multilingual_asr"
    ):
        return self.governance_provider.publish_changes(provider, change_ids, publisher)

    def rollback_active_version(
        self, actor: str, provider: str = "multilingual_asr"
    ):
        return self.governance_provider.rollback_active(provider, actor)

    def active_dataset_version(self, provider: str = "multilingual_asr"):
        return self.governance_provider.store.active_version(provider)

    def governance_audit(self, limit: int = 200):
        return self.governance_provider.store.audit_log(limit)

    def recall_investigations(self, query: str, limit: int = 3):
        return self.investigation_memory.recall(query, limit)

    def _blocked_answer(
        self,
        question: str,
        conversation_id: str | None,
        decision: SafetyDecision,
    ) -> AgentAnswer:
        context = self.state_store.load_context(conversation_id).model_copy(
            update={"last_question": question}
        )
        self.state_store.save_context(context)
        trace_id = uuid4().hex
        answer = (
            "## 操作已拦截\n\n"
            "该请求涉及高风险数据治理动作，聊天 Agent 不会直接执行。"
            "请在数据治理工作台中检查结构化 Diff 和契约校验结果并明确确认；"
            "确认、发布或回滚只能通过显式人工操作执行。\n\n"
            f"- 识别动作：`{decision.action}`\n"
            "- 原始数据：保持不变\n"
            "- 当前处理：未确认、未发布任何变更"
        )
        events = [
            TraceEvent(
                event_type="verify",
                name="high_risk_guard",
                payload=decision.as_dict(),
            ),
            TraceEvent(
                event_type="answer",
                name="blocked_governance_action",
                payload={"action": decision.action},
            ),
        ]
        self.state_store.save_trace(trace_id, events)
        self.state_store.add_message(context.conversation_id, "user", question)
        self.state_store.add_message(
            context.conversation_id,
            "assistant",
            answer,
            {"trace_id": trace_id, "grounded": True, "blocked": True},
        )
        source = SourceRef(
            source_id="governance-state",
            label="数据治理状态与审计",
            path="data/agent_state.db",
            scope="governance_state",
        )
        return AgentAnswer(
            question=question,
            answer_markdown=answer,
            conversation_id=context.conversation_id,
            trace_id=trace_id,
            agents_used=[],
            tools_used=[],
            observations=[],
            sources=[source],
            warnings=["高风险治理动作已在进入 LLM 规划前被确定性规则门禁拦截。"],
            context=context,
            grounded=True,
        )

    @staticmethod
    def _memory_context(rows: List[dict]) -> str:
        if not rows:
            return ""
        lines = [
            "以下是相似历史调查，仅用于规划线索；当前事实必须重新调用工具验证："
        ]
        for row in rows:
            lines.append(
                f"- {row['document']}（trace_id={row['trace_id']}）"
            )
        return "\n".join(lines)
