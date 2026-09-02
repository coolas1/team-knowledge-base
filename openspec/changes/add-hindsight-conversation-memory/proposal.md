## Why

The knowledge base currently retains indexed files in the embedded Hindsight memory engine, while Pi Agent conversations are stored only as session history and are never recalled across sessions. Adding automatic conversation retention and retrieval lets the assistant reuse durable facts and preferences learned in earlier conversations without requiring the model or user to invoke memory tools manually.

## What Changes

- Add automatic conversation-memory recall before each Pi Agent response and inject relevant memories as bounded, non-persistent instructions.
- Retain completed user/assistant turns after successful responses through a dedicated conversation-memory write path.
- Represent conversations as first-class memory sources with stable session and turn identifiers instead of treating them as uploaded files.
- Add internal transport contracts for conversation recall, retention, status, and explicit forgetting while keeping the existing public message-streaming API compatible.
- Make memory failures observable but non-blocking so recall or retention outages do not prevent normal conversation.
- Use one shared team memory scope for this change; user identity, per-user banks, and cross-tenant isolation are explicitly out of scope.
- Deliver the implementation in independently testable batches and record one focused commit after each completed batch.

## Capabilities

### New Capabilities

- `conversation-memory`: Automatic retention, retrieval, provenance, failure handling, and forgetting behavior for Pi Agent conversations.

### Modified Capabilities

None.

## Impact

- Pi Agent runtime lifecycle and configuration under `src/extensions/pi-agent/`.
- Engine Hindsight service, repository, persistence models, and MCP transport under `src/engine/`.
- BFF session deletion behavior and optional memory-status exposure under `src/frontend/webapp/server/`.
- PostgreSQL schema initialization/migration behavior for conversation memory sources.
- Unit, contract, and integration tests across engine, agent runtime, MCP, and BFF boundaries.
- Existing file ingestion and knowledge-query contracts remain backward compatible.
