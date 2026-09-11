## Context

The build recipe already has an opt-in regional mirror for Python: the
pipeline defaults `PYPI_MIRROR` to `https://mirrors.aliyun.com/pypi/simple`,
`docker-compose.yml` forwards it as a build arg, and the `Containerfile` feeds
it to `uv sync`. The SPA dependency step has no equivalent, so it is the only
step that must reach the open internet from the build container.

Two facts about that container shape the design and are not obvious:

- **npm ignores `HTTP_PROXY`/`HTTPS_PROXY`.** It reads only its own
  `npm_config_proxy`/`npm_config_https_proxy`. Compose forwards `--env-file`
  variables into the container's *runtime* environment, not into build RANs,
  and `build.args` currently carries only `PYPI_MIRROR`. So a build RAN sees
  `HTTP_PROXY` (inherited from the pipeline's env) and npm disregards it.
- **Mirrors do not uniformly implement npm's audit API.** Measured:

  | Registry | `npm ci` (cold) | `npm audit --omit=dev` |
  |---|---|---|
  | `registry.npmjs.org` direct | stalls / times out | **3 s, OK** |
  | `registry.npmmirror.com` | **5–8 s** | `404 [NOT_IMPLEMENTED]`, exit 1 |
  | `mirrors.cloud.tencent.com/npm` | 9 s | OK, matches npmjs |
  | `repo.huaweicloud.com/repository/npm` | — | `405 Method Not Allowed`, exit 1 |

  `npm run security` runs `security-gate.mjs`, which is fail-closed. A mirror
  that cannot serve the audit API therefore **blocks the deploy**, not merely
  degrades it.

An end-to-end overlay build with the proposed plumbing — mirror for install,
npmjs for audit, no proxy, cold cache — measured `npm ci` 5 s, security 5 s,
`vite build` 11 s, and succeeded.

## Goals / Non-Goals

**Goals:**

- A build with no extra configuration completes on the LAN build host, using a
  regional mirror rather than a direct route to an upstream registry.
- The security gate keeps running against an authoritative source, so fixing
  fetching does not weaken or block the gate.
- No machine-specific value is committed: the proxy is opt-in and empty by
  default.
- The dependency layer stays cached across ordinary source edits.

**Non-Goals:**

- Making the build's *network* reliable. This change routes around an
  unreliable route; it does not fix the route.
- Changing what the security gate checks, its accepted-advisory list, or
  making it tolerate an unreachable audit endpoint. Failing closed is
  deliberate.
- Bounding the build stage's wall-clock or cleaning up orphaned build
  processes when systemd kills a long attempt. That interaction is real — a
  lengthened start timeout makes each doomed attempt more expensive and can
  leave concurrent orphaned fetches — but it is a separate concern with its own
  trade-offs, and is recorded as follow-up work rather than folded in here.
- Moving the CI/CD stable directory off the shared home filesystem. Measured
  as not the cause: the build context from there is 490.8 kB and the gate's
  clone-resident `npm install` takes 6 s.

## Decisions

### 1. Separate the install registry from the security-metadata registry

Two arguments, not one: an install registry defaulting to the mirror, and an
audit registry defaulting to `registry.npmjs.org`. `npm run security` is run
with only the audit registry overridden.

*Alternatives considered:* a single registry for everything — rejected, since
the mirror that makes install fast cannot serve the audit API and
`security-gate.mjs` fails closed, so it would block every deploy. Defaulting
to Tencent's mirror, which implements the audit API correctly, would allow one
registry — rejected as the default because it couples the correctness of the
security gate to a third party's API fidelity; if that mirror returned an empty
or stale vulnerability set, the gate would silently pass. Keeping the audit on
the authoritative registry costs ~3 s and removes that class of doubt. (The
split also means a mirror can be swapped without re-validating the gate.)

### 2. Defaults live in the build recipe, not only in the pipeline

The registry defaults are declared as `Containerfile` argument defaults, and
`docker-compose.yml` supplies them from the environment with those defaults as
the fallback. This way a plain `podman build -f Containerfile .` also gets the
mirror, rather than only builds routed through the pipeline. `PYPI_MIRROR`'s
existing shape (defaulted in the pipeline, forwarded by compose) is left as it
is; the two are not made uniform in this change.

*Alternatives considered:* defaulting only in `cicd/pipeline.sh` — rejected
because it leaves direct builds, and any future build entry point, on the slow
path; the failure this change fixes would survive outside the pipeline.

### 3. Registry configuration participates in cache invalidation

The three registry arguments are ordinary `ARG`s consumed directly on the SPA
`RUN` line, so their values are part of that layer's cache key. A rebuild
with unchanged source and unchanged registries is therefore still a cache
hit, while a registry change invalidates the layer.

*Implementation note:* the original plan was a `URL` source with `inputs`
covering the client tree and the argument values, but no such construct
exists in Containerfile/buildah (1.33.7); the task list's pre-authorized
fallback (plain `ARG`s on the `RUN` line) was used instead. Both cache
properties were verified empirically against this buildah (unchanged args →
cache hit; changed `NPM_REGISTRY` → re-run), on a minimal Containerfile and
again on the real SPA layer.

*Alternatives considered:* passing the registry as a plain environment
variable on the `RUN` — this is the trap. An `ENV` or inline variable that is
not part of the hash lets a changed registry silently reuse a layer built
against a different registry, which would make the behaviour hard to reason
about and could pin a stale mirror. The hash must see the registry.

### 4. Proxy is opt-in and empty by default

A single proxy argument, empty by default, exported to npm only when non-empty.
It exists so a developer building locally behind their own proxy can build
without editing tracked files.

*Alternatives considered:* defaulting it to the current host's proxy —
rejected, it is one developer's machine-specific service and must not be
committed. Note the asymmetry that motivated the whole change: today the build
gets `HTTP_PROXY` but npm ignores it, so the proxy is *present and ineffective*;
after this change it is *absent unless asked for*, which is both honest and
explicit.

### 5. The security gate fails closed on non-report audit responses

Verified during implementation: when the audit registry answers with a JSON
error body (e.g. a mirror's 404), `npm audit --json` prints that object on
stdout; the old `security-gate.mjs` parsed it, found no `vulnerabilities` key,
and **passed vacuously** — the "fails closed" premise above held only for
unparseable output, not registry errors. The gate now rejects any audit
response that has an `error` or lacks a `vulnerabilities` object, so pointing
`NPM_AUDIT_REGISTRY` at a mirror that cannot serve audits fails the build
loudly instead of silently passing. This hardening is part of this change
because the registry split is what makes that misconfiguration reachable.

### 6. Compose args duplicate the recipe defaults

An empty value passed through compose `build.args` **overrides** a Containerfile
`ARG` default (verified with docker-compose 2.40 + buildah 1.33: with
`NPM_REGISTRY: ${NPM_REGISTRY:-}` and the variable unset, the build saw an
empty registry). The PYPI_MIRROR-style `${VAR:-}` substitution therefore cannot
carry a non-empty recipe default, so `docker-compose.yml` repeats the defaults
in its `${VAR:-<default>}` substitutions. The environment still overrides each
variable, and a plain `compose build` still gets the mirror.

## Risks / Trade-offs

- **A mirror can lag a brand-new package release.** → The registry is a build
  argument, so a build can point elsewhere without a code change; and audit
  stays authoritative. A hard failure to resolve is visible and immediate, not
  a silent wrong result.
- **A third-party mirror serves the install bytes.** → `npm ci` verifies each
  tarball against the `integrity` hash in `package-lock.json`, so a mirror
  cannot substitute different content for a pinned dependency; it can only
  withhold.
- **The build recipe now names a specific regional mirror as its default.** →
  Intended, and overridable; a public-checkout build can pass an empty value to
  fall back to upstream. Documented in `.env.example`.
- **Wiring the registries into the cache key changes how the npm layer is
  cached.** → Both properties are verified in the task list: the layer must
  still be a cache hit on an unchanged rebuild, and a registry change must
  invalidate it. (`URL`+`inputs` turned out not to exist in buildah 1.33;
  the ARG-on-`RUN`-line fallback carries both properties — verified.)
- **Developers may assume the mirror is now required.** → It is not: the
  defaults are chosen for this network, not mandated by the code.
