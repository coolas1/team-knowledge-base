## 1. Release-train resync (prerequisite)

- [x] 1.1 Merge `origin/main` into `develop`, resolving `VERSION` /
  `pyproject.toml` in favour of the higher version, and push to `origin`.
  Verify: `git merge-base --is-ancestor origin/main origin/develop` exits 0
  and `git rev-list --count origin/develop..origin/main` prints `0`.
- [x] 1.2 Confirm the version numbers now agree with content order. Verify:
  `curl -s http://localhost:8001/version` reports the same version as
  `curl -s http://localhost:8000/version` (currently 0.3.1 vs 0.4.0), and
  `git rev-list --count origin/main..origin/develop` is non-zero.

## 2. Surrogate-safe extraction

- [x] 2.1 Add the normalization helper and apply it in
  `ExtractorRegistry.extract()` (`src/engine/components/extractors/registry.py`)
  so every format is covered at the one production choke point.
  Verify: a unit test feeding `"A\udbc3B"` through `registry.extract` returns
  `"A�B"` — exactly one U+FFFD, `len()` unchanged.
- [x] 2.2 Apply the same helper at the existing encode sites
  (`graphrag/backend.py:248,416`, `graphrag/pipeline.py:215,371`,
  `graphrag/_version_match.py:113,116`) so the invariant does not depend on
  the registry remaining the only entry point.
  Verify: a test exercises each site with surrogate-bearing text and none
  raises `UnicodeEncodeError`.
- [x] 2.3 Log the count and position of any replacements per document.
  Verify: ingesting the surrogate fixture emits a log record naming the
  document and the replacement count.
- [x] 2.4 Add a minimal PDF fixture whose `ToUnicode` map points a code at an
  unpaired surrogate (generator pattern already proven at
  `/tmp/mk_surrogate_pdf.py`) under `tests/`.
  Verify: the fixture is < 2 KB and a test asserts its extraction yields
  U+FFFD and that the ingest content hash succeeds.
- [x] 2.5 Confirm the real 3DGS PDF no longer reproduces the reported error.
  Verify: running extraction over
  `基于3D高斯溅射的周视3D占用预测方法.pdf` (repo root) yields 13 pages,
  28,170 characters, zero code points in `0xD800–0xDFFF`, and a successful
  `.encode("utf-8")`.

## 3. Malformed-trailer recovery

- [x] 3.1 In `src/engine/components/extractors/pdf.py`, attempt `PdfReader` on
  the unmodified bytes first and, on `PdfStreamError`, retry once with
  `re.sub(rb"startxref\s+(\d+)\s+%%EOF", rb"startxref\r\n\1\r\n%%EOF", data)`.
  Verify: a test asserts the retry path is taken only after the first attempt
  raises.
- [x] 3.2 Add a flattened-trailer fixture under `tests/`.
  Verify: the fixture extracts successfully through `PDFExtractor`, and the
  same bytes with the trailer un-flattened extract to identical text.
- [x] 3.3 Confirm the repair does not mask genuinely broken input.
  Verify: a truncated-PDF fixture still raises the extraction failure with the
  underlying reason, and no document row is created.
- [x] 3.4 Confirm well-formed PDFs keep today's path.
  Verify: `uv run pytest tests/engine/test_extractors.py` passes unchanged.
- [x] 3.5 Confirm the real Frenet PDF extracts.
  Verify: extraction over
  `基于Frenet坐标系的车辆自动驾驶轨迹规划算法研究.pdf` (repo root) yields
  5 pages and 6,301 characters.

## 4. Honest failure classification

- [x] 4.1 Catch `UnicodeEncodeError` ahead of the existing `except ValueError`
  in `src/frontend/webapp/server/routes_documents.py:173` and return a
  service-side envelope instead of `invalid_file`.
  Verify: a BFF test simulating an encoding failure asserts the response
  `code` is not `invalid_file` and the suggestion does not claim the file is
  corrupt.
- [x] 4.2 Apply the same treatment at `routes_documents.py:273` for edited
  content.
  Verify: a test `PUT`s `{"content": "\udbc3"}` to an existing markdown
  document and gets neither 404 nor an "unsupported format" message.

## 5. Structured API error envelope

- [x] 5.1 Register a FastAPI handler for unhandled exceptions on the `/api`
  router in `src/frontend/webapp/server/app.py`, returning
  `{code, message, suggestion, retryable}` with `retryable` true and a 5xx
  status.
  Verify: a BFF test against a route forced to raise returns that body shape
  rather than a bare server-error response.
- [x] 5.2 Include a per-request identifier in the response body and in the
  logged traceback.
  Verify: a test asserts the identifier in the response body also appears in
  the captured log output for the same failure.
- [x] 5.3 Confirm explicit HTTP errors are untouched.
  Verify: the existing BFF suite passes with no change to any expected
  `detail` shape (`uv run pytest tests/frontend`).
- [x] 5.4 Confirm no client change is needed.
  Verify: `cd src/frontend/webapp/client && npm test` passes with the
  `responseError` tests covering both the object and string `detail` shapes.

## 6. Live verification on staging

- [x] 6.1 Confirm memory routes are unaffected by the new handler.
  Verify: `curl -s -o /dev/null -w '%{http_code}'` on each of
  `/api/memory/{operations,facts,models,directives,policy}` returns 200 on
  `:8001`.
- [x] 6.2 Confirm both affected PDFs ingest end to end against staging.
  Verify: uploading each returns a document ref, and
  `GET /api/documents/{id}` reaches `indexed` with non-empty extracted text.
- [x] 6.3 Confirm no encoding or extraction errors remain in the staging log.
  Verify: `podman logs team-kb-dev-webapp` shows no `UnicodeEncodeError` and no
  `PdfStreamError` since the deploy.

## 7. Validation and PR

- [x] 7.1 `uv run ruff check` passes with no new findings.
- [x] 7.2 `uv run pytest` passes (unit + contract + BFF).
- [x] 7.3 `cd src/frontend/webapp/client && npm test` passes.
- [x] 7.4 Push the branch and open a PR into `develop` using
  `.github/PULL_REQUEST_TEMPLATE.md`, carrying this change's spec delta.

## 8. Post-merge release (maintainer)

- [ ] 8.1 Merge `develop` into `main` with `--ff-only`, bump `VERSION` and
  `pyproject.toml` together, and tag.
  Verify: the merge succeeds without needing a manual conflict resolution —
  the direct evidence that task 1.1 restored ancestry.
- [ ] 8.2 Confirm production reports the released version and the deployed
  commit.
  Verify: `curl -s http://localhost:8000/version` reports the new version and
  the SHA the pipeline logged, and the pipeline log contains no
  "stale build-cache layer" failure.
- [ ] 8.3 Confirm production's pre-deploy backup still targets the production
  namespace after the infra fixes land.
  Verify: the pipeline log's backup stage names `team-kb-postgres` /
  `team-kb_uploadsdata`, and `.deploy/main/backups/<sha>-*/` contains a fresh
  `postgres.sql.gz`.
