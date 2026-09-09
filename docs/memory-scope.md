# Memory scope operation

The default scope remains the shared `default-team` bank. Requests without a
scope credential use it. New private data should use explicitly provisioned
banks; tagging data inside the shared bank does not make it private from callers
that already have unrestricted access to that bank.

## Trusted HTTP bindings

`MEMORY_SCOPE_BINDINGS` is a server environment JSON mapping from the SHA-256
hex digest of a high-entropy credential to its scope definition. Clients send
the original credential in `X-TKB-Scope-Token`, over the deployment's protected
transport. Store credentials with the calling host, not in prompts or tool
arguments. Example definition (replace the placeholder digest):

```json
{
  "<sha256-hex-digest>": {
    "bank_id": "project-a",
    "visibility": {"tags": ["user:alice"], "match": "all_strict"},
    "write_tags": ["user:alice"],
    "subject_id": "alice",
    "observation_scopes": [["user:alice", "project:a"]]
  }
}
```

Provision the bank in `memory_banks` through a trusted database administration
connection before configuring traffic to it. Bank administration APIs/UI are
part of B7. The definition must include write tags permitted by its visibility
expression. Credentials are only honored when `engine.memory.enabled` and
`engine.memory.features.scope` are both true in the app configuration. All new
batch flags default off. Later batch flags validate their preceding dependencies
but must not be enabled before those implementations and acceptance records exist.

BFF dependencies and MCP tool dispatch resolve the credential for each HTTP
request. A reused MCP session does not retain the previous request's authority.
Unknown credentials are rejected; `bank_id` or tags in ordinary request/tool
arguments cannot grant access. Stdio and existing callers keep default-team.

BFF validates and forwards only X-TKB-Scope-Token to Pi. Configure the same
MEMORY_SCOPE_BINDINGS JSON on Pi and BFF; unknown credentials fail closed even
when Pi is accessed directly. Pi partitions session/transcript/tool-library
storage under PI_AGENT_DATA_DIR/scopes/<canonical-binding-sha256>. Default callers
continue using the original directories. Each session has its own MCP client
and memory injection extension, with the credential carried only in HTTP headers.
Session IDs from other namespaces cannot open streams, read history or mutate data.
Ownership markers survive normal history deletion so explicit forgetting remains
available to its original scope after a restart.

Credential rotation to an identical binding preserves history; changing a binding
creates a new namespace and requires an explicit migration to recover old history.
Restart Pi after changing/revoking credentials. Configuration is immutable for
running sessions. Credentials are never stored in transcript or ownership files.
BFF still requires engine.memory.features.scope before accepting scoped traffic.

Generated downloads also preserve scope in their artifact manifest; old manifests
without ownership remain default-team. Scoped downloads require the credential.

## Ownership and background work

Documents are authoritative for bank and visibility tags. Chunks and memory
`scope_tags` inherit them. Memory `tags` additionally retain source classifications
such as file type and session; these do not change the document's access scope.
This separation preserves exact scope matching when retention adds source tags.

Conversation IDs include bank while preserving the legacy UUID calculation for
default-team. Workers recover bank and source tags from durable queue records.
Graph jobs also restore their stored bank. Neither path mutates a shared service
instance to carry an individual request's scope.

## Migration and graph compatibility

Startup performs the additive PostgreSQL migration. It uses an advisory lock,
bounded backfill batches, default-team for legacy data, resumable progress and
scope constraints. The migration helper accepts `schema`, `batch_size` and
`max_batches` for isolated verification and controlled interruption.

GraphRAG writes entity/relationship descriptions per source document, then merges
permitted evidence when reading. Old JSON graph projections receive native source
IDs without changing their description or inventing attribution. An old merged
description is readable only while **all** its source documents are permitted and
present. Editing/removing one source makes that legacy aggregate stale. Reindex
the remaining source documents to reconstruct descriptions from their own text.
This is intentional conservative handling of provenance missing in old data.

Hindsight's graph migration replaces the global normalized-entity-name constraint
with a `(bank_id, normalized_name)` constraint. PostgreSQL stays authoritative;
graph reads also consult document visibility, so a stale graph cannot expose a
deleted document while projection cleanup catches up.

## Compatible rollback

Disable the scope entry flag and later batch flags, retaining the scope-aware
code and all new tables/columns. Scoped credentials then fail, old unscoped
requests still see only default-team, and scoped data/queued work remain stored.
Do not run pre-scope workers or roll back to the original `f33759a2` reader once
isolated data exists. No reverse migration that drops ownership is supplied.

Deploy through the existing CI/CD pipeline. The implementation and test record do
not constitute a deployment. See the change's `b1-progress.md` for verified paths
and the remaining B2–B7 work.

## Reliable delivery (B2 implementation; not enabled by default)

Set TKB_CONVERSATION_MEMORY_RELIABLE_DELIVERY=true only after B2 acceptance and
with conversation memory enabled and engine.memory.features.reliable_retention=true
in the BFF configuration (its scope dependency must also be enabled). Completed turns then carry durable pending
intent, and retries require explicit engine acknowledgement with a matching
content fingerprint. Pending delivery is separate from pending engine processing.
Pi health exposes memoryDelivery counts (pending/accepted/failed/conflict/cancelled).
The scanner processes at most 20 turns per pass, polls every 5 seconds, uses a
10-second request timeout and stops after 10 failed attempts with capped backoff.

For private scopes, configure PI_AGENT_MEMORY_DELIVERY_TOKENS as a JSON array of
already provisioned scope credentials in Pi's protected environment. One credential
must cover each distinct non-default binding in MEMORY_SCOPE_BINDINGS. Pi verifies
coverage before listening, allowing background recovery without waiting for a user
request. These values never go into transcript, model prompt, ownership manifest or
logs. Rotate them together with the digest map and restart Pi. No default secrets
are generated or installed by this change.

Ordinary history deletion archives only undelivered completed turns under the
namespace's transcripts/.delivery; those records are removed after acknowledgement.
Explicit forgetting cancels pending local records before calling the engine. B3
adds the engine tombstone protocol for in-flight deletion races. Existing
completed turns without intent are not automatically resent; historical replay
requires the later explicit backfill workflow.

## Entity identity and correction (B2)

`engine.memory.features.entity_resolution` is off by default and requires
`reliable_retention` and `scope`. Enable only after B2 acceptance. When enabled,
retain resolves names and source-supported aliases using visible fact context.
Unknown identities remain separate; failed resolution is reported as degraded.
The entity stage has a shared 15-second default deadline and ten candidate limit,
configurable through `HindsightOptions` for service construction.

Entity migrations preserve existing IDs and add `identity_key`, original mention
names, source aliases and correction records. Neo4j must run `ensure_schema` from
this version to remove the old bank/name uniqueness rule before projection.
For rollback, disable the flag while retaining this schema-compatible runtime;
older name-only writers are incompatible with the new unique constraint.

The scoped `HindsightService.correct_entity(source_entity_id, memory_ids,
target_entity_id=..., reason=...)` moves explicit mentions to a visible target.
Omitting the target splits a new identity. It records the correction, preserves
original names, rebuilds affected entity edges and queues graph projection atomically.
User-facing management endpoints and correction history browsing arrive in B7.

## Retention revisions and request replay

`HindsightService.retain` accepts optional `request_id` and `expected_revision`.
Read the current scoped retention revision with `retention_revision(document_id)`.
Concurrent plans cannot overwrite a newer published revision; entity corrections
also advance this revision. It tracks memory publication, not ordinary file edits.

An accepted request key replays its original result without another extraction or
revision increment. Reusing the key with changed input raises a request conflict.
The request record, memory writes, revision and graph outbox commit together.
Degraded results remain degraded on replay; use explicit reprocessing for recovery.
Requests that fail before publication leave no accepted ledger entry.

`retain(..., update_mode="append", request_id="...")` adds only the supplied
content to the retained document; `update_mode="replace"` remains the default.
Append requires a request key. Read `retention_revision(document_id)` when callers
need an explicit compare-and-swap. After a revision conflict, fetch the new revision
and retry the unaccepted request. A previously accepted key always returns its
original result; it does not append again or refresh degraded extraction.

Content snapshots preserve chunk boundaries, source time, timezone and speakers.
Append does not re-anchor old facts to the new source time. Replace reuses complete
unchanged blocks and extracts changed gaps. Indistinguishable repeated blocks are
matched in saved order; retained block/fact IDs survive moves, while newly added
occurrences receive distinct identities. Removed source IDs invalidate dependent
observations and queue graph updates.

Successful and empty extraction responses are cached by content, source context
and policy/schema version; degraded responses are retried. `force_extraction=True`
bypasses extraction cache. `reprocess_document(document_id, stage="extract")`
reuses retained snapshots and original per-chunk provenance rather than newer
ordinary file text. Conversation jobs use their durable queue's stage retry path.

Older rows without snapshots recover saved source blocks and semantic ID mappings
on their first upgraded write. Missing historical time/speaker context stays
unknown. A legacy extraction without validated cache may require one fresh
extraction; matching facts keep their old UUIDs. These operations publish snapshot,
memory, revision, request ledger and graph outbox together. They do not rewrite
the ordinary file Document.raw_text; source expansion must respect the retained
snapshot and document revision instead of assuming file text is identical.

Apply additive migrations before using append or correction. Rollback should
disable the new entry points/worker features while retaining a scope- and
snapshot-compatible runtime and all durable intent, request and source records.
Do not restore an older writer that unconditionally deletes all document memories
or uses entity names as unique identities. Deployment remains pipeline-managed.

## Continuous consolidation (B3; disabled by default)

Enable `engine.memory.features.consolidation` only together with its B1/B2
dependencies. This switches retain from same-call observation generation to an
atomic fact outbox. `engine.memory.consolidation_worker` controls consumption;
turning it off leaves facts durably queued and is the supported rollback. Do not
run the old synchronous consolidation path for a scope whose worker is enabled.

The worker coalesces jobs by bank and configured observation write scope. It reads
versioned current facts and observations, validates every model action against that
read set, then publishes create/update/delete actions under a scope lock and lease
fence. Exact normalization always runs. Optional semantic merging requires both the
configured similarity threshold and an explicit equivalence verdict; contradictions
are stored as conflict changes rather than similarity-only merges.

Each observation head has immutable history snapshots and versioned evidence edges.
Source replacement or deletion writes a fact tombstone and marks dependent heads
stale in the source transaction. A worker with an older watermark loses its lease;
remaining sources are recomputed, and heads with no valid evidence are tombstoned.
Recall continues to exclude non-active memory rows before graph cleanup completes.

The batch size, observation capacity, iterations, token/cost ceiling, worker
concurrency, and semantic threshold are bounded by `engine.memory.consolidation_*`.
Token counts come from the provider response. A nonzero cost ceiling also requires
the input or output USD-per-million-token price so the worker can enforce it.
Capacity or budget exhaustion remains visible on the durable scope job. Migration
backfills legacy observations as version 1, reconstructs their evidence edges, and
queues old atomic facts at a resumable cursor. Rollback must retain observation,
history, evidence, job, event and tombstone tables so forgotten data cannot return.
