import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync, renameSync, existsSync } from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { isDeepStrictEqual } from "node:util";
import { Check } from "typebox/value";
import type { TSchema } from "typebox";
import type { CodeResult, CodeRunner } from "./runner-client.js";

export interface ToolTest { input: unknown; expected?: unknown; tolerance?: number; expectError?: string }
export interface Manifest {
  name: string; description: string; inputSchema: Record<string, unknown>; outputSchema: Record<string, unknown>;
  capabilities: string[]; tests: ToolTest[];
}
export interface Artifact extends Manifest { version: number; hash: string; versionHash: string; runtime: string; active: boolean; code: string }
export type RunCode = (code: string, input: unknown, capabilities: string[], signal?: AbortSignal) => Promise<CodeResult>;
const digest = (code: string) => createHash("sha256").update(code).digest("hex");
const keywords = new Set(["type", "description", "properties", "required", "additionalProperties", "items", "enum", "const", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "anyOf"]);
export function validateSchema(schema: unknown, depth = 0): asserts schema is Record<string, unknown> {
  if (!schema || typeof schema !== "object" || Array.isArray(schema) || depth > 8) throw new Error("Invalid or overly deep JSON schema");
  const s = schema as Record<string, unknown>;
  for (const key of Object.keys(s)) if (!keywords.has(key)) throw new Error(`Unsupported schema keyword: ${key}`);
  if (s.type !== undefined && !["object", "array", "string", "number", "integer", "boolean", "null"].includes(String(s.type))) throw new Error("Invalid schema type");
  if (s.description !== undefined && typeof s.description !== "string") throw new Error("Invalid schema description");
  if (s.properties !== undefined) {
    if (!s.properties || typeof s.properties !== "object" || Array.isArray(s.properties)) throw new Error("Invalid properties");
    for (const child of Object.values(s.properties)) validateSchema(child, depth + 1);
  }
  if (s.items !== undefined) validateSchema(s.items, depth + 1);
  if (s.additionalProperties !== undefined && typeof s.additionalProperties !== "boolean") validateSchema(s.additionalProperties, depth + 1);
  if (s.required !== undefined && (!Array.isArray(s.required) || s.required.some(v => typeof v !== "string"))) throw new Error("Invalid required fields");
  if (s.enum !== undefined && (!Array.isArray(s.enum) || !s.enum.length)) throw new Error("Invalid enum");
  if (s.anyOf !== undefined) {
    if (!Array.isArray(s.anyOf) || !s.anyOf.length) throw new Error("Invalid anyOf");
    for (const child of s.anyOf) validateSchema(child, depth + 1);
  }
  for (const key of ["minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"]) {
    if (s[key] !== undefined && (typeof s[key] !== "number" || !Number.isFinite(s[key]) || (key !== "minimum" && key !== "maximum" && (!Number.isInteger(s[key]) || (s[key] as number) < 0)))) throw new Error(`Invalid ${key}`);
  }
}
export function checkValue(schema: Record<string, unknown>, value: unknown): void {
  if (Buffer.byteLength(JSON.stringify(value) ?? "") > 262144 || !Check(schema as TSchema, value)) throw new Error("schema_validation: value does not match tool schema");
}
export function validateManifest(value: Manifest, code: string): void {
  if (typeof code !== "string" || !code.trim() || Buffer.byteLength(code) > 65536) throw new Error("Invalid code size");
  if (!/^gen_[a-z][a-z0-9_]{2,59}$/.test(value.name)) throw new Error("Generated tools require a gen_ name");
  if (typeof value.description !== "string" || !value.description.trim() || value.description.length > 1000) throw new Error("Invalid description");
  if (Buffer.byteLength(JSON.stringify(value)) > 262144) throw new Error("Manifest too large");
  validateSchema(value.inputSchema); validateSchema(value.outputSchema);
  if (value.inputSchema.type !== "object") throw new Error("Tool input must be a parameterized object schema");
  if (!Array.isArray(value.capabilities) || value.capabilities.length > 16 || value.capabilities.some(c => typeof c !== "string" || !/^[a-z][a-z0-9_.-]{0,63}$/.test(c))) throw new Error("Invalid capabilities");
  if (!Array.isArray(value.tests) || value.tests.length < 2 || value.tests.length > 8) throw new Error("Supply 2-8 tests including a success and a distinct boundary/failure");
  if (!value.tests.some(t => "expected" in t && !t.expectError) || new Set(value.tests.map(t => JSON.stringify(t.input))).size < 2) throw new Error("Tests need success and distinct boundary inputs");
  for (const test of value.tests) {
    if (("expected" in test) === (typeof test.expectError === "string")) throw new Error("Each test needs expected JSON or expectError code");
    if (test.tolerance !== undefined && (typeof test.expected !== "number" || !Number.isFinite(test.tolerance) || test.tolerance < 0)) throw new Error("Invalid numeric tolerance");
    if ("expected" in test) checkValue(value.outputSchema, test.expected);
  }
  const shared = JSON.stringify(value) + code;
  if (/(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|Bearer\s+[A-Za-z0-9_.-]{16,}|(?:api[_-]?key|password|secret|token)\s*[=:]\s*["'][^"']{8,}["'])/i.test(shared)) throw new Error("Shared tool contains a likely credential; use a named capability profile");
}
export class ToolLibrary {
  private readonly db: DatabaseSync;
  private readonly blobs: string;
  constructor(readonly directory: string) {
    this.blobs = path.join(directory, "blobs"); mkdirSync(this.blobs, { recursive: true });
    this.db = new DatabaseSync(path.join(directory, "tools.sqlite"));
    this.db.exec("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; CREATE TABLE IF NOT EXISTS tools(name TEXT PRIMARY KEY, version INTEGER NOT NULL, active INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS versions(name TEXT NOT NULL, version INTEGER NOT NULL, manifest TEXT NOT NULL, hash TEXT NOT NULL, runtime TEXT NOT NULL, evidence TEXT NOT NULL, PRIMARY KEY(name,version));");
  }
  health() { this.db.prepare("SELECT count(*) FROM tools").get(); return { available: true }; }
  close() { this.db.close(); }
  find(query = "") {
    return this.db.prepare("SELECT v.name,v.version,v.manifest FROM versions v JOIN tools t ON t.name=v.name AND t.version=v.version WHERE t.active=1 ORDER BY v.name LIMIT 500").all()
      .map(r => ({ name: String(r.name), version: Number(r.version), description: (JSON.parse(String(r.manifest)) as Manifest).description }))
      .filter(r => `${r.name} ${r.description}`.toLowerCase().includes(query.slice(0, 200).toLowerCase())).slice(0, 20);
  }
  get(name: string, version?: number): Artifact {
    const row = this.db.prepare("SELECT v.*,t.active FROM versions v JOIN tools t ON t.name=v.name WHERE v.name=? AND v.version=COALESCE(?,t.version)").get(name, version ?? null);
    if (!row) throw new Error("tool_not_found: unknown tool/version");
    if (!row.active) throw new Error("tool_retired: tool is inactive");
    const hash = String(row.hash);
    if (!/^[a-f0-9]{64}$/.test(hash)) throw new Error("corrupt_artifact: invalid code hash");
    let code: string;
    try { code = readFileSync(path.join(this.blobs, hash + ".mjs"), "utf8"); } catch { throw new Error("corrupt_artifact: code is missing"); }
    if (digest(code) !== hash) throw new Error("corrupt_artifact: code hash mismatch");
    const manifest = JSON.parse(String(row.manifest)) as Manifest; validateManifest(manifest, code);
    const versionHash = digest(JSON.stringify({ manifest, hash, runtime: String(row.runtime) }));
    return { ...manifest, code, version: Number(row.version), hash, versionHash, runtime: String(row.runtime), active: true };
  }
  retire(name: string, expectedVersion: number) {
    const result = this.db.prepare("UPDATE tools SET active=0 WHERE name=? AND version=? AND active=1").run(name, expectedVersion);
    if (!result.changes) throw new Error("version_conflict: tool changed or is inactive");
    return { name, version: expectedVersion, active: false };
  }
  async publish(manifest: Manifest, code: string, expectedVersion: number, run: RunCode, signal?: AbortSignal) {
    // Copy before awaits: tests and the committed manifest always refer to the same snapshot.
    manifest = JSON.parse(JSON.stringify(manifest)) as Manifest;
    validateManifest(manifest, code);
    const initial = this.db.prepare("SELECT version FROM tools WHERE name=?").get(manifest.name);
    if (Number(initial?.version ?? 0) !== expectedVersion) throw new Error("version_conflict: read current version before publishing");
    const evidence: Array<{ jobId: string; status: string; runtime: string }> = [];
    for (const test of manifest.tests) {
      signal?.throwIfAborted();
      checkValue(manifest.inputSchema, test.input);
      const result = await run(code, test.input, manifest.capabilities, signal);
      evidence.push({ jobId: result.jobId, status: result.status, runtime: result.runtime });
      const passed = test.expectError ? result.status === "failed" && result.error?.code === test.expectError
        : result.status === "succeeded" && (test.tolerance !== undefined && typeof result.result === "number" && typeof test.expected === "number"
          ? Math.abs(result.result - test.expected) <= test.tolerance : isDeepStrictEqual(result.result, test.expected));
      if (!passed) throw new Error(`test_failed: case ${evidence.length}, job ${result.jobId}, ${result.error?.code ?? "unexpected output"}`);
      if (result.status === "succeeded") checkValue(manifest.outputSchema, result.result);
    }
    signal?.throwIfAborted();
    const hash = digest(code); const target = path.join(this.blobs, hash + ".mjs");
    if (!existsSync(target)) { const temporary = path.join(this.blobs, randomUUID() + ".tmp"); writeFileSync(temporary, code, { flag: "wx", mode: 0o600 }); renameSync(temporary, target); }
    if (digest(readFileSync(target, "utf8")) !== hash) throw new Error("corrupt_artifact: existing blob differs");
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const current = this.db.prepare("SELECT version FROM tools WHERE name=?").get(manifest.name);
      if (Number(current?.version ?? 0) !== expectedVersion) throw new Error("version_conflict: concurrent publish");
      const version = expectedVersion + 1;
      this.db.prepare("INSERT INTO versions VALUES(?,?,?,?,?,?)").run(manifest.name, version, JSON.stringify(manifest), hash, evidence[0].runtime, JSON.stringify(evidence));
      this.db.prepare("INSERT INTO tools VALUES(?,?,1) ON CONFLICT(name) DO UPDATE SET version=excluded.version,active=1").run(manifest.name, version);
      const versionHash = digest(JSON.stringify({ manifest, hash, runtime: evidence[0].runtime }));
      this.db.exec("COMMIT"); return { name: manifest.name, version, hash, versionHash, tests: evidence };
    } catch (error) { this.db.exec("ROLLBACK"); throw error; }
  }
  async call(name: string, version: number, input: unknown, runner: CodeRunner, run: RunCode, signal?: AbortSignal) {
    const artifact = this.get(name, version); checkValue(artifact.inputSchema, input);
    const health = await runner.health();
    if (!health.available) throw new Error("runner_unavailable: execution environment is unavailable");
    if (health.runtime && health.runtime !== artifact.runtime) throw new Error("runtime_changed: republish and validate this tool against the current image before reuse");
    if (artifact.capabilities.some(c => !health.capabilities.includes(c))) throw new Error("capability_unavailable: required profile is not enabled");
    const result = await run(artifact.code, input, artifact.capabilities, signal);
    if (result.status === "succeeded") checkValue(artifact.outputSchema, result.result);
    return { ...result, artifactId: name, version };
  }
}
