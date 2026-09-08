/** Process/socket acceptance for delivery. Receiver uses an isolated fsynced ledger,
 * not the engine's PostgreSQL queue (which has separate integration coverage). */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, open, readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { CallToolRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { TranscriptStore, completedTurnDelivery } from "../dist/transcript.js";
import { ConversationDeliveryWorker } from "../dist/delivery.js";
import { TkbMcpClient } from "../dist/mcp-client.js";
import { loadTkbAdapterConfig } from "../dist/config.js";

const [mode, directory, endpoint] = process.argv.slice(2);
const scope = "fault-test-scope";
const session = "fault-session";

async function child() {
  const store = new TranscriptStore(directory);
  if (mode === "seed") {
    await store.initialize(session);
    const { turn } = await store.accept(session, "Question", "request-1");
    await store.append({ type: "assistant.completed", sessionId: session, turnId: turn.id,
      messageId: "answer-1", text: "Answer", timestamp: new Date().toISOString(),
      delivery: completedTurnDelivery(scope, session, turn.id, "Question", "Answer") });
    await store.accept(session, "Unfinished", "request-2");
    process.exit(0);
  }
  const client = new TkbMcpClient(loadTkbAdapterConfig({ TKB_MCP_URL: endpoint }));
  if (mode === "crash") {
    const call = client.callTool.bind(client);
    client.callTool = async (...args) => {
      const result = await call(...args);
      if (result.isError) console.error(result.text);
      return result;
    };
    const enqueue = client.enqueueConversationTurn.bind(client);
    client.enqueueConversationTurn = async (...args) => {
      try { await enqueue(...args); }
      catch (error) { console.error(error.message); throw error; }
      process.exit(86); // Remote ack received; local delivery.result not appended.
    };
  }
  const worker = new ConversationDeliveryWorker(store, client, scope,
    { pollMs: 5000, maxAttempts: 5, batchSize: 20, timeoutMs: 3000 });
  await worker.run();
  console.log(JSON.stringify(await worker.status()));
  await worker.close();
}

function runChild(action, root, url) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [fileURLToPath(import.meta.url), action, root, url],
      { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    child.stdout.on("data", (data) => { stdout += data; });
    child.stderr.on("data", (data) => { stderr += data; });
    const timer = setTimeout(() => { child.kill(); reject(new Error("child timed out")); }, 20000);
    child.once("error", reject);
    child.once("exit", (code) => { clearTimeout(timer); resolve({ code, stdout, stderr }); });
  });
}

async function main() {
  const root = path.resolve(directory);
  await mkdir(root); // Fresh directory required; never reuse previous evidence.
  const transcript = path.join(root, "transcript");
  const ledger = path.join(root, "receiver.jsonl");
  let requests = 0;
  const accepted = new Map();
  const http = createServer(async (request, response) => {
    const server = new Server({ name: "delivery-fault-receiver", version: "1" }, { capabilities: { tools: {} } });
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
    server.setRequestHandler(CallToolRequestSchema, async ({ params }) => {
      assert.equal(params.name, "enqueue_conversation_turn");
      const args = params.arguments;
      const intent = completedTurnDelivery(scope, args.session_id, args.turn_id, args.user_text, args.assistant_text);
      requests++;
      const prior = accepted.get(intent.key);
      if (prior) assert.equal(prior.contentHash, intent.contentHash);
      else {
        const file = await open(ledger, "a");
        try { await file.writeFile(JSON.stringify(intent) + "\n"); await file.sync(); }
        finally { await file.close(); }
        accepted.set(intent.key, intent);
      }
      return { content: [{ type: "text", text: JSON.stringify({ document_id: "isolated-document",
        operation_id: "isolated-operation", status: "pending", durable_acceptance: true,
        content_hash: intent.contentHash }) }] };
    });
    response.on("close", () => { void transport.close(); void server.close(); });
    try { await server.connect(transport); await transport.handleRequest(request, response); }
    catch { if (!response.headersSent) response.writeHead(500); response.end(); }
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${http.address().port}/mcp`;
  const port = http.address().port;
  try {
    assert.equal((await runChild("seed", transcript, url)).code, 0);
    await new Promise((resolve) => http.close(resolve));
    const offline = await runChild("recover", transcript, url);
    assert.equal(offline.code, 0, offline.stderr);
    assert.equal(JSON.parse(offline.stdout).pending, 1);
    assert.equal(requests, 0);
    const pending = await new TranscriptStore(transcript).snapshot(session);
    assert.equal(pending.turns[0].deliveryResult.status, "pending");
    await new Promise((resolve) => http.listen(port, "127.0.0.1", resolve));
    const delay = Date.parse(pending.turns[0].deliveryResult.nextAttemptAt) - Date.now();
    if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay + 10));
    const crash = await runChild("crash", transcript, url);
    assert.equal(crash.code, 86, crash.stderr);
    const before = await new TranscriptStore(transcript).snapshot(session);
    assert.equal(before.turns[0].deliveryResult.status, "pending");
    assert.equal(accepted.size, 1);
    const recovery = await runChild("recover", transcript, url);
    assert.equal(recovery.code, 0, recovery.stderr);
    assert.equal(JSON.parse(recovery.stdout).accepted, 1);
    assert.equal(requests, 2);
    assert.equal(accepted.size, 1);
    assert.equal((await readFile(ledger, "utf8")).trim().split("\n").length, 1);
    assert.equal((await runChild("recover", transcript, url)).code, 0);
    assert.equal(requests, 2); // Accepted turn and unfinished turn stay out.
    const result = { status: "passed", scenarios: ["network_unavailable_then_process_restart", "process_exit_after_remote_ack"], requests,
      unique_remote_records: accepted.size, process_exit_code: crash.code,
      transport: "real MCP Streamable HTTP", receiver: "isolated fsynced test ledger",
      completed_at: new Date().toISOString() };
    await writeFile(path.join(root, "result.json"), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
  } finally { http.closeAllConnections(); await new Promise((resolve) => http.close(resolve)); }
}

if (mode === "run") await main();
else await child();
