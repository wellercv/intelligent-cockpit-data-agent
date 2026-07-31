from pathlib import Path
from types import SimpleNamespace

import pytest

from data_insight.llm import (
    AzureLLMConfig,
    AzureLLMGateway,
    LLMConfigurationError,
    LLMMonitor,
)


class FakeCompletions:
    def __init__(self, error=None, content="OK"):
        self.error = error
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(
                prompt_tokens=7,
                completion_tokens=1,
                total_tokens=8,
            ),
        )


class FakeEmbeddings:
    def create(self, **kwargs):
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=[float(index), 1.0])
                for index, _ in enumerate(kwargs["input"])
            ],
            usage=SimpleNamespace(prompt_tokens=5, total_tokens=5),
        )


def fake_client(error=None, content="OK"):
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=FakeCompletions(error=error, content=content)
        ),
        embeddings=FakeEmbeddings(),
    )


def valid_config():
    return AzureLLMConfig(
        endpoint="https://example.openai.azure.com/",
        api_key="test-key-not-secret",
        deployment="test-deployment",
        model_family="gpt-4.1-mini",
    )


def test_gateway_records_usage_without_prompts():
    persisted = []
    monitor = LLMMonitor(sink=persisted.append)
    gateway = AzureLLMGateway(valid_config(), monitor, client=fake_client())
    result = gateway.test_connection()
    assert result["connected"] is True
    assert result["response"] == "OK"
    assert monitor.summary()["total_tokens"] == 8
    assert persisted[0]["operation"] == "connection_test"
    assert "messages" not in persisted[0]


def test_gateway_records_failure_type():
    monitor = LLMMonitor()
    gateway = AzureLLMGateway(
        valid_config(), monitor, client=fake_client(RuntimeError("blocked"))
    )
    with pytest.raises(RuntimeError, match="blocked"):
        gateway.test_connection()
    assert monitor.summary()["success_rate"] == 0.0
    assert monitor.summary()["errors"] == {"RuntimeError": 1}


@pytest.mark.requires_business_data
def test_state_usage_reports_percentiles_and_estimated_cost(service):
    usage = service.state_store.llm_usage_summary(0.75, 4.50)

    assert usage["p50_latency_ms"] >= 0
    assert usage["p95_latency_ms"] >= usage["p50_latency_ms"]
    expected = (
        usage["prompt_tokens"] / 1_000_000 * 0.75
        + usage["completion_tokens"] / 1_000_000 * 4.50
    )
    assert usage["estimated_cost_usd"] == round(expected, 6)


def test_gateway_records_embedding_usage():
    monitor = LLMMonitor()
    gateway = AzureLLMGateway(valid_config(), monitor, client=fake_client())

    vectors = gateway.embed(
        "knowledge_embedding",
        ["accuracy", "source scope"],
        "embedding-small",
    )

    assert vectors == [[0.0, 1.0], [1.0, 1.0]]
    record = monitor.records()[0]
    assert record["operation"] == "knowledge_embedding"
    assert record["deployment"] == "embedding-small"
    assert record["total_tokens"] == 5


def test_gateway_uses_reasoning_parameters_for_gpt_5():
    client = fake_client(content='{"status":"answer"}')
    config = AzureLLMConfig(
        endpoint="https://example.openai.azure.com/",
        api_key="test-key-not-secret",
        deployment="data-agent-chat",
        model_family="gpt-5.4-mini",
        reasoning_effort="low",
        max_completion_tokens=4096,
    )
    gateway = AzureLLMGateway(config, client=client)

    gateway.chat(
        "planning",
        [{"role": "user", "content": "Return JSON."}],
        temperature=0,
        response_format={"type": "json_object"},
    )

    request = client.chat.completions.calls[0]
    assert "temperature" not in request
    assert request["reasoning_effort"] == "low"
    assert request["max_completion_tokens"] == 4096
    assert "response_format" not in request


def test_gateway_keeps_temperature_for_non_reasoning_models():
    client = fake_client()
    gateway = AzureLLMGateway(valid_config(), client=client)

    gateway.chat("answer", [{"role": "user", "content": "Answer."}], temperature=0)

    request = client.chat.completions.calls[0]
    assert request["temperature"] == 0
    assert "reasoning_effort" not in request


def test_entra_mode_does_not_require_api_key():
    config = AzureLLMConfig(
        endpoint="https://example.openai.azure.com/",
        api_key="",
        deployment="data-agent-chat",
        auth_mode="entra",
    )

    assert config.configured is True
    status = config.safe_status()
    assert status["auth_mode"] == "entra"
    assert status["api_key_present"] is False
    assert status["token_scope"] == "https://ai.azure.com/.default"


def test_entra_status_reports_saved_login_record(tmp_path):
    record = tmp_path / "azure_auth_record.json"
    config = AzureLLMConfig(
        endpoint="https://example.openai.azure.com/",
        api_key="",
        deployment="data-agent-chat",
        auth_mode="entra",
        auth_record_path=record,
    )

    assert config.safe_status()["entra_login_cached"] is False
    record.write_text("local account metadata", encoding="utf-8")
    assert config.safe_status()["entra_login_cached"] is True


def test_gateway_normalizes_identical_repeated_json_objects():
    gateway = AzureLLMGateway(
        valid_config(),
        client=fake_client(content='{"status":"answer"}\n{"status":"answer"}'),
    )

    content = gateway.chat(
        "planning",
        [{"role": "user", "content": "Return JSON."}],
        response_format={"type": "json_object"},
    )

    assert content == '{"status":"answer"}'


def test_gateway_rejects_different_repeated_json_objects():
    gateway = AzureLLMGateway(
        valid_config(),
        client=fake_client(content='{"status":"execute"}\n{"status":"answer"}'),
    )

    with pytest.raises(ValueError, match="multiple different JSON objects"):
        gateway.chat(
            "planning",
            [{"role": "user", "content": "Return JSON."}],
            response_format={"type": "json_object"},
        )


def test_placeholder_env_is_not_treated_as_configured(tmp_path, monkeypatch):
    for name in (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_DEPLOYMENT",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com/\n"
        "AZURE_OPENAI_API_KEY=YOUR_KEY\n"
        "AZURE_OPENAI_DEPLOYMENT=YOUR_CHAT_DEPLOYMENT\n",
        encoding="utf-8",
    )
    config = AzureLLMConfig.load(Path(tmp_path))
    assert config.configured is False
    assert config.safe_status()["api_key_present"] is False
    with pytest.raises(LLMConfigurationError):
        AzureLLMGateway(config)