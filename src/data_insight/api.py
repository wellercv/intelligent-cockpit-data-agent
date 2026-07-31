"""FastAPI surface for analysis, governance, tracing, and evaluation."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, List, Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from data_insight.config import Settings
from data_insight.evaluation import AgentEvaluator
from data_insight.llm import LLMConfigurationError
from data_insight.retrieval_evaluation import RetrievalEvaluator
from data_insight.schemas import ToolCall
from data_insight.service import AgentService


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    conversation_id: Optional[str] = None


class EvaluationRequest(BaseModel):
    dataset: str = "core_questions.json"
    mode: Literal["offline", "azure", "auto"] = "offline"
    update_baseline: bool = False


class ChangeDraftRequest(BaseModel):
    issue_id: str
    proposed_value: Any
    reason: str = Field(min_length=3)
    requested_by: str = Field(default="local-user", min_length=1)


class ActorRequest(BaseModel):
    actor: str = Field(min_length=1)


class ConfirmRequest(BaseModel):
    actor: str = Field(default="local-user", min_length=1)
    comment: str = Field(default="Diff and contract checks reviewed", min_length=3)


class PublishRequest(BaseModel):
    change_ids: List[str] = Field(min_length=1)
    publisher: str = Field(min_length=1)
    provider: str = "multilingual_asr"


@lru_cache(maxsize=3)
def get_service(mode: str = "offline") -> AgentService:
    return AgentService(mode=mode)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Intelligent Cockpit Multilingual Voice Quality Data Agent",
        version="0.1.0",
        description="Agent-based analysis and governed change for intelligent cockpit voice quality data.",
    )

    @app.get("/health")
    def health(mode: Literal["offline", "azure", "auto"] = "offline"):
        return get_service(mode).health()

    @app.post("/chat")
    def chat(request: ChatRequest, mode: Literal["offline", "azure", "auto"] = "offline"):
        try:
            return get_service(mode).ask(request.question, request.conversation_id).model_dump(mode="json")
        except (ValueError, RuntimeError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/tools")
    def tools():
        return get_service("offline").provider.tool_catalog()

    @app.get("/traces/{trace_id}")
    def trace(trace_id: str):
        events = get_service("offline").trace(trace_id)
        if not events:
            raise HTTPException(status_code=404, detail="Trace not found")
        return [event.model_dump(mode="json") for event in events]

    @app.get("/skills")
    def skills():
        return get_service("offline").skills.summary()

    @app.post("/skills/reload")
    def reload_skills():
        return get_service("offline").reload_skills()

    @app.get("/monitor")
    def monitor():
        service = get_service("offline")
        config = service.llm_config
        return {
            "tools": service.runtime.summary(),
            "llm": service.state_store.llm_usage_summary(
                config.input_price_per_million,
                config.output_price_per_million,
            ),
            "provider": service.provider.health(),
        }

    @app.get("/memory/investigations")
    def investigation_memory(query: str, limit: int = 3):
        return get_service("offline").recall_investigations(query, limit)

    @app.get("/eval/retrieval")
    def retrieval_evaluation():
        service = get_service("offline")
        dataset = (
            service.settings.project_root
            / "eval"
            / "datasets"
            / "retrieval_questions.json"
        )
        return RetrievalEvaluator(service.knowledge_provider.index, dataset).run()

    @app.get("/eval/runs")
    def evaluation_runs(limit: int = 20):
        return get_service("offline").state_store.list_evaluation_runs(limit)

    @app.get("/eval/runs/{run_id}")
    def evaluation_run(run_id: str):
        result = get_service("offline").state_store.get_evaluation_run(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Evaluation run not found")
        return result

    @app.get("/nlu/overview")
    def nlu_overview():
        observation = get_service("offline").runtime.execute(
            ToolCall(name="nlu_report_overview")
        )
        if not observation.success:
            raise HTTPException(status_code=503, detail=observation.error)
        return observation.model_dump(mode="json")

    @app.get("/nlu/errors")
    def nlu_errors(
        query: str = "",
        language: Optional[str] = None,
        domain: Optional[str] = None,
        error_type: Optional[str] = None,
        intent: Optional[str] = None,
        limit: int = 20,
    ):
        observation = get_service("offline").runtime.execute(
            ToolCall(
                name="search_nlu_errors",
                arguments={
                    "query": query,
                    "language": language,
                    "domain": domain,
                    "error_type": error_type,
                    "intent": intent,
                    "limit": limit,
                },
            )
        )
        if not observation.success:
            raise HTTPException(status_code=400, detail=observation.error)
        return observation.model_dump(mode="json")

    @app.get("/nlu/errors/{error_id}")
    def nlu_error_detail(error_id: str):
        observation = get_service("offline").runtime.execute(
            ToolCall(
                name="get_nlu_error_detail",
                arguments={"error_id": error_id},
            )
        )
        if not observation.success:
            raise HTTPException(status_code=404, detail=observation.error)
        return observation.model_dump(mode="json")

    @app.get("/nlu/label-quality")
    def nlu_label_quality(
        issue_kind: Optional[str] = None,
        language: Optional[str] = None,
        domain: Optional[str] = None,
        changed_slot: Optional[str] = None,
        limit: int = 20,
    ):
        observation = get_service("offline").runtime.execute(
            ToolCall(
                name="nlu_label_quality",
                arguments={
                    "issue_kind": issue_kind,
                    "language": language,
                    "domain": domain,
                    "changed_slot": changed_slot,
                    "limit": limit,
                },
            )
        )
        if not observation.success:
            raise HTTPException(status_code=400, detail=observation.error)
        return observation.model_dump(mode="json")

    @app.get("/llm/status")
    def llm_status():
        return get_service("offline").llm_status()

    @app.post("/llm/test")
    def llm_test():
        try:
            return get_service("offline").test_llm_connection()
        except LLMConfigurationError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(
                status_code=502,
                detail=f"{type(error).__name__}: {error}",
            ) from error

    @app.post("/eval/run")
    def evaluate(request: EvaluationRequest):
        settings = Settings.load()
        dataset = (settings.project_root / "eval" / "datasets" / request.dataset).resolve()
        allowed_root = (settings.project_root / "eval" / "datasets").resolve()
        if allowed_root not in dataset.parents or not dataset.exists():
            raise HTTPException(status_code=404, detail="Evaluation dataset not found")
        try:
            baseline = (
                settings.project_root
                / "eval"
                / "baselines"
                / f"{dataset.stem}.{request.mode}.json"
            )
            service = get_service(request.mode)
            return AgentEvaluator(
                service,
                dataset,
                judge=service.answer_judge,
            ).run(
                baseline_path=baseline,
                update_baseline=request.update_baseline,
            )
        except LLMConfigurationError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/governance/scan")
    def governance_scan(provider: str = "multilingual_asr"):
        return get_service("offline").governance_scan(provider).model_dump(mode="json")

    @app.get("/governance/issues")
    def governance_issues(
        provider: str = "multilingual_asr",
        status: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 500,
    ):
        return [
            item.model_dump(mode="json")
            for item in get_service("offline").governance_issues(
                provider, status, severity, limit
            )
        ]

    @app.get("/governance/changes")
    def governance_changes(
        provider: str = "multilingual_asr",
        status: Optional[str] = None,
        limit: int = 500,
    ):
        return [
            item.model_dump(mode="json")
            for item in get_service("offline").governance_changes(
                provider, status, limit
            )
        ]

    @app.post("/governance/changes")
    def create_change(request: ChangeDraftRequest):
        try:
            return get_service("offline").create_change_draft(
                request.issue_id,
                request.proposed_value,
                request.reason,
                request.requested_by,
            ).model_dump(mode="json")
        except (KeyError, ValueError, PermissionError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/governance/changes/{change_id}/preview")
    def preview_change(change_id: str):
        observation = get_service("offline").preview_change(change_id)
        if not observation.success:
            raise HTTPException(status_code=400, detail=observation.error)
        return observation.model_dump(mode="json")

    @app.post("/governance/changes/{change_id}/confirm")
    def confirm_change(change_id: str, request: ConfirmRequest):
        try:
            return get_service("offline").confirm_change(
                change_id, request.actor, request.comment
            ).model_dump(mode="json")
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/governance/publish")
    def publish_changes(request: PublishRequest):
        try:
            return get_service("offline").publish_changes(
                request.change_ids, request.publisher, request.provider
            ).model_dump(mode="json")
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/governance/rollback")
    def rollback(request: ActorRequest, provider: str = "multilingual_asr"):
        try:
            return get_service("offline").rollback_active_version(
                request.actor, provider
            ).model_dump(mode="json")
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/governance/audit")
    def governance_audit(limit: int = 200):
        return get_service("offline").governance_audit(limit)

    return app


app = create_app()
