"""Central Azure OpenAI configuration, calls, diagnostics, and telemetry."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence
from urllib.parse import urlparse

from azure.identity import (
    AuthenticationRecord,
    AuthenticationRequiredError,
    InteractiveBrowserCredential,
    TokenCachePersistenceOptions,
    get_bearer_token_provider,
)
from dotenv import dotenv_values
from openai import AzureOpenAI, OpenAI
from pydantic import BaseModel

_PLACEHOLDERS = {
    "YOUR_KEY",
    "YOUR_CHAT_DEPLOYMENT",
    "YOUR_EMBEDDING_DEPLOYMENT",
    "YOUR-DEPLOYMENT",
    "https://YOUR-RESOURCE.openai.azure.com/",
}


class LLMConfigurationError(RuntimeError):
    """Raised when Azure OpenAI settings are absent or invalid."""


@dataclass(frozen=True)
class AzureLLMConfig:
    endpoint: str
    api_key: str
    deployment: str
    embedding_deployment: str = ""
    model_family: str = "gpt-5.4-mini"
    api_mode: str = "v1"
    auth_mode: str = "key"
    tenant_id: str = ""
    token_scope: str = "https://ai.azure.com/.default"
    api_version: str = "2024-10-21"
    reasoning_effort: str = "low"
    max_completion_tokens: int = 8192
    input_price_per_million: float = 0.75
    output_price_per_million: float = 4.50
    timeout_s: float = 45.0
    max_retries: int = 2
    auth_record_path: Path | None = None

    @classmethod
    def load(cls, project_root: Path) -> "AzureLLMConfig":
        file_values = dotenv_values(project_root / ".env")

        def setting(name: str, default: str = "") -> str:
            return str(os.environ.get(name) or file_values.get(name) or default).strip()

        return cls(
            endpoint=setting("AZURE_OPENAI_ENDPOINT"),
            api_key=setting("AZURE_OPENAI_API_KEY"),
            deployment=setting("AZURE_OPENAI_DEPLOYMENT"),
            embedding_deployment=setting(
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT"
            ),
            model_family=setting("AZURE_OPENAI_MODEL_FAMILY", "gpt-5.4-mini"),
            api_mode=setting("AZURE_OPENAI_API_MODE", "v1").casefold(),
            auth_mode=setting("AZURE_OPENAI_AUTH_MODE", "key").casefold(),
            tenant_id=setting("AZURE_OPENAI_TENANT_ID"),
            token_scope=setting(
                "AZURE_OPENAI_TOKEN_SCOPE", "https://ai.azure.com/.default"
            ),
            api_version=setting("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            reasoning_effort=setting(
                "AZURE_OPENAI_REASONING_EFFORT", "low"
            ).casefold(),
            max_completion_tokens=int(
                setting("AZURE_OPENAI_MAX_COMPLETION_TOKENS", "8192")
            ),
            input_price_per_million=float(
                setting("AZURE_OPENAI_INPUT_PRICE_PER_MILLION", "0.75")
            ),
            output_price_per_million=float(
                setting("AZURE_OPENAI_OUTPUT_PRICE_PER_MILLION", "4.50")
            ),
            timeout_s=float(setting("DATA_AGENT_LLM_TIMEOUT", "45")),
            max_retries=int(setting("DATA_AGENT_LLM_RETRIES", "2")),
            auth_record_path=project_root / "data" / "azure_auth_record.json",
        )

    @property
    def errors(self) -> List[str]:
        errors: List[str] = []
        required = {
            "AZURE_OPENAI_ENDPOINT": self.endpoint,
            "AZURE_OPENAI_DEPLOYMENT": self.deployment,
        }
        if self.auth_mode == "key":
            required["AZURE_OPENAI_API_KEY"] = self.api_key
        for name, value in required.items():
            if not value or value in _PLACEHOLDERS or "YOUR-" in value:
                errors.append(f"{name} is not configured")
        if self.endpoint and not self.endpoint.startswith(("https://", "http://")):
            errors.append("AZURE_OPENAI_ENDPOINT must be an HTTP(S) URL")
        if self.api_mode not in {"v1", "legacy"}:
            errors.append("AZURE_OPENAI_API_MODE must be v1 or legacy")
        if self.auth_mode not in {"key", "entra"}:
            errors.append("AZURE_OPENAI_AUTH_MODE must be key or entra")
        if self.auth_mode == "entra" and not self.token_scope:
            errors.append("AZURE_OPENAI_TOKEN_SCOPE is empty")
        if self.api_mode == "legacy" and not self.api_version:
            errors.append("AZURE_OPENAI_API_VERSION is empty")
        if self.reasoning_effort not in {"none", "low", "medium", "high", "xhigh"}:
            errors.append(
                "AZURE_OPENAI_REASONING_EFFORT must be none, low, medium, high, or xhigh"
            )
        if self.max_completion_tokens <= 0:
            errors.append("AZURE_OPENAI_MAX_COMPLETION_TOKENS must be greater than zero")
        if self.input_price_per_million < 0 or self.output_price_per_million < 0:
            errors.append("Azure OpenAI token prices cannot be negative")
        if self.timeout_s <= 0:
            errors.append("DATA_AGENT_LLM_TIMEOUT must be greater than zero")
        if self.max_retries < 0:
            errors.append("DATA_AGENT_LLM_RETRIES cannot be negative")
        return errors

    @property
    def configured(self) -> bool:
        return not self.errors

    @property
    def is_reasoning_model(self) -> bool:
        family = self.model_family.casefold()
        return family.startswith(("gpt-5", "o1", "o3", "o4"))

    @property
    def entra_login_cached(self) -> bool:
        return bool(self.auth_record_path and self.auth_record_path.exists())

    def safe_status(self) -> Dict[str, Any]:
        host = urlparse(self.endpoint).hostname if self.endpoint else None
        return {
            "configured": self.configured,
            "endpoint_host": host,
            "deployment": self.deployment or None,
            "model_family": self.model_family or None,
            "api_mode": self.api_mode,
            "auth_mode": self.auth_mode,
            "tenant_id_configured": bool(self.tenant_id),
            "token_scope": self.token_scope if self.auth_mode == "entra" else None,
            "entra_login_cached": (
                self.entra_login_cached if self.auth_mode == "entra" else None
            ),
            "reasoning_model": self.is_reasoning_model,
            "reasoning_effort": (
                self.reasoning_effort if self.is_reasoning_model else None
            ),
            "max_completion_tokens": self.max_completion_tokens,
            "pricing_usd_per_million": {
                "input": self.input_price_per_million,
                "output": self.output_price_per_million,
            },
            "embedding_deployment": self.embedding_deployment or None,
            "embedding_configured": bool(
                self.embedding_deployment
                and self.embedding_deployment not in _PLACEHOLDERS
                and "YOUR_" not in self.embedding_deployment
            ),
            "api_version": self.api_version,
            "api_key_present": bool(self.api_key and self.api_key not in _PLACEHOLDERS),
            "timeout_s": self.timeout_s,
            "max_retries": self.max_retries,
            "errors": self.errors,
        }


@dataclass(frozen=True)
class LLMCallRecord:
    operation: str
    deployment: str
    success: bool
    latency_ms: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error_type: str | None = None
    created_at: str = ""


class LLMMonitor:
    """In-process LLM latency, token, and error telemetry without prompt storage."""

    def __init__(
        self,
        max_records: int = 1000,
        sink: Callable[[Dict[str, Any]], None] | None = None,
    ) -> None:
        self.max_records = max_records
        self.sink = sink
        self._records: List[LLMCallRecord] = []
        self._lock = threading.Lock()

    def add(self, record: LLMCallRecord) -> None:
        payload = asdict(record)
        with self._lock:
            self._records.append(record)
            if len(self._records) > self.max_records:
                del self._records[: len(self._records) - self.max_records]
        if self.sink is not None:
            try:
                self.sink(payload)
            except Exception:
                # Telemetry persistence must never break the model call itself.
                pass

    def records(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [asdict(item) for item in self._records]

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            records = list(self._records)
        if not records:
            return {
                "calls": 0,
                "success_rate": 1.0,
                "average_latency_ms": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "by_operation": {},
                "errors": {},
            }
        successful = sum(item.success for item in records)
        by_operation = Counter(item.operation for item in records)
        errors = Counter(item.error_type for item in records if item.error_type)
        return {
            "calls": len(records),
            "success_rate": round(successful / len(records), 4),
            "average_latency_ms": round(
                sum(item.latency_ms for item in records) / len(records), 2
            ),
            "prompt_tokens": sum(item.prompt_tokens for item in records),
            "completion_tokens": sum(item.completion_tokens for item in records),
            "total_tokens": sum(item.total_tokens for item in records),
            "by_operation": dict(by_operation),
            "errors": dict(errors),
        }


class AzureLLMGateway:
    """The only module that directly calls Azure OpenAI."""

    def __init__(
        self,
        config: AzureLLMConfig,
        monitor: LLMMonitor | None = None,
        client: Any | None = None,
    ) -> None:
        if not config.configured:
            raise LLMConfigurationError("; ".join(config.errors))
        self.config = config
        self.monitor = monitor or LLMMonitor()
        self.client = client or self._create_client(config)

    @staticmethod
    def _create_client(config: AzureLLMConfig) -> Any:
        credential: Any = config.api_key
        if config.auth_mode == "entra":
            options = AzureLLMGateway._entra_options(config)
            record = AzureLLMGateway._load_authentication_record(config)
            if record is not None:
                options["authentication_record"] = record
            options["disable_automatic_authentication"] = True
            browser_credential = InteractiveBrowserCredential(**options)
            provider = get_bearer_token_provider(
                browser_credential,
                config.token_scope,
            )

            def credential() -> str:
                try:
                    return provider()
                except AuthenticationRequiredError as error:
                    raise LLMConfigurationError(
                        "Microsoft Entra login is required or expired. "
                        "Run `data-agent llm login`, then retry."
                    ) from error
        if config.api_mode == "v1":
            return OpenAI(
                base_url=f"{config.endpoint.rstrip('/')}/openai/v1/",
                api_key=credential,
                timeout=config.timeout_s,
                max_retries=config.max_retries,
            )
        return AzureOpenAI(
            azure_endpoint=config.endpoint,
            api_key=config.api_key if config.auth_mode == "key" else None,
            azure_ad_token_provider=(
                credential if config.auth_mode == "entra" else None
            ),
            api_version=config.api_version,
            timeout=config.timeout_s,
            max_retries=config.max_retries,
        )

    @staticmethod
    def login(config: AzureLLMConfig) -> Dict[str, Any]:
        if config.auth_mode != "entra":
            raise LLMConfigurationError(
                "AZURE_OPENAI_AUTH_MODE must be entra for browser login"
            )
        if config.auth_record_path is None:
            raise LLMConfigurationError("Entra authentication record path is missing")
        credential = InteractiveBrowserCredential(
            **AzureLLMGateway._entra_options(config)
        )
        record = credential.authenticate(scopes=[config.token_scope])
        config.auth_record_path.parent.mkdir(parents=True, exist_ok=True)
        config.auth_record_path.write_text(record.serialize(), encoding="utf-8")
        return {
            "authenticated": True,
            "tenant_id": record.tenant_id,
            "authority": record.authority,
            "cache": "encrypted persistent token cache",
        }

    @staticmethod
    def _entra_options(config: AzureLLMConfig) -> Dict[str, Any]:
        options: Dict[str, Any] = {
            "cache_persistence_options": TokenCachePersistenceOptions(
                name="data-insight-agent"
            ),
            "additionally_allowed_tenants": ["*"],
        }
        if config.tenant_id:
            options["tenant_id"] = config.tenant_id
        return options

    @staticmethod
    def _load_authentication_record(
        config: AzureLLMConfig,
    ) -> AuthenticationRecord | None:
        path = config.auth_record_path
        if path is None or not path.exists():
            return None
        try:
            return AuthenticationRecord.deserialize(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise LLMConfigurationError(
                "The saved Entra login record is invalid. "
                "Run `data-agent llm login` to replace it."
            ) from error

    def chat(
        self,
        operation: str,
        messages: Sequence[Dict[str, str]],
        *,
        temperature: float = 0,
        response_format: Dict[str, str] | None = None,
        response_model: type[BaseModel] | None = None,
    ) -> str:
        started = time.perf_counter()
        try:
            if response_format is not None and response_model is not None:
                raise ValueError("response_format and response_model are mutually exclusive")
            kwargs: Dict[str, Any] = {
                "model": self.config.deployment,
                "messages": list(messages),
            }
            if self.config.is_reasoning_model:
                kwargs["reasoning_effort"] = self.config.reasoning_effort
                kwargs["max_completion_tokens"] = self.config.max_completion_tokens
            else:
                kwargs["temperature"] = temperature
            if response_format is not None and not self.config.is_reasoning_model:
                kwargs["response_format"] = response_format
            if response_model is not None:
                response = self.client.beta.chat.completions.parse(
                    **kwargs,
                    response_format=response_model,
                )
                parsed = response.choices[0].message.parsed
                content = parsed.model_dump_json() if parsed is not None else None
            else:
                response = self.client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
            if not content:
                raise RuntimeError("Azure OpenAI returned an empty response")
            if response_format and response_format.get("type") == "json_object":
                content = self._normalize_json_object(content)
            usage = getattr(response, "usage", None)
            self.monitor.add(
                LLMCallRecord(
                    operation=operation,
                    deployment=self.config.deployment,
                    success=True,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                    prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                    completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                    total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            return content.strip()
        except Exception as error:
            self.monitor.add(
                LLMCallRecord(
                    operation=operation,
                    deployment=self.config.deployment,
                    success=False,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                    error_type=type(error).__name__,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            raise

    def test_connection(self) -> Dict[str, Any]:
        started = time.perf_counter()
        content = self.chat(
            "connection_test",
            [
                {
                    "role": "system",
                    "content": "This is a connection test. Reply with exactly OK.",
                },
                {"role": "user", "content": "ping"},
            ],
        )
        return {
            "connected": True,
            "deployment": self.config.deployment,
            "response": content,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "usage": self.monitor.records()[-1],
        }

    def embed(
        self,
        operation: str,
        texts: Sequence[str],
        deployment: str,
    ) -> List[List[float]]:
        started = time.perf_counter()
        try:
            response = self.client.embeddings.create(
                model=deployment,
                input=list(texts),
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors = [list(item.embedding) for item in ordered]
            usage = getattr(response, "usage", None)
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            total_tokens = int(getattr(usage, "total_tokens", prompt_tokens) or 0)
            self.monitor.add(
                LLMCallRecord(
                    operation=operation,
                    deployment=deployment,
                    success=True,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                    prompt_tokens=prompt_tokens,
                    total_tokens=total_tokens,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            return vectors
        except Exception as error:
            self.monitor.add(
                LLMCallRecord(
                    operation=operation,
                    deployment=deployment,
                    success=False,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                    error_type=type(error).__name__,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            raise

    @staticmethod
    def _normalize_json_object(content: str) -> str:
        text = content.strip()
        decoder = json.JSONDecoder()
        documents: List[Any] = []
        cursor = 0
        while cursor < len(text):
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor >= len(text):
                break
            document, cursor = decoder.raw_decode(text, cursor)
            documents.append(document)
        if not documents or not isinstance(documents[0], dict):
            raise ValueError("Azure OpenAI JSON response must contain an object")
        if any(document != documents[0] for document in documents[1:]):
            raise ValueError("Azure OpenAI returned multiple different JSON objects")
        return json.dumps(documents[0], ensure_ascii=False, separators=(",", ":"))