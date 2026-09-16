## Context

See proposal.md — Why. The constraints that shape the approach:

**Extraction.** `ExtractorRegistry.extract()` (`src/engine/components/extractors/registry.py:42`)
is the single production choke point: `graphrag/backend.py:243` (upload/ingest)
and `graphrag/pipeline.py:214` (`process_file`) are its only callers, and
nothing in `src/` constructs an extractor directly. pypdf 6.14.2 decodes
`ToUnicode` CMap destinations with `.decode("utf-16-be", "surrogatepass")`
(`pypdf/_cmap.py:261-264,282-285,297-300,318`), so a malformed map yields an
unpaired surrogate rather than raising. The first failure downstream is
`hashlib.sha256(new_text.encode())`; there are five such encode sites
(`backend.py:248,416`, `pipeline.py:215,371`, `_version_match.py:113,116`),
and more further out (asyncpg inserts, model request bodies).

The upload route classifies failures with `except ValueError`
(`routes_documents.py:173`). `UnicodeEncodeError` subclasses `ValueError`, so
an internal encoding fault is returned as `invalid_file` with the suggestion
"请确认文件未损坏且可正常打开" — the file is blamed. The mirror case is
`routes_documents.py:273`, where a surrogate in edited content becomes a bogus
404, because Python's `json.loads` accepts the escape `"\udbc3"`.

Measured against the two real files that prompted this:

| File | Trailer | Opens? | Fails at |
|---|---|---|---|
| 3DGS paper (13 pp) | well-formed, 908 B stamp appended after `%%EOF` | yes | the surrogate encode |
| Frenet paper (5 pp) | flattened — `startxref 2333460 %%EOF` on one line | no | `PdfReader.__init__` |

Repair isolation (Frenet): raw → FAIL; truncate the appended stamp → still
FAIL; split the trailer line, **keeping the stamp** → OK (5 pages, 1545 chars).
The appended bytes are irrelevant; the flattened trailer is the sole trigger.

**API errors.** `app.py` mounts one `/api` router. Routes return structured
`{code, message, suggestion, retryable}` detail for anticipated failures
(the whole upload path), but an unhandled exception escapes as uvicorn's
default bare `500 Internal Server Error`. The SPA's `responseError`
(`client/src/api/client.ts:265`) already parses both the object and string
`detail` shapes, so a structured body needs no client change.

**Release state.** `git rev-list --count origin/main..origin/develop` = 12,
`origin/develop..origin/main` = 1 (the 0.4.0 bump). `main` is therefore not an
ancestor of `develop`, and the runbook's `git merge --ff-only origin/develop`
will fail at the next release.

## Goals / Non-Goals

**Goals:**

- Real-world PDFs from this source ingest, or fail with an honest reason.
- Extracted text is UTF-8-encodable by construction, not by luck.
- An unexpected API failure is diagnosable after the fact from the response
  alone.
- The branch state stops contradicting the version numbers it reports.

**Non-Goals:**

- Fixing the memory-page 500 that could not be reproduced. Its root cause is
  unknown; this change makes the *next* occurrence diagnosable and does not
  guess at a fix.
- The `reportAllChanges` console error — confirmed to come from a browser
  extension, not this codebase.
- Adding a new PDF library, a vision path, or improving OCR. pypdf stays.
- Changing the response shape of routes that already raise explicit HTTP
  errors.
- Recovering text that a malformed CMap mapped to the *wrong* character
  (observed as mojibake in the Frenet file). Only unpaired surrogates are in
  scope.

## Decisions

### D1 — Sanitize at the extractor registry boundary

Normalize in `ExtractorRegistry.extract()`, with backstops at the encode sites.

- *Alternative: inside `PDFExtractor`.* Rejected — docx, pptx and OCR can
  produce the same class of damage, and the rule would have to be repeated
  per format.
- *Alternative: at each encode site.* Rejected — five today, and any new call
  site silently reintroduces the bug. The hash at `backend.py:248` fails
  first, so patching call sites fixes the symptom in the wrong place.
- *Alternative: at the BFF boundary.* Rejected — the background pipeline path
  never passes through the BFF.

Backstops remain at the five encode sites because the registry is the sole
choke point *today*; the invariant should not depend on that staying true.

### D2 — U+FFFD via an explicit character filter, not a codec error handler

Both obvious codec approaches are wrong, and measurement decided this:

```
input                             'A\udbc3B'
s.encode('utf-8', 'replace')   -> b'A?B'        literal '?', not U+FFFD
encode(surrogatepass).decode(replace) -> 'A���B'  3 replacements, offsets shift
explicit per-character filter  -> 'A�B'        one U+FFFD, length preserved
```

The `surrogatepass` round-trip is the tempting one and it is the worst: a
lone surrogate is three UTF-8 bytes, each of which fails to decode, so one
bad character becomes three and every later offset moves. The spec requires
offsets preserved, so the filter is the only candidate.

### D3 — Repair the trailer as a retry, not as unconditional preprocessing

Try `PdfReader` on the unmodified bytes first; on `PdfStreamError`, apply
`re.sub(rb"startxref\s+(\d+)\s+%%EOF", rb"startxref\r\n\1\r\n%%EOF", data)`
and retry once. Only if the retry fails is the document reported as an
extraction failure.

- *Alternative: always rewrite bytes before parsing.* Rejected — it touches
  every PDF, including the overwhelming majority that are fine, and makes the
  happy path diverge from a plain `PdfReader` call for no benefit.
- The pattern cannot occur in a well-formed PDF (`startxref`, the offset and
  `%%EOF` are on separate lines by construction), so a match is itself
  evidence of the damage. Bounded risk of matching inside a binary stream is
  accepted because the substitution only runs on a file that has already
  failed to open.

### D4 — Classify by exception type at the route, and keep the honest message

Catch `UnicodeEncodeError` ahead of the existing `except ValueError` in
`routes_documents.py:173` and return a service-side envelope (retryable)
instead of `invalid_file`. With D1 in place this branch should be
unreachable — it is a backstop that keeps a future regression from being
reported as user error. The same treatment applies at `:273`, where an
unpaired surrogate currently produces a 404 for a document that plainly
exists.

### D5 — One global exception handler for `/api`

Register a FastAPI handler for unhandled exceptions on the `/api` router and
emit the same `{code, message, suggestion, retryable}` body the upload path
already uses, with a per-request identifier included in the body and in the
logged traceback. Status stays 5xx; `retryable` is true.

- *Alternative: wrap `routes_memory.py` only.* Rejected — the same gap exists
  on every route; one handler covers all of them for the same effort, and the
  SPA needs no change either way.
- Explicit `HTTPException`s are not intercepted, so no existing contract
  moves.

### D6 — Release repair as an ordered branch sequence, with the spec encoding the invariant

Order matters: merge `main` back into `develop` **first** (restores ancestry
and makes the version order match content order), then land this change on
`develop`, then promote to `main` through the normal release path. The two
infra commits stranded on `develop` (`a74505f9` Containerfile cache-bust +
pipeline fail-closed guard, `999d08e6` backup namespace scoping) ride along
with the next release rather than being cherry-picked, so `main` and `develop`
stay linear and the guard and the thing it guards arrive together.

The lesson is encoded as a `versioning` requirement rather than a checklist
entry, because the failure mode is invisible until the *next* release.

## Risks / Trade-offs

- **The sanitizer could mask a real extraction defect.** → Log the number and
  position of replacements per document when any occur, so silent corruption
  is still visible in the logs.
- **U+FFFD lands in the corpus and is embedded/indexed.** → Accepted; one
  character in 28,170, and the alternative is rejecting a valid document.
- **The global handler could swallow a failure that used to be visible as a
  plain-text 500.** → It logs the traceback plus a request id and preserves a
  5xx status; nothing is hidden that was not already opaque.
- **The memory 500 cause is still unknown.** → Explicitly non-goal. The
  envelope converts the next occurrence from "gone after refresh" into a
  logged traceback and a quotable id.
- **Merging `main` into `develop` may conflict.** → The divergence is one
  version-bump commit; the conflict surface is `VERSION` and
  `pyproject.toml`, and `develop` is expected to win or take the higher
  version.
- **Promoting the infra fixes changes production build behaviour.** →
  Verify after deploy that `/version` reports the released version and the
  deployed SHA, and that the production stack's backup stage still targets the
  production namespace.

## Migration Plan

1. Merge `main` into `develop`; confirm `main` is an ancestor of `develop`
   and that staging reports the higher version.
2. Implement this change on a branch off `develop`; PR into `develop` per the
   usual flow. Staging picks it up on the next poll.
3. Re-ingest the two affected PDFs against staging and confirm both reach
   `indexed`.
4. Release: `develop` → `main`, bump `VERSION` + `pyproject.toml`, tag.
5. Confirm the production instance reports the new version and the matching
   commit.

**Rollback:** revert the change commit on `develop` (staging redeploys), or
use the pipeline's per-stack rollback path for production. No data migration
is involved — the sanitizer affects only new extractions, and already-indexed
documents are untouched.

## Open Questions

- Whether replaced-character counts should be surfaced as a metric or stay in
  the logs. Deferrable; logging first is enough to answer it later.
- Whether the repair regex should also normalize the other trailer damage
  observed from this source (a `目录` comment injected after the PDF header,
  which makes pypdf report "Ignoring wrong pointing object"). Currently
  benign, so deferred.
