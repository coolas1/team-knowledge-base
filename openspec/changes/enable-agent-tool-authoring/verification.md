# Implementation verification — 2026-09-05

Implemented on `develop`, created from `main` at `53972ec9`. This change implements `enable-agent-tool-authoring`; the rejected `fix-pi-agent-runtime-tools` proposal is not the implementation basis. The existing local `gpus: all` change in Compose is preserved and excluded from this implementation's commit.

## Delivered behavior

Pi can execute an authored program, discover shared tools, publish an exactly tested version and call that version immediately. There are four bootstrap tools and no preinstalled date/calculation/search business implementations. A separate authenticated gateway owns Docker administration. Every execution and publish test runs in a disposable, resource-limited, non-root container without application/session/library mounts or direct network. Public reads and administrator-authorized API profiles go through a broker. SQLite/CAS versions, SHA256 source blobs, manifest/runtime version hashes, schema checks, retirement and runtime/permission rechecks support cross-session reuse.

The runtime loads authoring instructions and actual runner capabilities, propagates genuine tool errors through the Pi SDK, enforces tool/job/build/time budgets, and forwards only summarized authoring activity to the frontend. Activity history retains failure and repair after the final answer. The optional runner can be unavailable while ordinary TKB remains available.

## Automated checks

| Check | Result |
| --- | --- |
| Pi `npm run check` | Passed: dependency lockfile guard, TypeScript, 50 tests, build |
| Runner `npm run check` | Passed: TypeScript, 10 tests, build; 2 Docker cases excluded from this unit run |
| Runner `npm run test:integration` | Passed: 12 tests including real Docker jobs |
| Runner `npm run security` | Passed: zero reported vulnerabilities |
| SPA `npm test` | Passed: 15 tests, including activity history |
| SPA `npm run build` | Passed; existing large-chunk warning remains |
| `uv run ruff check` | Passed |
| `uv run pytest` | 227 passed, 4 skipped; existing Starlette/httpx deprecation warning |
| Compose `config --quiet`, both optional profiles | Passed without printing configured secrets |
| Job and gateway Containerfile targets | Built successfully with pinned Node base and package lock |
| OpenSpec strict validation | Passed |
| Pi `npm run security:audit` | **Failed on existing unchanged dependencies:** 8 high and 4 moderate findings; npm reports no fix available |

The Pi audit findings are in the existing fast-uri/Ajv/MCP and qs/Express dependency chains. No dependency version in that chain was changed by this feature. The existing Pi Containerfile's mandatory security audit is preserved, so its build remains blocked by these findings until an upstream fix or separately reviewed dependency remediation is available. We did not bypass the gate, rebuild/restart production Pi or Webapp, or push the branch remotely.

## Real model acceptance

Model: `custom/deepseek-v4-flash-vision-exp`, through the repository's existing private model configuration. An isolated temporary Pi data directory and initially empty tool library were used with the live local MCP service and real job containers. No business implementation was supplied to the model. The test runner supplied tasks and independently checked outputs on different inputs.

| Case | Independent expected result | Result |
| --- | --- | --- |
| Unanticipated JSON grouping | `{unseen:1.75,other:4}` | Passed |
| ISO instant converted to Asia/Tokyo | `"2021-01-01"` | Passed; current Asia/Shanghai date also executed by model |
| Fahrenheit conversion | `-40` | Passed |
| Intentional execution failure then repair | `["INDEPENDENT","XYZ",""]` | Passed; same server-issued build ID repaired, tested, saved and reused |
| Live public page title extraction | `"Example Domain"` | Passed via real HTTP broker and htmlparser2 |
| Close runtime, reopen library, start new session | Find and call existing grouping tool without execute/publish | Passed |

[Machine-readable evidence](evidence/live-acceptance.json) includes generated source, schemas, tests, immutable hashes, runtime identity, actual independent jobs and activity chains. It excludes private environment configuration and local session paths. Model prose is not treated as execution evidence. These artifacts are acceptance records, not tools preloaded by the application.

An initial acceptance run correctly rejected date and conversion tools whose values were right but whose output shape was wrapped in an unrequested object. Instructions were strengthened to honor the specified output schema; the full six-case rerun passed without relaxing expected outputs. Test passing demonstrates these cases, not universal correctness of future generated programs.

## Coverage and limits

- `runner.test.ts`: strict request/auth boundary, fixed container template, fragmented frames, public/private/mapped IPs, redirect denial, fixed credential profile, actual execution/Date/HTML/temp files, cross-job isolation, output bound, timeout/cancellation, memory exhaustion, child processes, malformed/excessive RPC and stale-owner-only cleanup.
- `broker-transport.test.ts`: DNS answer pinning without a second lookup, mixed private DNS records, decoded gzip size limit and DNS cancellation. The live model page case additionally exercises real DNS/TLS/HTTP from gateway through job RPC.
- `tool-library.test.ts`: durable reload, exact-version source, failed test rejection, concurrent CAS conflict, blob corruption, namespace/schema/secret checks, numeric tolerance, expected failures, cancelled publication, retirement with stable in-flight calls, permission/runtime rechecks and per-turn build/job budgets.
- `runtime-authoring.test.ts`: actual Pi SDK loop with deterministic model transport verifies failed MCP/read/bootstrap results agree between history and SSE, same-turn publication/reuse, prompt loading, source/input suppression and terminating budget behavior. Existing conversation memory tests still pass.
- `tool-activity.test.ts`: failure/repair/success history, duplicate suppression and independent state for a new turn; old API events remain supported. Existing BFF SSE passthrough tests pass.

Authenticated API profiles are verified with a controlled mock, not a paid search provider. No search provider credential is bundled; anonymous search depends on the endpoint, and an unavailable configured capability returns an explicit error. The real public-page case is verified, while real authenticated search remains unverified. Static credential scanning cannot prove absence of all private data. The gateway is privileged infrastructure and containers share the host kernel; this is not a hostile multi-tenant sandbox claim.

For deployment and rollback see [runner README](../../../src/extensions/tool-runner/README.md). Enable the runner profile only after building the job image and configuring a private authentication token. Preserve the independent tool-library volume on rollback.
