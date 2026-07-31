from __future__ import annotations

import shutil
from pathlib import Path

from data_insight.config import Settings
from data_insight.demo_data import DEMO_LANGUAGES, ensure_demo_data
from data_insight.service import AgentService


def _demo_settings(tmp_path: Path) -> Settings:
    project_root = Path(__file__).parents[1]
    source_root = tmp_path / "sources"
    demo = ensure_demo_data(source_root)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    governance = runtime / "governance"
    governance.mkdir()
    return Settings(
        project_root=project_root,
        data_root=source_root,
        warehouse_path=runtime / "warehouse.duckdb",
        state_db_path=runtime / "agent_state.db",
        knowledge_dir=project_root / "knowledge",
        skills_dir=project_root / "skills",
        contracts_dir=project_root / "config" / "contracts",
        governance_dir=governance,
        languages=DEMO_LANGUAGES,
        case_pattern="*_{language}/*_asr.csv",
        summary_pattern="*_{language}/*_output.json",
        nlu_report_path=Path(demo["nlu_report"]),
        demo_mode=True,
        runtime_dir=runtime,
    )


def test_demo_data_generation_is_idempotent(tmp_path: Path):
    first = ensure_demo_data(tmp_path)
    second = ensure_demo_data(tmp_path)

    assert first == second
    assert first["synthetic_demo"] is True
    assert first["asr_cases"] == 42
    assert first["nlu_samples"] == 14
    assert len(list(tmp_path.rglob("*_asr.csv"))) == 42
    assert len(list(tmp_path.rglob("*_output.json"))) == 42


def test_settings_loads_explicit_demo_mode(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATA_AGENT_DEMO_MODE", "1")
    monkeypatch.setenv("DATA_AGENT_DEMO_ROOT", str(tmp_path / "demo"))

    settings = Settings.load()

    assert settings.demo_mode is True
    assert settings.data_root == (tmp_path / "demo").resolve()
    assert settings.nlu_report_path == (tmp_path / "demo" / "demo_nlu_report.xlsx")
    assert settings.runtime_dir == settings.project_root / "data" / "demo_runtime"


def test_settings_loads_project_root_from_environment(monkeypatch, tmp_path: Path):
    installed_root = tmp_path / "installed-app"
    config_dir = installed_root / "config"
    config_dir.mkdir(parents=True)
    source_config = Path(__file__).parents[1] / "config" / "sources.yaml"
    shutil.copyfile(source_config, config_dir / "sources.yaml")
    monkeypatch.setenv("DATA_AGENT_PROJECT_ROOT", str(installed_root))
    monkeypatch.setenv("DATA_AGENT_DEMO_MODE", "1")

    settings = Settings.load()

    assert settings.project_root == installed_root.resolve()
    assert settings.data_root == installed_root / "data" / "demo_sources"
    assert settings.runtime_dir == installed_root / "data" / "demo_runtime"


def test_demo_mode_runs_cross_provider_agent_without_business_data(tmp_path: Path):
    settings = _demo_settings(tmp_path)
    service = AgentService(settings, mode="offline")
    try:
        providers = {
            item["provider"]: item
            for item in service.health()["provider"]["providers"]
        }
        answer = service.ask(
            "Compare ASR and NLU overall accuracy",
            use_investigation_memory=False,
        )
    finally:
        service.close()

    assert providers["multilingual_asr"]["cases"] == 42
    assert providers["multilingual_asr"]["synthetic_demo"] is True
    assert providers["nlu_evaluation"]["samples"] == 14
    assert providers["nlu_evaluation"]["synthetic_demo"] is True
    assert answer.tools_used == ["nlu_report_overview", "dataset_overview"]
    assert answer.grounded is True
    assert any("Synthetic demo data" in warning for warning in answer.warnings)
