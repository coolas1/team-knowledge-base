# Config Layer Unification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every key in `config/app.yaml` a reachable environment
override, resolve configuration in three named layers, and shrink
`.env.example` to the values a deployment actually chooses.

**Architecture:** A pure-function module derives an environment name for each
leaf of the `AppConfig` schema (`TKB_` + uppercased path). A second module
resolves the three layers — committed `config/app.yaml`, then environment,
then `config/app.runtime.yaml` — and reports each leaf's source. The existing
archive merge is left intact and composes underneath, so `ARCHIVE_*` keeps
winning.

**Tech Stack:** Python 3.12, pydantic v2, pydantic-settings, PyYAML, pytest,
FastAPI (BFF route), uv.

**Spec:** `openspec/changes/unify-config-layers/` — `proposal.md`,
`design.md`, `specs/configuration/spec.md`. The tasks checklist is
`tasks.md`; this document is the granular execution plan for it.

## Global Constraints

- Python 3.12+, fully type-hinted. No new third-party dependencies.
- `AppConfig`'s schema (`config/schema.py`) is unchanged — no field added,
  removed, or retyped.
- Precedence is fixed: `config/app.yaml` < environment < `config/app.runtime.yaml`.
  Runtime wins; that ordering is the whole point of the change.
- `merge_archive_config` and `ArchiveSettings` are not modified. Existing
  archive behaviour and tests must pass untouched.
- Commits: Conventional Commits, scope `config` (or `webapp` for the route),
  subject ≤50 chars, imperative, no trailing period.
- Before pushing: `uv run ruff check` and `uv run pytest` both clean.
- Branch: `feat/unify-config-layers`, already created off `origin/develop`.

---

## File Structure

**Create:**

| File | Responsibility |
|---|---|
| `config/overrides.py` | Leaf-path enumeration, env-name derivation, applying the environment layer. Pure — no file I/O. |
| `config/layers.py` | Reading the two YAML layers, merging all three, reporting per-leaf source. Owns the precedence order. |
| `tests/config/test_overrides.py` | Derivation, collision guard, coercion. |
| `tests/config/test_layers.py` | Precedence order, absent-layer behaviour, source reporting, archive composition. |
| `tests/config/test_reachability.py` | Every schema leaf derives an override name. |
| `tests/config/test_env_example.py` | `.env.example` honesty: no dead keys, no missing required keys. |

**Modify:**

| File | Change |
|---|---|
| `config/schema.py` | `load_config` delegates to `layers.effective_config`. |
| `src/frontend/webapp/server/routes_config.py` | GET returns sources; PUT writes the runtime layer. |
| `.gitignore` | Ignore `config/app.runtime.yaml`. |
| `.env.example` | Rewritten to per-deployment facts only. |
| `docs/config-reference.md` | Layer model, derivation rule, deferred items. |

---

## Task 1: Leaf-path enumeration and env-name derivation

**Files:**
- Create: `config/overrides.py`
- Test: `tests/config/test_overrides.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `leaf_paths(model: type[BaseModel]) -> list[str]`
  - `derive_env_name(path: str) -> str`
  - `derived_names(model: type[BaseModel]) -> dict[str, str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_overrides.py
"""Derived environment names for the committed configuration schema."""
from __future__ import annotations

from config.overrides import derive_env_name, derived_names, leaf_paths
from config.schema import AppConfig


def test_derive_env_name_joins_path_segments():
    assert (
        derive_env_name("engine.ingest.chunk_concurrency")
        == "TKB_ENGINE_INGEST_CHUNK_CONCURRENCY"
    )


def test_derive_env_name_keeps_inner_underscores_in_one_segment():
    # The segment's own underscores are not path separators; nothing is parsed
    # back out of the name, so this is unambiguous by construction.
    assert (
        derive_env_name("engine.memory.entity_resolution_max_concurrent")
        == "TKB_ENGINE_MEMORY_ENTITY_RESOLUTION_MAX_CONCURRENT"
    )


def test_leaf_paths_includes_nested_memory_features():
    paths = leaf_paths(AppConfig)
    assert "engine.ingest.chunk_concurrency" in paths
    assert "engine.memory.features.scope" in paths
    assert "archive.collision_policy" in paths
    assert "plugin.impl" in paths


def test_leaf_paths_contains_only_leaves():
    paths = leaf_paths(AppConfig)
    # A branch (has children) is not itself a leaf.
    assert "engine" not in paths
    assert "engine.ingest" not in paths


def test_derived_names_covers_every_leaf_and_is_collision_free():
    names = derived_names(AppConfig)
    assert set(names) == set(leaf_paths(AppConfig))
    assert len(set(names.values())) == len(names), "two paths derive one name"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_overrides.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config.overrides'`

- [ ] **Step 3: Write minimal implementation**

```python
# config/overrides.py
"""Derived environment overrides for the committed application configuration.

Every leaf of the AppConfig schema gets an environment name built from its
path: ``engine.ingest.chunk_concurrency`` becomes
``TKB_ENGINE_INGEST_CHUNK_CONCURRENCY``.

Names are only ever *generated* from a path that already exists in the
schema and then looked up in the environment. No environment name is ever
parsed back into a path, so underscores inside a key name cannot be confused
with path separators -- the ambiguity that would plague a parsing approach
never arises.
"""
from __future__ import annotations

from pydantic import BaseModel

ENV_PREFIX = "TKB_"


def leaf_paths(model: type[BaseModel]) -> list[str]:
    """Every leaf dot-path of a pydantic model, in declaration order."""
    paths: list[str] = []

    def walk(current: type[BaseModel], prefix: str) -> None:
        for name, field in current.model_fields.items():
            path = f"{prefix}{name}"
            annotation = field.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                walk(annotation, f"{path}.")
            else:
                paths.append(path)

    walk(model, "")
    return paths


def derive_env_name(path: str) -> str:
    """``engine.ingest.chunk_concurrency`` -> ``TKB_ENGINE_INGEST_CHUNK_CONCURRENCY``."""
    return ENV_PREFIX + path.replace(".", "_").upper()


def derived_names(model: type[BaseModel]) -> dict[str, str]:
    """Leaf path -> derived environment name."""
    return {path: derive_env_name(path) for path in leaf_paths(model)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/config/test_overrides.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add config/overrides.py tests/config/test_overrides.py
git commit -m "feat(config): derive env names from schema paths"
```

---

## Task 2: Apply the environment layer

**Files:**
- Modify: `config/overrides.py`
- Test: `tests/config/test_overrides.py`

**Interfaces:**
- Consumes: `derived_names`, `derive_env_name` from Task 1.
- Produces:
  - `apply_env_overrides(data: dict, model: type[BaseModel], environ: Mapping[str, str]) -> tuple[dict, set[str]]`
    returning the nested dict with environment values overlaid, and the set
    of leaf paths that were overridden.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/config/test_overrides.py
from config.overrides import apply_env_overrides
from config.schema import AppConfig


def test_apply_env_overrides_sets_a_nested_leaf():
    data, applied = apply_env_overrides(
        {},
        AppConfig,
        {"TKB_ENGINE_INGEST_CHUNK_CONCURRENCY": "9"},
    )
    assert data["engine"]["ingest"]["chunk_concurrency"] == "9"
    assert applied == {"engine.ingest.chunk_concurrency"}


def test_apply_env_overrides_ignores_unrelated_variables():
    data, applied = apply_env_overrides({}, AppConfig, {"PATH": "/usr/bin"})
    assert data == {}
    assert applied == set()


def test_apply_env_overrides_preserves_existing_values():
    data, _ = apply_env_overrides(
        {"engine": {"ingest": {"doc_concurrency": 7, "chunk_concurrency": 1}}},
        AppConfig,
        {"TKB_ENGINE_INGEST_CHUNK_CONCURRENCY": "9"},
    )
    assert data["engine"]["ingest"]["doc_concurrency"] == 7
    assert data["engine"]["ingest"]["chunk_concurrency"] == "9"


def test_apply_env_overrides_does_not_mutate_the_input():
    original = {"engine": {"ingest": {"doc_concurrency": 7}}}
    apply_env_overrides(original, AppConfig, {"TKB_ENGINE_INGEST_CHUNK_CONCURRENCY": "9"})
    assert original == {"engine": {"ingest": {"doc_concurrency": 7}}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_overrides.py -v -k apply_env`
Expected: FAIL — `ImportError: cannot import name 'apply_env_overrides'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to config/overrides.py
import copy
from collections.abc import Mapping
from typing import Any


def _set_path(target: dict[str, Any], path: str, value: str) -> None:
    parts = path.split(".")
    node = target
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def apply_env_overrides(
    data: dict[str, Any],
    model: type[BaseModel],
    environ: Mapping[str, str],
) -> tuple[dict[str, Any], set[str]]:
    """Overlay environment values onto a config mapping.

    Values stay as strings here; pydantic coerces them on validation, so a
    bad value fails with the same error it would raise coming from YAML.
    """
    merged = copy.deepcopy(data)
    applied: set[str] = set()
    for path, env_name in derived_names(model).items():
        value = environ.get(env_name)
        if value is None:
            continue
        _set_path(merged, path, value)
        applied.add(path)
    return merged, applied
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/config/test_overrides.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add config/overrides.py tests/config/test_overrides.py
git commit -m "feat(config): apply env layer onto schema paths"
```

---

## Task 3: Three-layer resolution and source reporting

**Files:**
- Create: `config/layers.py`
- Test: `tests/config/test_layers.py`

**Interfaces:**
- Consumes: `apply_env_overrides` from Task 2, `AppConfig` from `config.schema`.
- Produces:
  - `SOURCE_DEFAULT`, `SOURCE_APP`, `SOURCE_ENV`, `SOURCE_RUNTIME` constants
    (`"default"`, `"app.yaml"`, `"env"`, `"runtime"`)
  - `read_layer(path) -> dict`
  - `effective_config(app_path=None, runtime_path=None, environ=None) -> tuple[AppConfig, dict[str, str]]`

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_layers.py
"""Three-layer configuration resolution: app.yaml < env < runtime."""
from __future__ import annotations

import pytest
import yaml

from config.layers import effective_config


@pytest.fixture
def layers(tmp_path):
    """Write an app.yaml and a runtime file, and return their paths."""
    def write(name, data):
        path = tmp_path / name
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return path

    app = write("app.yaml", {"archive": {"threshold": 0.5}})
    runtime = write("runtime.yaml", {})
    return app, runtime


def test_defaults_apply_when_no_layer_sets_a_key(layers):
    app, runtime = layers
    cfg, sources = effective_config(app, runtime, environ={})
    assert cfg.archive.delta == 0.10
    assert sources["archive.delta"] == "default"


def test_app_yaml_beats_the_default(layers):
    app, runtime = layers
    cfg, sources = effective_config(app, runtime, environ={})
    assert cfg.archive.threshold == 0.5
    assert sources["archive.threshold"] == "app.yaml"


def test_env_beats_app_yaml(layers):
    app, runtime = layers
    cfg, sources = effective_config(
        app, runtime, environ={"TKB_ARCHIVE_THRESHOLD": "0.9"}
    )
    assert cfg.archive.threshold == 0.9
    assert sources["archive.threshold"] == "env"


def test_runtime_beats_env(layers):
    app, runtime = layers
    runtime.write_text(yaml.safe_dump({"archive": {"threshold": 0.7}}), encoding="utf-8")
    cfg, sources = effective_config(
        app, runtime, environ={"TKB_ARCHIVE_THRESHOLD": "0.9"}
    )
    assert cfg.archive.threshold == 0.7  # runtime wins over env
    assert sources["archive.threshold"] == "runtime"


def test_absent_runtime_file_changes_nothing(tmp_path):
    app = tmp_path / "app.yaml"
    app.write_text(yaml.safe_dump({"archive": {"threshold": 0.5}}), encoding="utf-8")
    missing = tmp_path / "absent.yaml"
    cfg, sources = effective_config(app, missing, environ={})
    assert cfg.archive.threshold == 0.5
    assert sources["archive.threshold"] == "app.yaml"


def test_bad_env_value_raises_on_validation(layers):
    app, runtime = layers
    with pytest.raises(Exception):
        effective_config(app, runtime, environ={"TKB_ARCHIVE_THRESHOLD": "not-a-number"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_layers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config.layers'`

- [ ] **Step 3: Write minimal implementation**

```python
# config/layers.py
"""Three-layer configuration resolution.

Precedence, lowest to highest:

    config/app.yaml          committed defaults, never written at runtime
    environment              per-deployment facts
    config/app.runtime.yaml  runtime edits from the config API

Resolution is one function; a layer that is absent contributes nothing, so a
deployment with no runtime file and no environment overrides resolves exactly
to the committed defaults.
"""
from __future__ import annotations

import copy
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from config.overrides import apply_env_overrides
from config.schema import AppConfig

SOURCE_DEFAULT = "default"
SOURCE_APP = "app.yaml"
SOURCE_ENV = "env"
SOURCE_RUNTIME = "runtime"

DEFAULT_APP_PATH = Path("config/app.yaml")
DEFAULT_RUNTIME_PATH = Path("config/app.runtime.yaml")


def read_layer(path: Path | str | None) -> dict[str, Any]:
    """Raw mapping from a YAML layer file; {} when absent or empty."""
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def effective_config(
    app_path: Path | str | None = None,
    runtime_path: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[AppConfig, dict[str, str]]:
    """Resolve the three layers; return the config and each leaf's source."""
    app_raw = read_layer(app_path if app_path is not None else DEFAULT_APP_PATH)
    runtime_raw = read_layer(
        runtime_path if runtime_path is not None else DEFAULT_RUNTIME_PATH
    )
    env = os.environ if environ is None else environ

    # env sits between the committed defaults and the runtime layer, so the
    # environment is applied first and the runtime file merged over it.
    data, env_applied = apply_env_overrides(app_raw, AppConfig, env)
    merged = _deep_merge(data, runtime_raw)

    cfg = AppConfig.model_validate(merged)

    from config.overrides import leaf_paths

    sources = {path: SOURCE_DEFAULT for path in leaf_paths(AppConfig)}
    for path in _flatten(app_raw):
        sources[path] = SOURCE_APP
    for path in env_applied:
        sources[path] = SOURCE_ENV
    for path in _flatten(runtime_raw):
        sources[path] = SOURCE_RUNTIME

    return cfg, sources
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/config/test_layers.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add config/layers.py tests/config/test_layers.py
git commit -m "feat(config): resolve app.yaml, env and runtime layers"
```

---

## Task 4: Point `load_config` at the layered resolver

**Files:**
- Modify: `config/schema.py` (the `load_config` function at the end)
- Test: `tests/config/test_layers.py`

**Interfaces:**
- Consumes: `effective_config` from Task 3.
- Produces: `load_config(path=None) -> AppConfig` keeps its signature and now
  returns the resolved configuration. Existing callers
  (`src/agent/tkb/mcp/server.py`, `src/frontend/webapp/server/deps.py`,
  `src/engine/hindsight_components/service.py`) need no change.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/config/test_layers.py
from config.schema import ArchiveCfg, AppConfig, load_config


def test_load_config_applies_env_overrides(tmp_path, monkeypatch):
    app = tmp_path / "app.yaml"
    app.write_text(yaml.safe_dump({"archive": {"threshold": 0.5}}), encoding="utf-8")
    monkeypatch.setenv("TKB_ARCHIVE_THRESHOLD", "0.9")
    assert load_config(app).archive.threshold == 0.9


def test_load_config_unchanged_when_nothing_overrides(tmp_path, monkeypatch):
    app = tmp_path / "app.yaml"
    app.write_text(yaml.safe_dump({"archive": {"threshold": 0.5}}), encoding="utf-8")
    monkeypatch.delenv("TKB_ARCHIVE_THRESHOLD", raising=False)
    # No runtime file at the default path in the test's cwd.
    assert load_config(app).archive.threshold == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_layers.py -v -k load_config`
Expected: FAIL — `assert 0.5 == 0.9` (env override not applied yet)

- [ ] **Step 3: Write minimal implementation**

Replace the body of `load_config` in `config/schema.py`:

```python
def load_config(path: Path | str | None = None) -> AppConfig:
    """Load the committed config with environment and runtime overrides applied.

    Thin wrapper over config.layers.effective_config so that every existing
    caller picks up the override layers without changing its call site.
    """
    from config.layers import effective_config

    return effective_config(app_path=path)[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/config/ tests/engine/test_config.py -v`
Expected: PASS — the two new tests plus every pre-existing config test.

- [ ] **Step 5: Commit**

```bash
git add config/schema.py tests/config/test_layers.py
git commit -m "refactor(config): load_config resolves all three layers"
```

---

## Task 5: Reachability sweep

**Files:**
- Test: `tests/config/test_reachability.py`

**Interfaces:**
- Consumes: `derived_names` (Task 1), `derived_names` over `AppConfig`.
- Produces: no production code; this is the regression guard.

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_reachability.py
"""Regression guard: no configurable knob may be unreachable from the env.

This is the defect the change exists to fix -- a knob reviewed, documented as
configurable, and then reachable only by editing committed code.
"""
from __future__ import annotations

from config.overrides import derived_names, leaf_paths
from config.schema import AppConfig


def test_every_leaf_derives_an_override_name():
    paths = leaf_paths(AppConfig)
    names = derived_names(AppConfig)
    unreachable = [path for path in paths if not names.get(path)]
    assert not unreachable, f"no environment override derivable for: {unreachable}"


def test_derived_names_are_unique():
    names = derived_names(AppConfig)
    seen: dict[str, str] = {}
    collisions = []
    for path, env_name in names.items():
        if env_name in seen:
            collisions.append((seen[env_name], path, env_name))
        seen[env_name] = path
    assert not collisions, f"colliding override names: {collisions}"


def test_derived_names_use_the_documented_prefix():
    for env_name in derived_names(AppConfig).values():
        assert env_name.startswith("TKB_")
```

- [ ] **Step 2: Run the guard and confirm it is sensitive**

Run: `uv run pytest tests/config/test_reachability.py -v`
Expected: PASS once Tasks 1–3 are correct. If it fails, the derivation in
Task 1 is wrong — fix it there rather than here.

A guard that has never been observed to fail proves nothing, so add this
third test to the same file to pin its sensitivity:

```python
def test_guard_reports_unknown_paths_as_unreachable():
    """The guard is meaningful only if a missing name is detectable."""
    names = derived_names(AppConfig)
    assert names.get("engine.not_a_real_key") is None
    assert not names.get("engine.not_a_real_key")
```

Run: `uv run pytest tests/config/test_reachability.py -v`
Expected: PASS (3 tests).

- [ ] **Step 3: Commit**

```bash
git add tests/config/test_reachability.py
git commit -m "test(config): guard every knob against env unreachability"
```

---

## Task 6: Archive composes beneath the derived rule

**Files:**
- Test: `tests/config/test_layers.py`

**Interfaces:**
- Consumes: `effective_config` (Task 3), `merge_archive_config`
  (`src/engine/components/archive/config.py`, **unmodified**).
- Produces: no production code. Asserts the three-way order
  `ARCHIVE_*` → `TKB_ARCHIVE_*` → `app.yaml`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/config/test_layers.py
import os
from src.engine.components.archive.config import merge_archive_config


def test_archive_alias_outranks_derived_name(tmp_path, monkeypatch):
    app = tmp_path / "app.yaml"
    app.write_text(yaml.safe_dump({"archive": {"threshold": 0.5}}), encoding="utf-8")
    runtime = tmp_path / "runtime.yaml"

    # Derived name lands in the AppConfig passed to the archive merge.
    cfg, sources = effective_config(
        app, runtime, environ={"TKB_ARCHIVE_THRESHOLD": "0.8"}
    )
    assert cfg.archive.threshold == 0.8
    assert sources["archive.threshold"] == "env"

    # The ArchiveSettings tri-state reads the real environment; with
    # ARCHIVE_THRESHOLD set it must win over the derived value.
    monkeypatch.setenv("ARCHIVE_THRESHOLD", "0.9")
    from config.settings import ArchiveSettings

    monkeypatch.setattr(
        "src.engine.components.archive.config.settings",
        type("S", (), {"archive": ArchiveSettings(_env_file=None)})(),
    )
    assert merge_archive_config(cfg).threshold == 0.9


def test_archive_falls_back_to_derived_name_when_alias_unset(tmp_path, monkeypatch):
    app = tmp_path / "app.yaml"
    app.write_text(yaml.safe_dump({"archive": {"threshold": 0.5}}), encoding="utf-8")
    runtime = tmp_path / "runtime.yaml"
    cfg, _ = effective_config(app, runtime, environ={"TKB_ARCHIVE_THRESHOLD": "0.8"})

    monkeypatch.delenv("ARCHIVE_THRESHOLD", raising=False)
    from config.settings import ArchiveSettings

    monkeypatch.setattr(
        "src.engine.components.archive.config.settings",
        type("S", (), {"archive": ArchiveSettings(_env_file=None)})(),
    )
    assert merge_archive_config(cfg).threshold == 0.8
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `uv run pytest tests/config/test_layers.py -v -k archive`
Expected: PASS, because this asserts existing behaviour composed with the new
layer. If it fails, do **not** change `merge_archive_config` — the
composition assumption in `design.md` is wrong, so stop and report it rather
than working around it.

- [ ] **Step 3: Confirm the archive suite is untouched**

Run: `uv run pytest tests/engine/test_archive.py tests/config/test_settings.py -v`
Expected: PASS with no source edits to either file.

- [ ] **Step 4: Commit**

```bash
git add tests/config/test_layers.py
git commit -m "test(config): pin archive alias precedence over derived names"
```

---

## Task 7: BFF config routes

**Files:**
- Modify: `src/frontend/webapp/server/routes_config.py`
- Test: `tests/frontend/test_routes_config.py` (create if absent)

**Interfaces:**
- Consumes: `effective_config` (Task 3), `SOURCE_*` constants.
- Produces:
  - `GET /api/config` → `{"config": {...}, "sources": {path: source}}`
  - `PUT /api/config` → merges the submitted partial into
    `config/app.runtime.yaml` and returns the new effective config.

- [ ] **Step 1: Write the failing test**

```python
# tests/frontend/test_routes_config.py
"""Config routes read the effective config and write only the runtime layer."""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.frontend.webapp.server.app import app

client = TestClient(app)


def test_get_reports_a_source_for_every_key():
    body = client.get("/api/config").json()
    assert "config" in body and "sources" in body
    assert body["sources"]["archive.threshold"] in {
        "default", "app.yaml", "env", "runtime"
    }


def test_put_writes_runtime_layer_and_leaves_app_yaml(tmp_path, monkeypatch):
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text("archive:\n  threshold: 0.5\n", encoding="utf-8")
    runtime_yaml = tmp_path / "runtime.yaml"
    monkeypatch.setattr(
        "src.frontend.webapp.server.routes_config.DEFAULT_RUNTIME_PATH", runtime_yaml
    )
    before = app_yaml.read_text(encoding="utf-8")

    resp = client.put("/api/config", json={"archive": {"threshold": 0.7}})
    assert resp.status_code == 200
    assert app_yaml.read_text(encoding="utf-8") == before
    assert "0.7" in runtime_yaml.read_text(encoding="utf-8")


def test_put_merges_rather_than_replacing(tmp_path, monkeypatch):
    runtime_yaml = tmp_path / "runtime.yaml"
    runtime_yaml.write_text("archive:\n  threshold: 0.7\n", encoding="utf-8")
    monkeypatch.setattr(
        "src.frontend.webapp.server.routes_config.DEFAULT_RUNTIME_PATH", runtime_yaml
    )
    client.put("/api/config", json={"engine": {"ingest": {"doc_concurrency": 3}}})
    written = runtime_yaml.read_text(encoding="utf-8")
    assert "threshold: 0.7" in written
    assert "doc_concurrency: 3" in written
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/frontend/test_routes_config.py -v`
Expected: FAIL — `KeyError: 'sources'` and no runtime file written.

- [ ] **Step 3: Write minimal implementation**

```python
"""BFF config routes: read the effective config, write the runtime layer."""
from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from config.layers import DEFAULT_RUNTIME_PATH, effective_config, read_layer
from config.schema import AppConfig

router = APIRouter(prefix="/config", tags=["config"])


@router.get("")
async def get_config():
    cfg, sources = effective_config()
    return {"config": cfg.model_dump(), "sources": sources}


@router.put("")
async def put_config(body: dict):
    try:
        submitted = AppConfig.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, e.errors())

    # Persist only what the caller actually sent. Writing the whole model
    # would make every key runtime-sourced and blank out the environment.
    partial = submitted.model_dump(exclude_unset=True)
    merged = _deep_merge(read_layer(DEFAULT_RUNTIME_PATH), partial)

    DEFAULT_RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_RUNTIME_PATH.write_text(
        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    cfg, sources = effective_config()
    return {"config": cfg.model_dump(), "sources": sources}


def _deep_merge(base: dict, overlay: dict) -> dict:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/frontend/test_routes_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/frontend/webapp/server/routes_config.py tests/frontend/test_routes_config.py
git commit -m "feat(webapp): config route writes the runtime layer"
```

---

## Task 8: Ignore the runtime layer

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add the ignore rule**

Append under the config section of `.gitignore`:

```
# Runtime configuration overrides written by the config API. Deployment
# state, never committed; the committed defaults live in config/app.yaml.
/config/app.runtime.yaml
```

- [ ] **Step 2: Verify**

Run: `git check-ignore -v config/app.runtime.yaml`
Expected: prints the `.gitignore` line number and pattern, exit 0.

Run: `git status --porcelain config/`
Expected: no output (the runtime file is not reported as untracked).

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore(config): ignore runtime override layer"
```

---

## Task 9: Shrink `.env.example`

**Files:**
- Modify: `.env.example`
- Test: `tests/config/test_env_example.py`

**Interfaces:**
- Consumes: nothing from earlier tasks at runtime; the tests read the template
  file and the schema.
- Produces: `.env.example` listing only per-deployment facts.

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_env_example.py
"""The environment template lists per-deployment facts, and stays honest."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATE = Path(".env.example")
COMPOSE = Path("docker-compose.yml")
# Values a deployment cannot start without, whatever else changes.
REQUIRED = {
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
    "POSTGRES_USER", "POSTGRES_PASSWORD",
    "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
    "EMBEDDING_BASE_URL", "EMBEDDING_MODEL",
    "APP_PORT",
}


def declared_keys() -> set[str]:
    keys = set()
    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
        if match:
            keys.add(match.group(1))
    return keys


def test_template_is_short():
    assert len(TEMPLATE.read_text(encoding="utf-8").splitlines()) <= 60


def settings_env_names() -> set[str]:
    """Every env name a pydantic-settings class reads, prefix included.

    A key can legitimately be absent from compose and still be read: compose
    hardcodes POSTGRES_HOST, NEO4J_URI, APP_HOST and UPLOADS_DIR to their
    in-container values, while the app reads them from the environment.
    """
    from pydantic_settings import BaseSettings

    from config import settings as settings_module

    names: set[str] = set()
    for obj in vars(settings_module).values():
        if isinstance(obj, type) and issubclass(obj, BaseSettings):
            prefix = obj.model_config.get("env_prefix", "")
            for field in obj.model_fields:
                names.add(f"{prefix}{field}".upper())
    return names


def test_no_declared_key_is_unread():
    compose = COMPOSE.read_text(encoding="utf-8")
    known = settings_env_names()
    unread = [
        key
        for key in declared_keys()
        if f"${{{key}" not in compose and key.upper() not in known
    ]
    assert not unread, f"declared in .env.example but read nowhere: {sorted(unread)}"


def test_required_keys_are_present():
    missing = REQUIRED - declared_keys()
    assert not missing, f"missing from .env.example: {sorted(missing)}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_env_example.py -v`
Expected: FAIL on `test_template_is_short` (195 lines) and on
`test_no_declared_key_is_unread` for the `HINDSIGHT_*` keys that compose does
not pass through.

- [ ] **Step 3: Rewrite the template**

Replace `.env.example` with exactly this content:

```
# Copy to .env and fill in real values. .env is gitignored; this template is
# committed. Only per-deployment values belong here -- credentials, endpoints,
# host ports, paths and switches. Behaviour defaults live in config/app.yaml;
# every key there is overridable as TKB_<PATH>. Prose: docs/config-reference.md

# -- Postgres (pgvector, managed by docker compose) -------------------
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
POSTGRES_DB=knowledge_base
POSTGRES_USER=kb_user
POSTGRES_PASSWORD=change-me

# -- Neo4j (managed by docker compose) --------------------------------
# NEO4J_BOLT_PORT must match the port in NEO4J_URI.
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=change-me
NEO4J_BOLT_PORT=7687
NEO4J_HTTP_PORT=7474

# -- Embeddings (OpenAI-compatible /v1/embeddings) --------------------
# Model must emit 768-dim vectors (stored width is fixed).
EMBEDDING_BASE_URL=http://localhost:11434/v1
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_API_KEY=

# -- Chat/analysis LLM (OpenAI-compatible /chat/completions) ---------
# Empty LLM_BASE_URL disables the LLM.
LLM_MODEL=
LLM_BASE_URL=
LLM_API_KEY=

# -- Reranker (provider: local | http | none) -------------------------
RERANKER_PROVIDER=none
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_BASE_URL=
RERANKER_API_KEY=

# -- Image generation (image-based PPT; no LLM credential fallback) ---
PPT_ENABLED=false
IMAGE_PROVIDER=ark
IMAGE_BASE_URL=
IMAGE_MODEL=
IMAGE_API_KEY=

# -- App server -------------------------------------------------------
APP_HOST=0.0.0.0
APP_PORT=8000
UPLOADS_DIR=uploads

# -- Runtime dirs -----------------------------------------------------
# HOME hosts the HuggingFace cache mount; ARCHIVE_WORKSPACE_DIR is the
# inbox/archive workspace and is deliberately environment-only.
HOME=./team-kb-runtime
ARCHIVE_WORKSPACE_DIR=workspace

# -- Deployment switches ----------------------------------------------
HINDSIGHT_GRAPH_WORKER_ENABLED=true
HINDSIGHT_CONVERSATION_MEMORY_ENABLED=true
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/config/test_env_example.py -v`
Expected: PASS (3 tests). If `test_no_declared_key_is_unread` still fails,
the offending key is genuinely not passed by compose — either add it to the
compose environment block in the same commit, or drop it from the template.

- [ ] **Step 5: Commit**

```bash
git add .env.example tests/config/test_env_example.py
git commit -m "docs(config): shrink env template to deployment facts"
```

---

## Task 10: Documentation

**Files:**
- Modify: `docs/config-reference.md`

- [ ] **Step 1: Add the layer model section**

Add near the top of `docs/config-reference.md`:

```markdown
## 配置分层

配置按三层解析,上层覆盖下层(逐键):

| 层 | 位置 | 写入方 |
|---|---|---|
| 默认值 | `config/app.yaml`(已提交) | 运行时从不写入 |
| 部署级 | `.env` / CICD 的 `deploy.env` | 人工 |
| 运行时 | `config/app.runtime.yaml` | 配置 API(`PUT /api/config`) |

`config/app.yaml` 的每个叶子键都可由环境变量覆盖,命名为
`TKB_` + 路径各段大写后以 `_` 连接,例如
`engine.ingest.chunk_concurrency` → `TKB_ENGINE_INGEST_CHUNK_CONCURRENCY`。

`ARCHIVE_*` 是既有别名,优先级高于其派生名
(`ARCHIVE_ENABLED` > `TKB_ARCHIVE_ENABLED` > `app.yaml`)。

### 暂未包含

- `HINDSIGHT_*` 与 `app.yaml: engine.memory` 的合并(键名不同,尚未统一)。
- pi-agent sidecar 的 `PI_AGENT_*` / `TKB_*` 配置。
- 运行时覆盖层的持久化:`config/` 位于镜像内且未挂载,`PUT /api/config`
  的写入会在容器重建时丢失(与改动前的行为一致)。
```

- [ ] **Step 2: Verify**

Run: `grep -c "TKB_ENGINE_" docs/config-reference.md`
Expected: a non-zero count.

Run: `grep -c "app.runtime.yaml" docs/config-reference.md`
Expected: a non-zero count.

- [ ] **Step 3: Commit**

```bash
git add docs/config-reference.md
git commit -m "docs(config): document the three config layers"
```

---

## Task 11: Full validation and PR

- [ ] **Step 1: Lint**

Run: `uv run ruff check`
Expected: no findings, exit 0.

- [ ] **Step 2: Full test suite**

Run: `uv run pytest`
Expected: exit 0, no failures. Pay attention to
`tests/engine/test_archive.py` and `tests/config/test_settings.py`, which
must pass with no edits.

- [ ] **Step 3: Confirm no behaviour change without new inputs**

Resolve with the environment isolated and the runtime layer pointed at a
path that cannot exist, and compare against the plain committed YAML. This
avoids `git stash`, which would leave the new untracked modules in place and
silently invalidate the comparison:

```bash
uv run python -c "
import yaml
from config.layers import effective_config
from config.schema import AppConfig

raw = yaml.safe_load(open('config/app.yaml', encoding='utf-8'))
baseline = AppConfig.model_validate(raw).model_dump()
resolved, _ = effective_config(runtime_path='/nonexistent/none.yaml', environ={})

assert resolved.model_dump() == baseline, 'the layers changed the defaults'
print('IDENTICAL - no behaviour change without new inputs')
"
```

Expected: `IDENTICAL - no behaviour change without new inputs`. If it
differs, a layer is applying when it should not.

- [ ] **Step 4: Confirm the openspec change still validates**

Run: `openspec validate unify-config-layers --strict`
Expected: `Change 'unify-config-layers' is valid`.

- [ ] **Step 5: Open the PR**

```bash
git push -u origin feat/unify-config-layers
gh pr create --base develop --fill
```

Then edit the PR body to follow `.github/PULL_REQUEST_TEMPLATE.md`, stating
the spec delta (`openspec/changes/unify-config-layers/`) and the code.

Verify: `gh pr view --json baseRefName,url` reports `develop`.

---

## Self-Review Notes

Checked against `specs/configuration/spec.md`:

| Requirement | Task |
|---|---|
| Three ordered layers | 3 |
| Every key has a derived override | 1, 2, 5 |
| Collision-free names | 1, 5 |
| Prefixed names remain honoured | 6 |
| Per-key source reporting | 3, 7 |
| Runtime edits never mutate defaults | 7 |
| Template lists only deployment facts | 9 |

Type consistency verified across tasks: `effective_config` returns
`tuple[AppConfig, dict[str, str]]` in Tasks 3, 4, 6 and 7;
`apply_env_overrides` returns `tuple[dict, set[str]]` in Tasks 2 and 3;
`DEFAULT_RUNTIME_PATH` is defined in Task 3 and imported by Task 7.
