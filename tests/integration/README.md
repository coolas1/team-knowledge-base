# Conversation memory integration tests

These tests are marked `integration` and are skipped unless `RUN_INTEGRATION=1`
is set. They require the Compose PostgreSQL, Neo4j, Ollama, Webapp, and Pi Agent
services to be running with conversation memory enabled.

Run the live checks from the repository root:

```bash
RUN_INTEGRATION=1 uv run pytest tests/integration/test_conversation_memory_flow.py
```

The Pi test uses `PI_AGENT_INTEGRATION_URL` when set and otherwise targets
`http://127.0.0.1:8010`. It creates uniquely named sessions and removes their
conversation memory in cleanup. The database test uses unique session/document
identifiers and removes all rows it creates.

Without live services, the regular `uv run pytest` run still covers all unit and
contract behavior and reports these two scenarios as skipped.
