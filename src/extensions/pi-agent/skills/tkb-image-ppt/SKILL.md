---
name: tkb-image-ppt
description: Generate visually unified image-slide presentations with outline and sample review, background progress, and PPTX delivery.
---

Use `tkb_create_ppt` for image-based presentations. Each slide is a complete
generated image: text and shapes are not individually editable. Keep ordinary
editable PPT, DOCX and PDF on `tkb_generate_document`.

1. Read authorized sources with search/get_document. Draft a concise outline,
   one consistent visual style and speaker notes. Do not invent facts or charts.
2. Each page needs a title, 1–6 points, a content-appropriate layout and notes.
   Vary layouts (timeline, comparison, evidence, architecture, cover, conclusion).
   Fill source_document_ids and each page's reference_document_ids using actual
   authorized document UUIDs. Required images must be uploaded image documents;
   do not silently replace a required figure with a text-only approximation.
   Required figures are embedded intact by the server in a fixed central region
   (x=15%, y=20%, width=70%, height=60%; multiple assets split horizontally).
   Keep titles above and points below it. Review shows the exact region mapping.
   The image model generates surrounding content, not copies of required figures.
3. Call tkb_create_ppt with the full spec and return the actual review_url.
   The user reviews the outline, style, backend and source-image mapping there.
   Do not say generation has finished. The background task survives chat timeout.
4. After the outline is approved, only one sample is generated. The user must
   explicitly approve its preview before remaining slides run. tkb_approve_ppt
   returns a review link; it cannot approve for the user.
5. Use tkb_get_ppt for status and tkb_preview_ppt for actual image inspection.
   Avoid repeated polling in one turn. Never claim to have inspected an image
   from its file path alone. Use the preview image content returned by the tool.
6. Failures identify the affected page. Unknown outcomes may already be charged;
   tkb_retry_ppt links to explicit paid retry controls. Cancel via tkb_cancel_ppt
   on the user's request. Never create replacement jobs to evade limits.
7. Only completed tasks expose a real download. Report the actual link and
   remaining limitations. Do not claim an outline is an editable image-slide PPT.

This adapts ningzimu/codex-ppt-skill (MIT), pinned at
f47bd3e54e49d14d51807692694e2d5619a8e298. The server uses explicitly configured
Ark Seedream, persistent serial page workers instead of unavailable Pi subagents,
database state instead of CLI dispatch, and revision-bound UI approvals. It
preserves source assets, sample style, per-page QA and speaker-note assembly.
No runtime skill installation, backend substitution or local screenshot fallback.
