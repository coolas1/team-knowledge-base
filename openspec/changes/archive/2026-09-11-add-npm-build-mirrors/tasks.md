## 1. Build recipe: configurable registries

- [x] 1.1 In `Containerfile`, declare the build arguments for the SPA
      dependency step: `NPM_REGISTRY` (default
      `https://registry.npmmirror.com`), `NPM_AUDIT_REGISTRY` (default
      `https://registry.npmjs.org`) and `NPM_PROXY` (default empty). Keep the
      defaults in the recipe so a direct `podman build` gets them too, per
      design decision 2.

- [x] 1.2 Rewrite the SPA `RUN` step to use them: run `npm ci` against
      `NPM_REGISTRY`, run `npm run security` against `NPM_AUDIT_REGISTRY`, and
      run `npm run build` against `NPM_REGISTRY`. Export
      `npm_config_proxy`/`npm_config_https_proxy` **only** when `NPM_PROXY` is
      non-empty. Do not regress the existing layer property: `node_modules`
      must still be installed and removed within the single layer, and
      `dist/` must survive the later `COPY src/` overlay (`.dockerignore`
      excludes `**/dist`).

- [x] 1.3 Convert the SPA dependency layer to a `URL` source whose `inputs`
      cover the client source tree, `package.json`, `package-lock.json`, and
      all three argument values, so the configured registries participate in
      the cache key (design decision 3).

- [x] 1.4 Verify the layer properties this change must not break: with an
      unchanged source tree and unchanged registries, rebuild and confirm the
      SPA step is a cache hit; then change only `NPM_REGISTRY` and confirm the
      step re-runs. If `URL`+`inputs` does not behave as expected under this
      buildah, fall back to declaring the registries as ordinary `ARG`s
      consumed on the `RUN` line and re-verify both properties.

## 2. Composition and pipeline wiring

- [x] 2.1 Add the three arguments to `docker-compose.yml`'s
      `webapp.build.args`, following the existing `PYPI_MIRROR`
      comment-and-default style (`${VAR:-}`), so the environment can override
      each and a plain `compose build` still gets the recipe defaults.

- [x] 2.2 In `cicd/pipeline.sh`, default the registry variables alongside
      `PYPI_MIRROR` (override-able from `deploy.env` / the environment) and
      pass them to `podman compose build`. Leave `NPM_PROXY` unset by default
      — a local-only escape hatch, never a committed default. Extend the
      existing build log line so the effective registries are visible in the
      pipeline log, as `PYPI_MIRROR` already is.

- [x] 2.3 Confirm no machine-specific value is committed: `NPM_PROXY` is
      empty by default everywhere, and the only addresses in tracked files are
      public mirror and upstream registry URLs.

## 3. Documentation

- [x] 3.1 Document the three variables in `.env.example` next to the existing
      build/dependency notes: what each does, the defaults, that the audit
      registry is deliberately the authoritative one because mirrors do not
      implement the audit API, and that the proxy is a local-only opt-in.

## 4. Verification

- [x] 4.1 Build with no explicit registry configuration (cold SPA layer) and
      confirm the install succeeds from the mirror and the security gate
      passes — the failure this change exists to fix. Record the step timings
      from the build log.

- [x] 4.2 Reproduce the original failure mode is gone: confirm the build does
      not open connections to the upstream registry for install, e.g. inspect
      the build's npm connections (`ss -tanp`) or the npm
      `--loglevel=http` destination, and that no `ETIMEDOUT` appears.

- [x] 4.3 Verify the audit registry separation: run a build with
      `NPM_REGISTRY` pointing at a mirror that does not implement the audit API
      and confirm the security step still succeeds, then confirm a genuinely
      failing audit (an advisory not in the accepted list) still fails the
      build — i.e. the separation did not defang the gate.

- [ ] 4.4 End-to-end: re-enable the pipeline (`systemctl --user start
      team-kb-cicd.timer`) once `harden-ask-page-session-recovery`'s code is on
      `origin/main`, and confirm the deploy completes and the deployed image
      serves. This is the change that unblocks that deploy.

- [x] 4.5 Record the follow-up in `docs/todos.md`: the build stage has no
      bounded retry and systemd can kill a long attempt leaving orphaned
      fetch processes, which is why a lengthened start timeout made things
      worse. Note it as separate work, with the option of bounding the build
      stage and cleaning up orphans on abort.

## Implementation notes

- **1.3 / URL source:** no `URL`-source-with-`inputs` construct exists in
  Containerfile/buildah (verified on 1.33.7). The task's own fallback
  (ordinary `ARG`s consumed on the `RUN` line) was implemented instead, and
  both cache properties verified empirically — see design decision 3's
  implementation note.
- **2.1 / compose defaults:** an empty value passed through compose
  `build.args` overrides a Containerfile `ARG` default (verified with
  docker-compose 2.40), so the literal `${VAR:-}` shape would have defeated
  the mirror default for plain `compose build`. The compose file duplicates
  the recipe defaults in `${VAR:-<default>}` substitutions — see design
  decision 6.
- **Security gate hardening (added scope, user-approved):** while verifying
  the registry split, `security-gate.mjs` was found to pass vacuously when
  the audit registry returns a JSON error body (a mirror's 404 parses fine
  and carries no `vulnerabilities` key). The gate now rejects such
  responses — see design decision 5.
- **`npm ci` and the lockfile:** npm 10 honors `npm_config_registry` over
  the lockfile's absolute `resolved` URLs (verified: mirror install in 3 s,
  zero `registry.npmjs.org` contacts), so no lockfile rewriting is needed;
  integrity hashes are still enforced against the mirror's tarballs.

## Verification record (2026-09-11, LAN build host)

- **4.1 (defaults, cold SPA layer):** full `podman build` with no npm
  registry args succeeded in 1 m 58 s total. SPA step: `npm ci` from
  npmmirror **9 s** (286 packages; was a ~30-min ETIMEDOUT stall), security
  gate passed against npmjs, `vite build` 7.18 s. No `ETIMEDOUT` in the log.
- **4.2 (no upstream contact for install):** inside the built image,
  `npm ci --loglevel=http` against the mirror issued **288 requests, all to
  `registry.npmmirror.com`, zero to `registry.npmjs.org`**. Host-level `ss`
  is blind to the build's connections (buildah RUN steps run in their own
  netns); the http-level destination log is the equivalent evidence. The
  audit step does contact npmjs — by design, via `NPM_AUDIT_REGISTRY`.
- **4.3 (audit separation):** the 4.1 build itself ran with
  `NPM_REGISTRY` = npmmirror (a mirror that 404s the audit API) and the
  security step still passed, auditing against npmjs. A genuinely failing
  audit still fails: a scratch install of `node-serialize@0.0.4` (critical,
  GHSA-q4v7-4rhw-9hqm, not in the accepted list) exited 1 with the advisory
  printed; the hardened gate also exits 1 on a mirror 404 (fail-closed on
  registry errors — previously a vacuous pass; see design decision 5).
- **1.4 (cache properties, real SPA layer, buildah 1.33.7):** identical
  rebuild → STEP 15 `Using cache` (same layer ID); rebuild with only
  `NPM_REGISTRY` changed (Tencent mirror) → STEP 15 re-ran (npm ci 8 s).
  Both properties hold with the ARG-on-RUN-line implementation.
- Repo checks after the change: `ruff check` clean, `pytest` 372 passed /
  5 skipped, SPA `npm test` 52 passed.
