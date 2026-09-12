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
| Per-slide subagents | Persistent application workers, initially serial; Pi has no subagent dispatcher |
| JSON dispatch state files | Transactional job/page/event records with revision, leases and budgets; export compatible assembly files |
| Chat-based approval | Explicit UI approval bound to revision for outline/style/backend and sample |
| Local paths as assets | Authorized document assets with checked content identity |

Preserve source reading, per-page context and layout variation, unified visual
style, required-image transmission, sample approval, page QA, speaker notes and
full-image PPTX assembly. Never use local text screenshots as generated slides.
The source skill's default provider scripts are retained for provenance only and
must not be invoked by the application.

Verified 2026-09-12: Agent Plan alias `doubao-seedream-5.0-lite`, JSON
`/images/generations`, `image` data URI reference, `b64_json`, 2560×1440,
single-image generation. Both synthetic text-to-image and reference-image tests
succeeded, returning 14400 output tokens each. Exact dated model version and
subscription deduction were not supplied; they remain unknown. The reference
test retained Chinese text, numeric values and the bridge icon while adding the
requested orange circle. These two probes do not constitute full PPT acceptance.
