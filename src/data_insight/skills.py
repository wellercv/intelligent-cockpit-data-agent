"""Runtime-loadable business analysis skills."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

import yaml


@dataclass(frozen=True)
class Skill:
    name: str
    content: str
    path: str
    agents: List[str]
    keywords: List[str]
    priority: int = 0
    enabled: bool = True

    def matches(self, message: str, agent_type: str) -> bool:
        if not self.enabled:
            return False
        if self.agents and agent_type.casefold() not in {item.casefold() for item in self.agents}:
            return False
        if not self.keywords:
            return True
        text = message.casefold()
        return any(keyword.casefold() in text for keyword in self.keywords)


class SkillManager:
    def __init__(self, root: Path, max_prompt_chars: int = 6000) -> None:
        self.root = root
        self.max_prompt_chars = max_prompt_chars
        self.skills: List[Skill] = []
        self.errors: List[str] = []
        self.reload()

    def reload(self) -> None:
        self.skills = []
        self.errors = []
        if not self.root.exists():
            return
        for path in sorted(self.root.rglob("SKILL.md")):
            try:
                self.skills.append(self._load(path))
            except (OSError, ValueError, yaml.YAMLError) as error:
                self.errors.append(f"{path}: {error}")
        self.skills.sort(key=lambda item: item.priority, reverse=True)

    def prompt_for(self, message: str, agent_type: str) -> str:
        blocks: List[str] = []
        used = 0
        for skill in self.skills:
            if not skill.matches(message, agent_type):
                continue
            block = f"[Skill: {skill.name}]\n{skill.content.strip()}"
            remaining = self.max_prompt_chars - used
            if remaining <= 0:
                break
            blocks.append(block[:remaining])
            used += len(block)
        return "\n\n".join(blocks)

    def summary(self) -> dict:
        return {
            "loaded": len(self.skills),
            "errors": self.errors,
            "skills": [
                {"name": item.name, "path": item.path, "agents": item.agents, "keywords": item.keywords, "enabled": item.enabled}
                for item in self.skills
            ],
        }

    @staticmethod
    def _as_list(value: object) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value).split(",") if item.strip()]

    def _load(self, path: Path) -> Skill:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            raise ValueError("SKILL.md requires YAML frontmatter")
        _, frontmatter, content = text.split("---", 2)
        meta = yaml.safe_load(frontmatter) or {}
        return Skill(
            name=str(meta.get("name") or path.parent.name),
            content=content.strip(),
            path=str(path),
            agents=self._as_list(meta.get("agents")),
            keywords=self._as_list(meta.get("keywords")),
            priority=int(meta.get("priority", 0)),
            enabled=bool(meta.get("enabled", True)),
        )
