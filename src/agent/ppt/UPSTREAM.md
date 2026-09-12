# codex-ppt integration

Vendored from ningzimu/codex-ppt-skill commit
`f47bd3e54e49d14d51807692694e2d5619a8e298` (MIT, copyright ningzimu).
Unmodified source bytes and the license live in `vendor/codex_ppt`.
`manifest.json` records SHA-256 for each file; initial retrieval also checked
Git blob hashes against the pinned GitHub tree. The image build verifies bytes.
No runtime download, bootstrap, shell execution or floating-main installation.

The upstream skill is a reference and assembly library, not an unrestricted
runtime skill installation. The application adapter deliberately changes:

| Upstream | Application contract |
|---|---|
| Built-in/GPT Image/AtlasCloud CLI | Explicit Ark Agent Plan Seedream provider |
| User home .env and dynamic runtime bootstrap | Server-only IMAGE_* and frozen image dependencies |
| Per-slide subagents | One bounded foreground skill executor, initially serial; Pi has no subagent dispatcher |
| JSON dispatch state files | Invocation-local temporary files; only the verified PPTX is published as a normal artifact |
| Chat-based approval | The user's chat request authorizes one bounded generation call; missing inputs are clarified in chat |
| Local paths as assets | Authorized document assets with checked content identity |

Preserve source reading, per-page context and layout variation, unified visual
style, required-image preservation, an internal style sample, page QA, speaker notes and
full-image PPTX assembly. Seedream generates the visual background; the adapter
rasterizes exact title and point text with a packaged CJK font before QA so that
Chinese and numeric fidelity do not depend on image-model typography.
The source skill's default provider scripts are retained for provenance only and
must not be invoked by the application.

Verified 2026-09-12: Agent Plan alias `doubao-seedream-5.0-lite`, JSON
`/images/generations`, `image` data URI reference, `b64_json`, 2560×1440,
single-image generation. Both synthetic text-to-image and reference-image tests
succeeded, returning 14400 output tokens each. Exact dated model version and
subscription deduction were not supplied; they remain unknown. The reference
test retained Chinese text, numeric values and the bridge icon while adding the
requested orange circle. These two probes do not constitute full PPT acceptance.

Approved adaptation 2026-09-12: required figures are composited locally into
versioned, recorded regions with aspect-preserving resize and no crop.
Asset pages use text style and empty-region instructions without image input,
preventing sample-layout copying. Other pages may send a style sample to Seedream;
final-image QA receives original assets and the sample.
Background bytes and embedded pixel/source hashes are retained. The isolated
upstream assembly module disables optional JPEG compression, and the adapter
verifies exact final PNG bytes inside the PPTX. Vendored source stays unchanged.

Chat-native acceptance on 2026-09-12 used one foreground tool invocation for a
three-page synthetic deck: three Seedream calls and three Turbo QA calls, 51,167
reported tokens, no unknown usage. LibreOffice 25.2.3.2 rendered all three pages;
speaker notes and the persisted artifact download were verified. AFP, plan
deduction and currency cost remain unknown.
