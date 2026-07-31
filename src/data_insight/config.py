"""Project settings and source catalog loading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import yaml


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_root: Path
    warehouse_path: Path
    state_db_path: Path
    knowledge_dir: Path
    skills_dir: Path
    contracts_dir: Path
    governance_dir: Path
    languages: Dict[str, str]
    case_pattern: str
    summary_pattern: str
    nlu_report_path: Path | None = None

    @classmethod
    def load(cls, project_root: Path | None = None) -> "Settings":
        root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        config_path = root / "config" / "sources.yaml"
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        configured_data_root = os.environ.get("DATA_AGENT_DATA_ROOT")
        data_root = Path(configured_data_root).resolve() if configured_data_root else (root / payload["data_root"]).resolve()
        configured_nlu_report = os.environ.get("DATA_AGENT_NLU_REPORT")
        nlu_file = (payload.get("nlu_report") or {}).get("file")
        nlu_report_path = (
            Path(configured_nlu_report).resolve()
            if configured_nlu_report
            else (data_root / nlu_file).resolve()
            if nlu_file
            else None
        )
        data_dir = root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        governance_dir = data_dir / "governance"
        governance_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            project_root=root,
            data_root=data_root,
            warehouse_path=data_dir / "warehouse.duckdb",
            state_db_path=data_dir / "agent_state.db",
            knowledge_dir=root / "knowledge",
            skills_dir=root / "skills",
            contracts_dir=root / "config" / "contracts",
            governance_dir=governance_dir,
            languages=dict(payload["languages"]),
            case_pattern=payload["patterns"]["cases"],
            summary_pattern=payload["patterns"]["summary"],
            nlu_report_path=nlu_report_path,
        )

    def display_path(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.data_root).as_posix()
        except ValueError:
            return resolved.as_posix()
