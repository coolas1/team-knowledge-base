"""Codex harness plugin: packages the shared, harness-agnostic skills into the
codex harness format + config. The codex harness calls the engine via MCP
(there is no harness to test against yet; the manifest describes the wiring).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import yaml

from src.agent.interface import AgentPlugin, Skill
from src.agent.skills.ingest_and_summarize import IngestAndSummarizeSkill
from src.agent.skills.search_and_answer import SearchAndAnswerSkill

_SKILL_FACTORIES: dict[str, Callable[[], Skill]] = {
    "search_and_answer": SearchAndAnswerSkill,
    "ingest_and_summarize": IngestAndSummarizeSkill,
}


class CodexPlugin:
    """AgentPlugin for the codex harness."""

    harness = "codex"

    def __init__(self, skills: list[Skill] | None, mcp_url: str) -> None:
        self._skills = skills if skills is not None else _default_skills()
        self._mcp_url = mcp_url

    def skills(self) -> list[Skill]:
        return list(self._skills)

    def build_manifest(self) -> dict:
        return {
            "harness": self.harness,
            "mcp_url": self._mcp_url,
            "skills": [{"name": s.name, "description": s.description} for s in self._skills],
        }


def _default_skills() -> list[Skill]:
    return [SearchAndAnswerSkill(), IngestAndSummarizeSkill()]


def build_plugin(config) -> CodexPlugin:
    """Build the codex plugin from AppConfig + config/agent/codex/plugin.yaml."""
    skill_names = getattr(config.agent, "skills", list(_SKILL_FACTORIES))
    skills: list[Skill] = []
    for name in skill_names:
        factory = _SKILL_FACTORIES.get(name)
        if factory is not None:
            skills.append(factory())

    plugin_cfg_path = Path("config/agent/codex/plugin.yaml")
    mcp_url = "http://localhost:8000/mcp"
    if plugin_cfg_path.exists():
        data = yaml.safe_load(plugin_cfg_path.read_text(encoding="utf-8")) or {}
        mcp_url = data.get("mcp_url", mcp_url)

    return CodexPlugin(skills=skills, mcp_url=mcp_url)
