from pathlib import Path

from src.agent.loader import PluginLoader


def test_load_tkb_plugin():
    plugin = PluginLoader().load(Path("src/agent/tkb"))
    assert "search_and_answer" in plugin.skills
    assert "ingest_and_summarize" in plugin.skills
    assert "reflective_search" in plugin.skills
    assert "remove" in plugin.hooks.gated_ops          # before_remove loaded
    assert plugin.manifest.name == "tkb"


def test_plugin_has_three_skills():
    plugin = PluginLoader().load(Path("src/agent/tkb"))
    assert set(plugin.skills) == {
        "search_and_answer",
        "ingest_and_summarize",
        "reflective_search",
    }


def test_plugin_has_both_hook_files():
    assert Path("src/agent/tkb/hooks/before_remove.yaml").exists()
    assert Path("src/agent/tkb/hooks/after_indexed.yaml").exists()


def test_skill_run_is_callable():
    plugin = PluginLoader().load(Path("src/agent/tkb"))
    assert callable(plugin.skills["search_and_answer"].run)
    assert callable(plugin.skills["reflective_search"].run)
