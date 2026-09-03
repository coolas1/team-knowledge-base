"""PluginLoader: reads a plugin folder (plugin.yaml + skills/ + hooks/ + mcp/)
and produces a LoadedPlugin. Replaces the old build_plugin()."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from src.agent.interface import LoadedPlugin, LoadedSkill, McpSpec, PluginManifest
from src.agent.policy import HookPolicy


class PluginLoader:
    def load(self, path: Path) -> LoadedPlugin:
        path = Path(path)
        manifest = self._load_manifest(path)
        names = manifest.skills or self._discover_skills(path)
        skills = {name: self._load_skill(path / "skills" / name) for name in names}
        hooks = HookPolicy.load(path / "hooks")
        return LoadedPlugin(manifest=manifest, skills=skills, hooks=hooks, mcp=manifest.mcp)

    def _load_manifest(self, path: Path) -> PluginManifest:
        data = yaml.safe_load((path / "plugin.yaml").read_text(encoding="utf-8")) or {}
        mcp = data.get("mcp") or {}
        return PluginManifest(
            name=data.get("name", path.name),
            version=data.get("version", "0.1.0"),
            mcp=McpSpec(endpoint=mcp.get("endpoint")),
            skills=list(data.get("skills") or []),
        )

    def _load_skill(self, skill_dir: Path) -> LoadedSkill:
        meta = yaml.safe_load((skill_dir / "skill.yaml").read_text(encoding="utf-8")) or {}
        run = self._import_run(skill_dir / "skill.py")
        return LoadedSkill(
            name=meta.get("name", skill_dir.name),
            description=meta.get("description", ""),
            inputs=meta.get("inputs", {}),
            run=run,
        )

    @staticmethod
    def _discover_skills(path: Path) -> list[str]:
        skills_dir = path / "skills"
        if not skills_dir.is_dir():
            return []
        return [p.name for p in sorted(skills_dir.iterdir()) if (p / "skill.yaml").exists()]

    @staticmethod
    def _import_run(skill_py: Path):
        spec = importlib.util.spec_from_file_location(f"_skill_{skill_py.parent.name}", skill_py)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod.run
