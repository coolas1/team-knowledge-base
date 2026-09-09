export const LIMITS = Object.freeze({ code: 65_536, input: 262_144, output: 262_144,
  body: 400_000, timeoutMs: 20_000, networkBytes: 2_097_152, networkCalls: 10 });

export interface JobRequest {
  code: string;
  input: unknown;
  capabilities: string[];
  timeoutMs: number;
}
export interface JobResult {
  jobId: string;
  status: "succeeded" | "failed";
  result?: unknown;
  error?: { code: string; message: string };
  logs: string;
  runtime: string;
}
export class RunnerError extends Error {
  constructor(readonly code: string, message: string, readonly status = 400) { super(message); }
}
export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new RunnerError("invalid_input", "Expected an object");
  return value as Record<string, unknown>;
}
export function boundedJson(value: unknown, max: number): string {
  const json = JSON.stringify(value);
  if (json === undefined || Buffer.byteLength(json) > max) throw new RunnerError("size_limit", "JSON size limit exceeded");
  return json;
}
export function parseJob(value: unknown): JobRequest {
  const data = object(value);
  if (Object.keys(data).some(k => !["code", "input", "capabilities", "timeoutMs"].includes(k))) {
    throw new RunnerError("invalid_input", "Unknown job field");
  }
  if (typeof data.code !== "string" || !data.code.trim() || Buffer.byteLength(data.code) > LIMITS.code) {
    throw new RunnerError("invalid_code", "Code must contain 1–65536 UTF-8 bytes");
  }
  boundedJson(data.input ?? null, LIMITS.input);
  const caps = data.capabilities ?? [];
  if (!Array.isArray(caps) || caps.length > 16 || caps.some(c => typeof c !== "string" || !/^[a-z][a-z0-9_-]{0,63}$/.test(c))) {
    throw new RunnerError("invalid_capabilities", "Invalid capability names");
  }
  const timeout = data.timeoutMs ?? LIMITS.timeoutMs;
  if (!Number.isInteger(timeout) || Number(timeout) < 1 || Number(timeout) > LIMITS.timeoutMs) {
    throw new RunnerError("invalid_timeout", "timeoutMs must be between 1 and 20000");
  }
  return { code: data.code, input: data.input ?? null, capabilities: [...new Set(caps)], timeoutMs: Number(timeout) };
}
export function cleanError(error: unknown, secrets: string[] = []): string {
  let message = error instanceof Error ? error.message : "Execution failed";
  for (const secret of secrets.filter(Boolean)) message = message.split(secret).join("[redacted]");
  return message.replace(/(?:Bearer\s+|(?:api[_-]?key|token|password)\s*[:=]\s*)[^\s,;]+/gi, "[redacted]")
    .replace(/[\r\n]+/g, " ").slice(0, 240);
}
