# plugin/ -- stateless skills and LLM orchestration

## Module summary

The stateless plugin layer: a folder-convention plugin whose skills call the
engine's `KnowledgeBase` contract directly (in-process, no client), plus an
LLM helper and a policy-as-data hook gate. A `PluginLoader` turns the plugin
folder into a `LoadedPlugin` (manifest + skills + hooks).

- `interface.py` - plugin contract: `SkillContext`/`SkillResult`/`LoadedSkill`,
  `PluginManifest`/`LoadedPlugin`, `LlmClient` Protocol.
- `loader.py` - `PluginLoader.load(folder)` reads `plugin.yaml` + `skills/`
  + `hooks/` and returns a `LoadedPlugin`.
- `policy.py` - `HookPolicy`: policy-as-data gate over hook YAML files
  (`check(op, params, approved)` -> proceed or needs-approval).
- `llm.py` - LLM provider abstraction (`build_llm()`).
- `tkb/` - the one plugin (selected by `plugin.impl`): `plugin.yaml` manifest,
  three skills (`search_and_answer`, `ingest_and_summarize`,
  `reflective_search`), two hooks (`before_remove`, `after_indexed`), and the
  MCP server under `mcp/` (FastMCP, `remove_document` is hook-guarded).

MCP tool registration is conditional: the memory tools (`query_knowledge`,
`search_knowledge_fast`, `search_knowledge_deep`) exist only when a query
service is wired via `set_query_service` (i.e. `engine.memory.enabled`);
`search` falls back to plain recall otherwise.

## Hard-won knowledge

<!-- Inclusion rule: add an entry only if it is non-obvious, repo-related, and
     painful to re-derive. Each entry: the decision (1-3 sentences) + why.
     Truly long-form decisions link out to a doc instead of bloating this file. -->
