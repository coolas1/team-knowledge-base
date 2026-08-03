# gbrain install guide (Linux)

RAG Agent Bench evaluates gbrain against the `raw/` corpus. gbrain is installed as an
external binary — no source checkout, no submodule. Treat it like `git` or
`bun`: a tool on `PATH`.

The bench expects a Linux host. (gbrain does not officially support Windows —
the PGLite WASM extraction fails under Bun's `--compile` vfs there.)

## Prerequisites

| Tool | Why | Install |
|---|---|---|
| **Bun ≥ 1.3** | gbrain runtime | `curl -fsSL https://bun.sh/install \| bash` |
| **Ollama** (or another embedding provider) | Generate vectors | see "Embedding provider" below |
| **git** | Clone this repo | system package manager |

After installing Bun, reload your shell so `~/.bun/bin` is on `PATH`:
```bash
export PATH="$HOME/.bun/bin:$PATH"   # add to ~/.bashrc / ~/.zshrc
bun --version
```

## Install gbrain globally

One command pulls gbrain from GitHub and drops a binary at `~/.bun/bin/gbrain`:

```bash
bun install -g github:garrytan/gbrain
gbrain --version    # should print gbrain 0.42.x.x or newer
```

If `bun install -g` reports a postinstall hook failure or `gbrain doctor`
reports `schema_version: 0`, run `gbrain apply-migrations --yes` once. This is
the documented recovery path (gbrain issue #218).

## Embedding provider

The bench was set up against a remote **Ollama** instance serving
`nomic-embed-text` (768 dims). Pick one of the supported providers below; the
rest of the bench assumes Ollama.

### Option A — Ollama (default, used by this bench)

Either run Ollama locally (`ollama serve` on the default port 11434) or point
the bench at a remote host. Pull the embedding model:
```bash
ollama pull nomic-embed-text
```

### Option B — OpenAI / Voyage / ZeroEntropy

See `gbrain config --help` for `embedding_model` and the API key env vars.
Drop the `provider_base_urls.ollama` line from the config below and set the
appropriate `OPENAI_API_KEY` / `VOYAGE_API_KEY` / `ZEROENTROPY_API_KEY`.

## Initialize the brain

gbrain's runtime lives in `~/.gbrain/` — `config.json`, the PGLite brain
database, and audit logs. None of it is committed to this repo.

Write `~/.gbrain/config.json` (adjust `provider_base_urls.ollama` to your
host — keep the `/v1` suffix):
```json
{
  "engine": "pglite",
  "database_path": "~/.gbrain/brain.pglite",
  "embedding_model": "ollama:nomic-embed-text",
  "embedding_dimensions": 768,
  "provider_base_urls": {
    "ollama": "http://10.201.186.15:11434/v1"
  },
  "schema_pack": "gbrain-base-v2",
  "mcp": { "publish_skills": true }
}
```

Create the empty brain:
```bash
gbrain init --pglite
gbrain doctor --json   # verify all checks pass
```

## Run the bench pipeline

From the repo root:
```bash
# All-in-one: reset + ingest raw/ + extract links + embed + health check
bash scripts/gbrain/run-all.sh

# Or granular:
bash scripts/gbrain/reset.sh    # wipe + re-init
bash scripts/gbrain/ingest.sh   # import raw + extract --source fs --dir raw + embed --stale
bash scripts/gbrain/health.sh   # gbrain doctor + summary
```

**PGLite is single-writer.** The gbrain MCP server (`.mcp.json`) holds the
brain lock while it runs, so in-session use the `mcp__gbrain__*` tools and run
the gbrain CLI / bench scripts from a separate terminal — or `gbrain doctor`
hangs ~30s on `Timed out waiting for PGLite lock`. The scripts probe for the
lock and fail fast with a clear message. (If the MCP server is stopped, the CLI
runs inline — but then the in-session MCP tools are unavailable.)

**The MCP server must be on PATH.** `.mcp.json` spawns `gbrain serve`, whose
entrypoint is `#!/usr/bin/env bun`. Both `gbrain` and `bun` must be on the PATH
Claude Code uses to spawn MCP servers — but the installer puts them in
`~/.bun/bin` and adds that only to `~/.bashrc`, which MCP spawns don't source.
If `/mcp` reports `Failed to reconnect to gbrain: ENOENT`, the binaries aren't
resolvable from the spawn env; symlink them onto a directory already on the
host PATH:
```bash
ln -s ~/.bun/bin/bun    /usr/local/bin/bun
ln -s ~/.bun/bin/gbrain /usr/local/bin/gbrain
```
Then `/mcp` to reconnect.

Expected outcome on a clean run:
- 14 `.md` pages imported from `raw/`
- ~57 links auto-extracted from inline markdown links
- All chunks embedded (Ollama reachable)
- `gbrain doctor` overall health score ~70/100 (the missing 30 is timeline +
  entity-graph slots that are structural to this corpus design)

## What this bench deliberately does NOT do

- **No source-mutation.** `raw/` is read-only. All gbrain output goes to
  `~/.gbrain/` (the brain DB) or `gbrain-files/` (logs and snapshots).
- **No frontmatter.** Markdown files under `raw/` are pure prose; the
  canonical relations graph lives in `eval/STORY.md`. gbrain derives its link
  graph from inline `[text](path.md)` markdown links only.
- **No multimodal image ingestion.** Binary attachments (pdf/xlsx/csv/png/jpg)
  are leaf nodes described in prose by surrounding `.md` files. gbrain's
  `import` does not parse them.
- **No source registration via `gbrain sources add`.** Plain
  `gbrain import raw` defaults to the `default` source and downstream
  commands work without it.

## Upgrading gbrain

```bash
gbrain upgrade        # self-updates the binary, runs schema migrations,
                      # prints post-upgrade notes
```

Then read `~/.gbrain/skills/migrations/v<NEW_VERSION>.md` for any backfill or
verification steps.
