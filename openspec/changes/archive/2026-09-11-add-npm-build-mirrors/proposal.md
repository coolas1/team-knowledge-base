## Why

The image build's SPA dependency step fetches from `registry.npmjs.org` over
whatever direct route the build host happens to have, and on the LAN build host
that route is unreliable: the step stalls and dies after ~30 minutes with

```
npm error code ETIMEDOUT
npm error network read ETIMEDOUT
```

Two consecutive pipeline runs failed this way on 2026-09-10, and a manual
reproduction failed identically. The app could not be deployed at all.

The cause is a gap in an otherwise-solved pattern, not a bandwidth problem.
Measured on the build host, in the real build image:

- `npm ci` from `registry.npmjs.org` **direct** — stalls and times out.
- `npm ci` from `registry.npmmirror.com`, cold cache — **5–8 s**.
- The `uv` step does not suffer this because `PYPI_MIRROR` (default
  `https://mirrors.aliyun.com/pypi/simple`) is already passed as a build arg.
  npm has no equivalent, so it alone goes to the open internet.
- The pipeline's *gate* runs `npm install` in ~6 s because the pipeline exports
  `npm_config_proxy`, but npm does **not** read `HTTP_PROXY`/`HTTPS_PROXY` —
  only its own `npm_config_proxy`. `docker-compose.yml` forwards only
  `PYPI_MIRROR` into build args, so build RANs receive `HTTP_PROXY` and npm
  ignores it. `ss` confirmed the build's npm held 15 established connections to
  Cloudflare (`104.16.x.34` = npmjs) and none to the proxy.

The machine-local proxy is one developer's own, so it cannot become a committed
default; a regional mirror is both faster and independent of any individual's
machine. The repository has already hit and fixed this class of failure once —
the `Containerfile` records that a build-time `curl` "was the 78%-of-wall-clock
cost of a build (flaky through the proxy: one run spent ~113 min on it, another
failed with curl exit 35)" — and the fix then was to stop depending on a
mutable upstream fetch. The npm step is the remaining one.

## What Changes

- **npm fetches become registry-configurable, defaulting to a regional
  mirror.** An install registry build argument defaults to
  `https://registry.npmmirror.com`, so a build with no extra configuration uses
  the fast regional path. The existing `PYPI_MIRROR` behaviour is unchanged.
- **The security audit is pinned to the authoritative registry.** Registry
  mirrors do not implement npm's audit API — `registry.npmmirror.com` answers
  `404 [NOT_IMPLEMENTED] /-/npm/v1/security/*`, and `npm run security`
  (`security-gate.mjs`) fails closed, so a single mirror setting would have
  **blocked every deploy**. The install and audit registries therefore become
  separate arguments, with the audit defaulting to `registry.npmjs.org`.
  Measured: audit against npmjs (3s) matches audit against a mirror that
  implements the API (Tencent), so the authoritative source is not slower.
- **The proxy becomes a default-empty, opt-in local escape hatch.** A proxy
  argument, empty by default, is exported to npm only when set, for a
  developer building locally behind their own proxy. It is never a committed
  default.
- **Registry configuration does not defeat layer caching.** The registry
  arguments are consumed on the SPA `RUN` line, so they participate in the
  layer's cache key; a build with the same source and the same mirror still
  hits the cached SPA layer, and only an actual change to the registry or the
  source invalidates it.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `container-build`: gains a requirement that the build's dependency fetches
  are directed at configurable registries with a mirror-friendly default, that
  the security-metadata fetch stays on the authoritative registry rather than
  a mirror, and that any proxy is opt-in and empty by default.

## Impact

- `Containerfile` — registry/proxy build arguments (defaults mirror + npmjs)
  and the npm step rewritten to use them; the arguments are consumed on the
  `RUN` line so hashing covers the registry configuration (the planned
  `URL`-source construct does not exist in this buildah; the task list's
  pre-authorized `ARG` fallback was used and verified).
- `docker-compose.yml` — the `webapp.build.args` block gains the new arguments.
  Their defaults are duplicated from the recipe in the `${VAR:-<default>}`
  substitutions, because an empty value passed through compose overrides a
  Containerfile `ARG` default (verified), which would silently send plain
  `compose build` installs back to upstream npmjs.
- `src/frontend/webapp/client/scripts/security-gate.mjs` — hardened to fail
  closed when the audit registry returns a JSON error instead of a
  vulnerability report (a mirror's 404 previously passed the gate vacuously;
  discovered while verifying the registry split).
- `cicd/pipeline.sh` — supplies the registry defaults (mirroring how
  `PYPI_MIRROR` is defaulted and logged) and passes them to `compose build`.
- `.env.example` — documents the new opt-in variables.
- No application, API, schema, or data change; no new dependency. The
  behaviour of a configured build is unchanged apart from which host serves
  packages.
- Out of scope: moving the CI/CD stable directory off the shared home
  filesystem. That was considered and measured as **not** the cause — the
  build context from that location is 490.8 kB and the gate's clone-resident
  `npm install` completes in 6 s.
