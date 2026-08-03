import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { pathToFileURL } from "node:url";
import { loadPiAgentConfig } from "./config.js";
import {
  type AgentRuntimeApi,
  PiAgentRuntime,
  RuntimeConflictError,
  RuntimeLimitError,
  RuntimeNotFoundError,
} from "./runtime.js";

function json(response: ServerResponse, status: number, body: unknown): void {
  const data = JSON.stringify(body);
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(data),
  });
  response.end(data);
}

async function readJson(
  request: IncomingMessage,
  maxBytes: number,
): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > maxBytes) throw new HttpError(413, "request body is too large");
    chunks.push(buffer);
  }
  if (chunks.length === 0) return {};
  try {
    const value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("JSON body must be an object");
    }
    return value as Record<string, unknown>;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new HttpError(400, `invalid JSON body: ${message}`);
  }
}

class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

function statusFor(error: unknown): number {
  if (error instanceof HttpError) return error.status;
  if (error instanceof RuntimeNotFoundError) return 404;
  if (error instanceof RuntimeConflictError) return 409;
  return 500;
}

function errorBody(error: unknown): { error: string; code?: string } {
  if (error instanceof RuntimeLimitError) {
    return { error: error.message, code: error.limit };
  }
  return { error: error instanceof Error ? error.message : String(error) };
}

function sendSse(response: ServerResponse, event: unknown): void {
  const type =
    event && typeof event === "object" && "type" in event
      ? String((event as { type: unknown }).type)
      : "message";
  response.write(`event: ${type}\ndata: ${JSON.stringify(event)}\n\n`);
}

export function createPiAgentHttpServer(
  runtime: AgentRuntimeApi,
  options: { maxRequestBytes?: number } = {},
): Server {
  const maxRequestBytes = options.maxRequestBytes ?? 1_048_576;
  return createServer(async (request, response) => {
    const method = request.method ?? "GET";
    const url = new URL(request.url ?? "/", "http://localhost");
    const parts = url.pathname.split("/").filter(Boolean);
    try {
      if (method === "GET" && url.pathname === "/health") {
        const health = await runtime.health();
        json(response, health.status === "ok" ? 200 : 503, health);
        return;
      }
      if (parts[0] !== "v1" || parts[1] !== "sessions") {
        throw new HttpError(404, "route not found");
      }
      if (method === "POST" && parts.length === 2) {
        json(response, 201, await runtime.createSession());
        return;
      }
      if (method === "GET" && parts.length === 2) {
        json(response, 200, { items: await runtime.listSessions() });
        return;
      }
      const sessionId = parts[2];
      if (!sessionId) throw new HttpError(404, "session id is required");
      if (method === "GET" && parts.length === 3) {
        json(response, 200, await runtime.getSession(sessionId));
        return;
      }
      if (method === "DELETE" && parts.length === 3) {
        const deleted = await runtime.deleteSession(sessionId);
        json(response, deleted ? 200 : 404, { deleted, sessionId });
        return;
      }
      if (method === "POST" && parts[3] === "cancel" && parts.length === 4) {
        json(response, 200, { cancelled: await runtime.cancel(sessionId), sessionId });
        return;
      }
      if (method === "POST" && parts[3] === "messages" && parts.length === 4) {
        const body = await readJson(request, maxRequestBytes);
        if (typeof body.message !== "string" || body.message.trim() === "") {
          throw new HttpError(400, "message must be a non-empty string");
        }
        response.writeHead(200, {
          "content-type": "text/event-stream; charset=utf-8",
          "cache-control": "no-cache, no-transform",
          connection: "keep-alive",
          "x-accel-buffering": "no",
        });
        response.flushHeaders();
        response.on("close", () => {
          if (!response.writableEnded) void runtime.cancel(sessionId);
        });
        try {
          await runtime.streamMessage(sessionId, body.message, (event) =>
            sendSse(response, event),
          );
        } catch (error) {
          sendSse(response, { type: "message.failed", ...errorBody(error) });
        } finally {
          response.end();
        }
        return;
      }
      throw new HttpError(404, "route not found");
    } catch (error) {
      if (response.headersSent) {
        sendSse(response, { type: "message.failed", ...errorBody(error) });
        response.end();
        return;
      }
      json(response, statusFor(error), errorBody(error));
    }
  });
}

export async function startPiAgentServer(): Promise<{
  runtime: PiAgentRuntime;
  server: Server;
}> {
  const config = loadPiAgentConfig();
  const runtime = new PiAgentRuntime(config);
  await runtime.initialize();
  const server = createPiAgentHttpServer(runtime, {
    maxRequestBytes: config.maxRequestBytes,
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(config.port, config.host, () => {
      server.off("error", reject);
      resolve();
    });
  });
  const shutdown = async () => {
    await runtime.close();
    server.close();
  };
  process.once("SIGTERM", () => void shutdown());
  process.once("SIGINT", () => void shutdown());
  return { runtime, server };
}

const invokedPath = process.argv[1] ? pathToFileURL(process.argv[1]).href : "";
if (import.meta.url === invokedPath) {
  startPiAgentServer()
    .then(({ runtime }) => runtime.health())
    .then((health) => {
      process.stdout.write(
        `TKB Pi Agent listening with ${health.model.provider}/${health.model.id}\n`,
      );
    })
    .catch((error) => {
      process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
      process.exitCode = 1;
    });
}
