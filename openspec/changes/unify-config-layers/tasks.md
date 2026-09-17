# Tasks

## 1. Derived override mechanism

- [x] 1.1 Add `config/overrides.py` with a function that walks an
      `AppConfig`-shaped schema and returns `{leaf_path: derived_env_name}`
      for every leaf.
      Verify: `uv run python -c "from config.overrides import derived_names;
      n = derived_names(); print(len(n));
      print(n['engine.ingest.chunk_concurrency'])"` prints a count matching
      the schema's leaf count and `TKB_ENGINE_INGEST_CHUNK_CONCURRENCY`.

- [x] 1.2 Add a collision guard asserting the generated name set has no
      duplicates, so two schema paths can never share an override name.
      Verify: a test that mutates the schema with two colliding paths fails;
      `uv run pytest tests/config/test_overrides.py -k collision` passes on
      the unmodified schema.

- [x] 1.3 Keep `ARCHIVE_*` working as a documented alias for its `archive`
      key, taking precedence over the derived `TKB_ARCHIVE_*` equivalent.
      Verify: `uv run pytest tests/config/test_overrides.py -k alias` passes,
      including a case where both names are set for one knob and the
      `ARCHIVE_*` value is resolved.

## 2. Layer resolution

- [x] 2.1 Make `load_config` resolve three layers in order — committed
      `config/app.yaml`, then `config/app.runtime.yaml` if present, then
      environment overrides. `AppConfig`'s schema is unchanged.
      Verify: `uv run pytest tests/config/test_layers.py` passes, including
      "no runtime file resolves identically to today".

- [x] 2.2 Confirm the derived rule composes with the existing archive merge
      instead of replacing it: `ARCHIVE_*` beats `TKB_ARCHIVE_*`, which beats
      the committed default. No archive code and no archive test changes.
      Verify: `uv run pytest tests/engine/test_archive.py tests/config/test_settings.py`
      passes unchanged, plus a three-way precedence test in
      `tests/config/test_layers.py`.

- [x] 2.3 Add the reachability sweep: every leaf path in the resolved schema
      derives an override name, and the test fails if any does not.
      Verify: `uv run pytest tests/config/test_reachability.py` passes; the
      test names the offending path in its failure message.

## 3. BFF config routes

- [x] 3.1 `PUT /api/config` writes `config/app.runtime.yaml` and never
      mutates `config/app.yaml`.
      Verify: `uv run pytest tests/frontend -k config` passes, asserting
      `config/app.yaml` is byte-identical before and after the request.

- [x] 3.2 `GET /api/config` returns the effective configuration with a
      per-key source of `default`, `app.yaml`, `env`, or `runtime`.
      Verify: same test asserts a key set only in the environment reports
      `env`, and one set in no layer reports `default`.

- [x] 3.3 Add `config/app.runtime.yaml` to `.gitignore`.
      Verify: `git check-ignore -v config/app.runtime.yaml` prints the
      matching `.gitignore` line.

## 4. Shrink `.env.example`

- [x] 4.1 Rewrite `.env.example` to list only per-deployment facts —
      credentials, endpoints, host ports, paths and feature switches — with
      behaviour defaults left to `config/app.yaml`. Target roughly 45 lines.
      Verify: `wc -l .env.example` reports a count at or below 60.

- [x] 4.2 Add a test that every key defined in `.env.example` is read by some
      consumer, so the template cannot accumulate dead entries.
      Verify: `uv run pytest tests/config/test_env_example.py -k consumed`
      passes; adding a fabricated key makes it fail.

- [x] 4.3 Add a test that every deployment-required key is present in the
      template (the values a deployment cannot run without).
      Verify: `uv run pytest tests/config/test_env_example.py -k required`
      passes; removing `POSTGRES_PASSWORD` makes it fail.

## 5. Documentation

- [x] 5.1 Document the layer model, the derived naming rule, and which names
      are aliases in `docs/config-reference.md`.
      Verify: the file contains the three layer names and the `TKB_ENGINE_`
      derivation example; `grep -c "TKB_ENGINE_" docs/config-reference.md`
      is non-zero.

- [x] 5.2 Record the deferred items — knob consolidation, pi-agent sidecar,
      runtime-override durability — in `docs/config-reference.md` so the
      boundary is visible to users, not only in the change archive.
      Verify: `grep -c "runtime" docs/config-reference.md` is non-zero and the
      deferred notes appear under a heading naming them.

## 6. Validation

- [x] 6.1 `uv run ruff check` reports no findings.
      Verify: command exits zero.

- [x] 6.2 `uv run pytest` passes in full, including the new `tests/config/`
      suite.
      Verify: command exits zero with no failures.

- [x] 6.3 Confirm no deployment action is required: with no runtime file and
      no new env names, resolved configuration matches the pre-change values.
      Verify: resolve configuration on a clean checkout of the previous
      revision and on this one, and diff the serialized effective config —
      no difference.

- [x] 6.4 Open the PR into `develop` with the spec delta and code, described
      with `.github/PULL_REQUEST_TEMPLATE.md`.
      Verify: `gh pr view --json baseRefName,url` reports `develop`.
