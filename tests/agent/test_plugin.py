from pathlib import Path

from src.agent.codex.plugin import CodexPlugin, build_plugin
from src.agent.skills.ingest_and_summarize import IngestAndSummarizeSkill
from src.agent.skills.search_and_answer import SearchAndAnswerSkill


def test_codex_plugin_exposes_skills():
    plugin = CodexPlugin(skills=None, mcp_url="http://localhost:8000/mcp")
    assert plugin.harness == "codex"
    names = [s.name for s in plugin.skills()]
    assert "search_and_answer" in names
    assert "ingest_and_summarize" in names


def test_codex_plugin_manifest_shape():
    plugin = CodexPlugin(
        skills=[SearchAndAnswerSkill(), IngestAndSummarizeSkill()],
        mcp_url="http://x/mcp",
    )
    m = plugin.build_manifest()
    assert m["harness"] == "codex"
    assert m["mcp_url"] == "http://x/mcp"
    assert m["skills"][0]["name"] == "search_and_answer"
    assert m["skills"][1]["name"] == "ingest_and_summarize"


def test_build_plugin_reads_config():
    from config.schema import AppConfig, load_config

    cfg = load_config(Path("config/app.yaml"))
    plugin = build_plugin(cfg)
    assert plugin.harness == "codex"
    assert plugin._mcp_url == "http://localhost:8000/mcp"
    assert len(plugin.skills()) == 2
