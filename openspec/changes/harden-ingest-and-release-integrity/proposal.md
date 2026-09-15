## Why

Two PDFs downloaded from the same library portal — both of which open fine in
every PDF viewer — are rejected by the ingest path, and the rejection message
blames the file ("请确认文件未损坏且可正常打开") for what is an internal
defect. A third failure surface is invisible for the opposite reason: an
unhandled exception on any API route escapes as a bare `500 Internal Server
Error` with no code, no retry guidance, and nothing to correlate against,
which is why a transient 记忆-page failure could not be diagnosed after the
fact.

Separately, the release train has diverged: `main` carries a version bump
`develop` never received, so the staging stack reports a *lower* version
(0.3.1) than production (0.4.0) while carrying strictly newer content — and
because `main` is no longer an ancestor of `develop`, the next release's
`merge --ff-only` will fail. Two infra fixes already on `develop` are missing
from production as a result.

## What Changes

**Extraction robustness.** PDFs are accepted or rejected on the strength of
their actual text, not on artifacts the source portal left behind.

- Lone surrogates that pypdf's CMap decoder produces (`errors="surrogatepass"`
  on a malformed `ToUnicode` map) are replaced with `U+FFFD` at the extractor
  registry boundary, so every format the engine ingests is covered by one
  rule and extracted text is always UTF-8 encodable.
- A PDF whose trailer has been flattened to `startxref <offset> %%EOF` on a
  single line — which defeats pypdf's backwards end-of-file scan and fails at
  `PdfReader` construction — is repaired and retried rather than reported as
  corrupt.
- An internal encoding failure is never again reported to the user as a
  damaged file. `UnicodeEncodeError` is a `ValueError`, and the upload route's
  `except ValueError` currently converts it into `invalid_file` with a "file
  may be corrupt" suggestion.

**Legible API failures.** Every `/api` route gains one structured error
envelope for unhandled exceptions, matching the shape the upload path already
returns (`code` / `message` / `suggestion` / `retryable`), with a request id
logged alongside the traceback. Explicit `HTTPException`s keep their current
detail, so no existing client contract changes.

**Release integrity.** The release bump is propagated back to the integration
branch so version numbers never contradict content order, the two infra fixes
stranded on `develop` reach `main`, and the release workflow records the
merge-back as a required step rather than tribal knowledge.

**Explicitly out of scope:** the `reportAllChanges` TypeError seen in the 记忆
tab console was confirmed to originate from a browser extension — it does not
reproduce in incognito and the symbol exists nowhere in the client source,
the built bundles, or the dependency tree. No code change is warranted.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingest`: extraction must tolerate real-world PDF container damage
  (surrogate-producing CMaps, flattened trailers) and must classify failures
  honestly instead of reporting internal errors as corrupt input files.
- `webapp`: unhandled exceptions on API routes must return a structured,
  actionable error envelope instead of a bare server-error response.
- `versioning`: a release bump on the release branch must propagate to the
  integration branch, so a branch that carries newer content never reports an
  older version.

## Impact

**Engine — extraction path** (`src/engine/components/extractors/`):
`registry.py` (the single choke point both ingest paths call), `pdf.py`
(trailer repair), plus sanitization backstops at the existing encode sites in
`src/engine/graphrag/backend.py:248,416`, `src/engine/graphrag/pipeline.py:215,371`,
and `src/engine/graphrag/_version_match.py:113,116`.

**Frontend — API error surface** (`src/frontend/webapp/server/`): a global
exception handler registered in `app.py`, and narrowed exception handling in
`routes_documents.py:173` (upload misclassification) and `:273` (a surrogate
in edited content currently surfaces as a bogus 404).

**Release state**: `main` and `develop` branch history; the release procedure
documented in `CLAUDE.md`.

**Tests**: new fixtures under `tests/` for both PDF failure modes; BFF tests
for the error envelope and the corrected upload classification.

**Dependencies**: none added. **Breaking changes**: none — the error envelope
replaces only the bare-`500` body, and the SPA's `responseError` already
parses both the structured and string `detail` shapes.
