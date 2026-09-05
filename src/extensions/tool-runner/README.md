# Isolated tool execution

The Pi Agent writes JavaScript programs and sends authenticated jobs to this gateway. The gateway fixes the job image, resolves its immutable image ID at startup, and creates one disposable container per execution. Programs cannot change the image, mounts, credentials, network or resource configuration.

Build and enable from the repository root:

```sh
docker compose --profile tool-images build tool-job
docker compose --profile tool-authoring build tool-runner pi-agent
```

Generate a random token (`node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))"`) and set these in your private `.env`:

```dotenv
PI_AGENT_RUNNER_TOKEN=<generated-token-at-least-24-characters>
PI_AGENT_RUNNER_URL=http://tool-runner:8020
PI_AGENT_TOOL_AUTHORING_ENABLED=true
PI_AGENT_TIMEZONE=Asia/Shanghai
PI_AGENT_MAX_CODE_JOBS=12
PI_AGENT_MAX_BUILD_ATTEMPTS=3
```

```sh
docker compose --profile tool-authoring up -d tool-runner pi-agent
```

Build the job image first; the gateway intentionally fails startup if it is absent. Restart the gateway after updating that image. Do not run the tool-images profile with `up`; tool-job is only an image build target. The optional tool-authoring profile leaves ordinary TKB startup independent of Docker job execution. Missing runner URL/token produces a clear unavailable state and does not remove TKB tools.

Only the trusted gateway mounts the Docker socket and must be treated as a host administrator. It has no published port, model credentials or product/session/tool-library volumes. Restrict access to its Compose network. Pi owns an independent `toolslibrary` volume; jobs never mount it. A single gateway owns containers labelled `tkb.owner=team-kb-tool-runner`; startup removes only that owner's stale jobs. Do not run competing gateway replicas on the same daemon.

Jobs run as UID/GID 1000, read-only root, no capabilities, no-new-privileges, no network, 256 MiB memory/swap ceiling, one CPU, 32 PIDs and 32 MiB `/work` tmpfs. They inherit only TZ/NODE_ENV. They are forcibly deleted after completion, cancellation or failure. Default maxima: 20 seconds/job, 64 KiB source, 256 KiB input/output and ten network requests. Public network replies are limited to 2 MiB after decompression. A job cannot access the Docker socket, application source, session credentials or another job's files. This is container isolation, not a claim of protection against host kernel vulnerabilities.

## Program and network protocol

```js
export default async (input, host) => {
  const page = await host.fetch({ url: input.url, method: 'GET' });
  return { source: page.url, fetchedAt: page.retrievedAt, text: page.text };
};
```

Use a syntactically valid arrow export such as `export default async (input, host) => { ... };`. Node standard libraries and pinned htmlparser2 are installed. Only `/work` is writable and it disappears with the job. `host.fetch` requires declared `public_http`; only public HTTP(S) GET/HEAD destinations are allowed. Every DNS answer and redirect is checked; the actual connection is pinned to the validated address. Local/private/link-local/mapped private IPs and credentials in URLs are denied. No ambient proxy is used.

For authorized APIs, mount a JSON profiles file read-only **into the gateway only**, set RUNNER_PROFILES_FILE to its absolute path, and inject the named secret environment variable into the gateway. Example configuration (provider origin/path must be replaced with your actual API):

```json
{"search":{"origin":"https://api.example.com","path":"/search","method":"GET","secretEnv":"SEARCH_API_KEY","header":"Authorization","prefix":"Bearer "}}
```

The program declares `search` and calls `host.request({capability:'search',query:{q:input.query}})`. The broker fixes the origin/path/method, adds credentials itself, refuses redirects for profiles, and strips reflected configured secrets. Available profile names appear in health; credentials do not. POST requires an explicit administrator profile. Public search availability depends on the selected endpoint; no search provider or API credential is bundled. Disable public access with RUNNER_PUBLIC_HTTP=false.

## Persistence and rollback

Pi stores SQLite version metadata, test evidence and SHA256-addressed source in its library directory. Publication runs 2-8 supplied tests as isolated jobs against that exact source, then commits the active version transactionally using expectedVersion CAS. Failed tests never replace the active version. Calls pin a version, validate input/output, verify the source hash, and recheck current permissions. Retirement disables all versions of that name. The same shared library is discoverable across sessions/restarts. Back up the entire volume while Pi is stopped; preserve the DB, WAL if present and blobs together.

Saved code/tests/descriptions must use synthetic data. Runtime arguments are passed separately. Known credential patterns are rejected; static scanning cannot prove an artifact contains no private information. Share this library only within the deployment's trust boundary. Generated code executes only in job containers, never in the Pi process.

Rollback: set PI_AGENT_TOOL_AUTHORING_ENABLED=false and clear PI_AGENT_RUNNER_URL, recreate Pi, then `docker compose --profile tool-authoring stop tool-runner`. Keep toolslibrary for recovery. This does not modify the existing TKB data or require deleting volumes.

## Verification

```sh
npm ci
npm run check
npm run security
docker build -f Containerfile --target job -t team-kb-tool-job:latest .
npm run test:integration
```

Integration tests need access to the local Docker daemon; Windows Docker Desktop uses the named pipe. Gateway health checks do not start jobs or call a model/provider. Authenticated API: GET /health, POST /jobs with code/input/capabilities/timeoutMs, DELETE /jobs/{jobId}. Client disconnect cancels its job. The token must be at least 24 characters; use TLS when gateway traffic crosses a host/network trust boundary.
