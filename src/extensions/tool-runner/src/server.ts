import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { Jobs } from "./jobs.js";
import { Broker, type CapabilityProfile } from "./broker.js";
import { LIMITS, RunnerError, cleanError, parseJob } from "./contract.js";

export function createRunnerServer(jobs: Jobs, token: string) {
  if (token.length < 24) throw new Error("RUNNER_TOKEN must contain at least 24 characters");
  return createServer(async (request, response) => {
    const send = (status: number, value: unknown) => { response.writeHead(status, { "content-type": "application/json" }); response.end(JSON.stringify(value)); };
    try {
      const supplied = Buffer.from(request.headers.authorization ?? ""); const expected = Buffer.from(`Bearer ${token}`);
      if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) { send(401, { error: "Unauthorized" }); return; }
      if (request.method === "GET" && request.url === "/health") { send(200, await jobs.health()); return; }
      if (request.method === "DELETE" && /^\/jobs\/[a-f0-9-]{36}$/.test(request.url ?? "")) { send(200, { cancelled: jobs.cancel(request.url!.slice(6)) }); return; }
      if (request.method !== "POST" || request.url !== "/jobs") { send(404, { error: "Not found" }); return; }
      let size = 0; const chunks: Buffer[] = [];
      for await (const chunk of request) { size += chunk.length; if (size > LIMITS.body) throw new RunnerError("size_limit", "Request too large", 413); chunks.push(chunk); }
      const job = parseJob(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      const controller = new AbortController();
      response.on("close", () => { if (!response.writableEnded) controller.abort(); });
      const result = await jobs.run(job, controller.signal);
      if (!response.destroyed) send(200, result);
    } catch (error) { if (!response.destroyed) send(error instanceof RunnerError ? error.status : error instanceof SyntaxError ? 400 : 503, { error: cleanError(error), code: error instanceof RunnerError ? error.code : error instanceof SyntaxError ? "invalid_json" : "runner_unavailable" }); }
  });
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const profiles: Record<string, CapabilityProfile> = process.env.RUNNER_PROFILES_FILE ? JSON.parse(await readFile(process.env.RUNNER_PROFILES_FILE, "utf8")) : {};
  const jobs = new Jobs(undefined, new Broker(process.env.RUNNER_PUBLIC_HTTP !== "false", profiles));
  await jobs.initialize();
  const server = createRunnerServer(jobs, process.env.RUNNER_TOKEN ?? "");
  server.requestTimeout = 30_000;
  server.listen(Number(process.env.RUNNER_PORT ?? 8020), process.env.RUNNER_HOST ?? "127.0.0.1");
  const shutdown = () => { void jobs.close(); server.close(); };
  process.on("SIGTERM", shutdown); process.on("SIGINT", shutdown);
}
