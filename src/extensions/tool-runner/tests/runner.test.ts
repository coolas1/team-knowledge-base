import { describe, expect, it } from "vitest";
import { createServer } from "node:http";
import { parseJob } from "../src/contract.js";
import { Broker, publicAddress, publicUrl } from "../src/broker.js";
import { DockerFrames } from "../src/docker.js";
import { Docker } from "../src/docker.js";
import { jobTemplate, Jobs } from "../src/jobs.js";
import { createRunnerServer } from "../src/server.js";

describe("job boundary", () => {
  it("rejects management fields and bounds code, input and time", () => {
    expect(parseJob({ code: "export default()=>1" }).input).toBeNull();
    for (const extra of [{ image: "host" }, { mounts: ["/"] }, { timeoutMs: 20001 }, { code: "x".repeat(65537) }, { input: "x".repeat(262144) }]) {
      expect(() => parseJob({ code: "export default()=>1", ...extra })).toThrow();
    }
  });
  it("constructs an isolated template without secrets or mounts", () => {
    const template = jobTemplate(`sha256:${"a".repeat(64)}`, "job", "UTC");
    expect(template.User).toBe("1000:1000");
    expect(template.HostConfig).toMatchObject({ NetworkMode: "none", ReadonlyRootfs: true, CapDrop: ["ALL"], PidsLimit: 32 });
    expect(JSON.stringify(template)).not.toMatch(/Binds|docker.sock|API_KEY/);
    expect(() => jobTemplate("mutable:tag", "id", "UTC")).toThrow();
  });
  it("parses split Docker frames and rejects excessive declared sizes", () => {
    const values: string[] = []; const frames = new DockerFrames((_id, chunk) => values.push(chunk.toString()));
    const header = Buffer.alloc(8); header[0] = 1; header.writeUInt32BE(5, 4);
    const data = Buffer.concat([header, Buffer.from("hello")]);
    frames.push(data.subarray(0, 9)); frames.push(data.subarray(9)); expect(values).toEqual(["hello"]);
    header.writeUInt32BE(1_000_000, 4); expect(() => frames.push(header)).toThrow();
  });
});
describe("network broker", () => {
  it("rejects non-public IPs including mapped IPv6 and URL normalization", () => {
    for (const ip of ["127.0.0.1", "10.0.0.1", "172.16.1.1", "192.168.1.1", "169.254.169.254", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "224.0.0.1"]) expect(publicAddress(ip), ip).toBe(false);
    expect(publicAddress("8.8.8.8")).toBe(true);
    for (const url of ["file:///etc/passwd", "http://2130706433", "http://0x7f000001", "http://user:pass@example.com", "http://[::1]"]) expect(() => publicUrl(url), url).toThrow();
  });
  it("checks every redirect and enforces declared capability", async () => {
    let calls = 0;
    const broker = new Broker(true, {}, {}, async url => { calls++; return { url: url.href, status: 302, location: "http://127.0.0.1/private", text: "", contentType: "", retrievedAt: "now" }; });
    await expect(broker.call({ url: "https://example.com" }, [], new AbortController().signal)).rejects.toThrow("declared");
    await expect(broker.call({ url: "https://example.com" }, ["public_http"], new AbortController().signal)).rejects.toThrow("Non-public");
    expect(calls).toBe(1);
  });
  it("keeps credential profiles on their configured origin and redacts reflected secrets", async () => {
    const requests: unknown[] = [];
    const broker = new Broker(false, { search: { origin: "https://example.com", path: "/search", method: "GET", secretEnv: "TEST_SECRET", header: "Authorization", prefix: "Bearer " } }, { TEST_SECRET: "hidden-value" }, async (url, method, headers) => {
      requests.push({ url: url.href, method, headers }); return { url: url.href, status: 200, text: "hidden-value", contentType: "text/plain", retrievedAt: "now" };
    });
    const result = await broker.call({ capability: "search", url: "https://evil.example", query: { q: "test" } }, ["search"], new AbortController().signal);
    expect(requests).toEqual([{ url: "https://example.com/search?q=test", method: "GET", headers: { Authorization: "Bearer hidden-value" } }]);
    expect(result.text).toBe("[redacted]");
    expect(broker.available()).toEqual(["search"]);
  });
});
describe("runner HTTP authentication", () => {
  it("denies unauthenticated requests before accessing the backend", async () => {
    const server = createRunnerServer({ health: () => { throw new Error("must not run"); } } as unknown as Jobs, "test-token-with-at-least-24-characters");
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
    try {
      const address = server.address() as { port: number };
      expect((await fetch(`http://127.0.0.1:${address.port}/health`)).status).toBe(401);
    } finally { await new Promise<void>(resolve => server.close(() => resolve())); }
  });
});

describe.skipIf(process.env.RUN_TOOL_RUNNER_INTEGRATION !== "1")("real disposable containers", () => {
  it("runs generated code and enforces filesystem isolation, failure, limits and cancellation", async () => {
    const jobs = new Jobs(); await jobs.initialize();
    const run = (code: string, timeoutMs = 20000) => jobs.run(parseJob({ code, timeoutMs }));
    const success = await run(`import {writeFile,readFile} from 'node:fs/promises'; import {Parser} from 'htmlparser2'; export default async()=>{await writeFile('/work/a','ok'); return {value:await readFile('/work/a','utf8'),year:new Date().getUTCFullYear(),parser:typeof Parser,secret:process.env.RUNNER_TOKEN??null}}`);
    expect(success.status, JSON.stringify(success)).toBe("succeeded");
    expect(success.result).toMatchObject({ value: "ok", parser: "function", secret: null });
    for (const file of ["/work/a", "/app/data/auth.json", "/var/run/docker.sock"]) {
      expect((await run(`import {readFile} from 'node:fs/promises';export default()=>readFile(${JSON.stringify(file)},'utf8')`)).status).toBe("failed");
    }
    expect((await run("export default()=>{throw new Error('repair me')}" )).error?.message).toContain("repair me");
    expect((await run("export default()=>{while(true){}}", 1500)).error?.code).toBe("time_limit");
    expect((await run("export default()=>{console.error('x'.repeat(300000));return 1}" )).error?.code).toBe("output_limit");
    const controller = new AbortController();
    const waiting = jobs.run(parseJob({ code: "export default()=>new Promise(()=>{})" }), controller.signal);
    setTimeout(() => controller.abort(), 1000);
    expect((await waiting).status).toBe("failed");
    expect((await jobs.health()).activeJobs).toBe(0);
  }, 60000);
  it("runs real RPC, rejects forged messages, enforces memory/process limits and cleans only owned jobs", async () => {
    const broker = new Broker(true, {}, {}, async url => ({ url: url.href, status: 200, contentType: "text/html", text: "<h1>Independent fixture</h1>", retrievedAt: "2026-01-02T00:00:00.000Z" }));
    const docker = new Docker(); const jobs = new Jobs(docker, broker); await jobs.initialize();
    const run = (code: string, capabilities: string[] = []) => jobs.run(parseJob({ code, capabilities, timeoutMs: 4000 }));
    const fetched = await run("export default async(input,host)=>host.fetch({url:'https://example.com'})", ["public_http"]);
    expect(fetched.result).toMatchObject({ text: "<h1>Independent fixture</h1>" });
    expect((await run("export default async(input,host)=>host.fetch({url:'http://127.0.0.1'})", ["public_http"])).status).toBe("failed");
    expect((await run("export default()=>{process.stdout.write('malformed\\n');return 1}" )).status).toBe("failed");
    expect((await run("export default()=>{for(let i=0;i<11;i++)process.stdout.write(JSON.stringify({type:'request',id:i,request:{url:'https://example.com'}})+'\\n');return new Promise(()=>{})}", ["public_http"])).error?.code).toBe("protocol_error");
    const isolation = await run("import {writeFile} from 'node:fs/promises';import os from 'node:os';export default async()=>{let readonly=false;try{await writeFile('/opt/job/escape','x')}catch{readonly=true}return{uid:process.getuid(),readonly,interfaces:Object.keys(os.networkInterfaces())}}" );
    expect(isolation.result).toEqual({ uid: 1000, readonly: true, interfaces: ["lo"] });
    const memory = await run("export default()=>{const data=[];while(true)data.push(Buffer.alloc(32*1024*1024,1))}" );
    expect(memory.status).toBe("failed");
    const children = await run("import{spawn}from'node:child_process';export default()=>{for(let i=0;i<40;i++){const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)']);child.on('error',()=>{})}return new Promise(()=>{})}" );
    expect(children.error?.code).toBe("time_limit");
    const health = await jobs.health(); const template = jobTemplate(health.runtime!, "stale", "UTC");
    const owned = await docker.request<{ Id: string }>("POST", "/containers/create", template);
    const unrelated = await docker.request<{ Id: string }>("POST", "/containers/create", { ...template, Labels: { "tkb.test": "unrelated" } });
    try {
      await new Jobs(docker, broker).initialize();
      await expect(docker.request("GET", `/containers/${owned.Id}/json`)).rejects.toThrow();
      expect(await docker.request("GET", `/containers/${unrelated.Id}/json`)).toBeTruthy();
    } finally { await docker.request("DELETE", `/containers/${unrelated.Id}?force=1`); }
    const remaining = await docker.request<unknown[]>("GET", `/containers/json?all=1&filters=${encodeURIComponent(JSON.stringify({ label: ["tkb.owner=team-kb-tool-runner"] }))}`);
    expect(remaining).toEqual([]);
  }, 60000);
});
