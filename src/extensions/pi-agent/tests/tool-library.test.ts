import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToolLibrary, validateManifest, type Manifest } from "../src/tool-library.js";
import { AuthoringBudget, buildAuthoringTools } from "../src/authoring.js";
import { RunnerClient, type CodeRunner } from "../src/runner-client.js";

const libraries: ToolLibrary[] = [];
function library(directory = mkdtempSync(path.join(tmpdir(), "tkb-library-"))) { const value = new ToolLibrary(directory); libraries.push(value); return value; }
afterEach(() => { for (const value of libraries.splice(0)) { try { value.close(); } catch {} } });
const manifest: Manifest = { name: "gen_double", description: "Double a supplied number", inputSchema: { type: "object", properties: { n: { type: "number" } }, required: ["n"], additionalProperties: false }, outputSchema: { type: "number" }, capabilities: [], tests: [{ input: { n: 3 }, expected: 6 }, { input: { n: 0 }, expected: 0 }] };
const code = "export default input=>input.n*2";
const run = vi.fn(async (_code: string, input: unknown) => ({ jobId: "job", status: "succeeded", result: (input as { n: number }).n * 2, logs: "", runtime: "sha256:test" }));
const runner: CodeRunner = { run: job => run(job.code, job.input), health: async () => ({ available: true, capabilities: [] }) };

describe("persistent generated tools", () => {
  it("publishes tested immutable versions and restores schemas/code after restart", async () => {
    const first = library(); const saved = await first.publish(manifest, code, 0, run);
    expect(saved.version).toBe(1); expect(saved.tests).toHaveLength(2);
    first.close();
    const reopened = library(first.directory);
    expect(reopened.find("double")).toEqual([{ name: "gen_double", description: manifest.description, version: 1 }]);
    expect(reopened.get("gen_double").code).toBe(code);
    expect((await reopened.call("gen_double", 1, { n: 7 }, runner, run)).result).toBe(14);
    await reopened.publish(manifest, code + " //v2", 1, run);
    expect(reopened.get("gen_double", 1).code).toBe(code);
    expect(reopened.get("gen_double").version).toBe(2);
    await expect(reopened.publish(manifest, code, 1, run)).rejects.toThrow("version_conflict");
    reopened.retire("gen_double", 2); expect(reopened.find()).toEqual([]);
    expect(() => reopened.get("gen_double", 1)).toThrow("retired");
  });
  it("rejects failed tests, forged reports, changed code and concurrent publish", async () => {
    const lib = library();
    await expect(lib.publish(manifest, code, 0, async () => ({ jobId: "bad", status: "succeeded", result: 9, logs: "", runtime: "test" }))).rejects.toThrow("test_failed");
    expect(lib.find()).toEqual([]);
    const results = await Promise.allSettled([lib.publish(manifest, code, 0, run), lib.publish(manifest, code + " //another", 0, run)]);
    expect(results.filter(r => r.status === "fulfilled")).toHaveLength(1);
    const current = lib.get("gen_double");
    writeFileSync(path.join(lib.directory, "blobs", current.hash + ".mjs"), "export default()=>999");
    expect(() => lib.get("gen_double")).toThrow("hash mismatch");
  });
  it("validates namespace, schemas, known secrets, distinct tests and input", async () => {
    for (const patch of [{ name: "tkb_search" }, { inputSchema: { type: "object", $ref: "https://host/schema" } }, { tests: [manifest.tests[0], manifest.tests[0]] }, { description: "Bearer abcdefghijklmnopqrstuvwxyz" }]) {
      expect(() => validateManifest({ ...manifest, ...patch }, code)).toThrow();
    }
    const lib = library(); await lib.publish(manifest, code, 0, run);
    await expect(lib.call("gen_double", 1, { n: "wrong" }, runner, run)).rejects.toThrow("schema_validation");
    const unavailable = { ...runner, health: async () => ({ available: false, capabilities: [] }) };
    await expect(lib.call("gen_double", 1, { n: 4 }, unavailable, run)).rejects.toThrow("runner_unavailable");
    expect(() => lib.get("unknown")).toThrow("not_found");
  });
  it("supports numeric tolerance and expected execution failures", async () => {
    const lib = library();
    const tests = [{ input: { n: 3 }, expected: 6.001, tolerance: 0.01 }, { input: { n: -1 }, expectError: "execution_failed" }];
    await lib.publish({ ...manifest, tests }, code, 0, async (_code, input) => (input as { n: number }).n < 0
      ? { jobId: "failure", status: "failed", error: { code: "execution_failed", message: "negative" }, runtime: "test", logs: "" } : run(_code, input));
    expect(lib.get("gen_double").tests).toEqual(tests);
  });
  it("does not activate cancelled publications or change an executing version", async () => {
    const lib = library(); const controller = new AbortController();
    const cancelled = async (source: string, input: unknown) => { controller.abort(); return run(source, input); };
    await expect(lib.publish(manifest, code, 0, cancelled, controller.signal)).rejects.toThrow();
    expect(lib.find()).toEqual([]);
    await lib.publish(manifest, code, 0, run);
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    let started!: () => void; const began = new Promise<void>(resolve => { started = resolve; });
    const inFlight = lib.call(manifest.name, 1, { n: 5 }, runner, async (source, input) => { started(); await gate; expect(source).toBe(code); return run(source, input); });
    await began; lib.retire(manifest.name, 1); release();
    expect((await inFlight).result).toBe(10);
    await expect(lib.call(manifest.name, 1, { n: 5 }, runner, run)).rejects.toThrow("retired");
  });
  it("rechecks permissions and the runtime image on saved calls", async () => {
    const lib = library(); await lib.publish({ ...manifest, capabilities: ["search"] }, code, 0, run);
    await expect(lib.call(manifest.name, 1, { n: 2 }, runner, run)).rejects.toThrow("capability_unavailable");
    await expect(lib.call(manifest.name, 1, { n: 2 }, { ...runner, health: async () => ({ available: true, capabilities: ["search"], runtime: "other-image" }) }, run)).rejects.toThrow("runtime_changed");
  });
});
describe("bootstrap tools and budgets", () => {
  it("publishes and calls a newly named business tool without changing the tool allowlist", async () => {
    const tools = buildAuthoringTools(library(), runner, new AuthoringBudget());
    const invoke = (name: string, params: unknown) => tools.find(t => t.name === name)!.execute("id", params, undefined, undefined, {} as never);
    await invoke("publish_tool", { action: "publish", ...manifest, code, expectedVersion: 0 });
    const result = await invoke("call_tool", { name: manifest.name, version: 1, input: { n: 12 } });
    expect(result.content[0]).toMatchObject({ text: expect.stringContaining('"result":24') });
    expect(tools.map(t => t.name)).toEqual(["execute_code", "find_tools", "publish_tool", "call_tool"]);
  });
  it("counts publish tests and rejects build ID cycling; resets each turn", async () => {
    const budget = new AuthoringBudget(2, 1); const lib = library(); const tools = buildAuthoringTools(lib, runner, budget);
    const execute = tools[0];
    await execute.execute("1", { code, input: { n: 1 }, capabilities: [] }, undefined, undefined, {} as never);
    await execute.execute("2", { code, input: { n: 1 }, capabilities: [] }, undefined, undefined, {} as never);
    const stopped = await execute.execute("3", { code, input: { n: 1 }, capabilities: [] }, undefined, undefined, {} as never);
    expect((stopped as { terminate?: boolean }).terminate).toBe(true);
    budget.reset();
    await tools[2].execute("4", { action: "publish", ...manifest, code, expectedVersion: 0 }, undefined, undefined, {} as never);
    expect(() => budget.claim()).toThrow("budget");
  });
  it("health without a configured runner does not start jobs or expose credentials", async () => {
    const client = new RunnerClient("", "private-token");
    expect(await client.health()).toEqual({ available: false, capabilities: [] });
    await expect(client.run({ code, input: {}, capabilities: [], timeoutMs: 1000 })).rejects.toThrow("runner_unavailable");
  });
  it("binds server-issued build IDs to a turn and limits retries", () => {
    const budget = new AuthoringBudget(12, 2); const id = budget.build(); budget.build(id);
    expect(() => budget.build(id)).toThrow("repair budget");
    expect(() => budget.build("invented")).toThrow("Unknown buildId");
    budget.reset(); expect(() => budget.build(id)).toThrow("Unknown buildId");
    expect(budget.build()).not.toBe(id);
  });
});
