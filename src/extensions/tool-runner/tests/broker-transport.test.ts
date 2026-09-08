import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { gzipSync } from "node:zlib";
import { afterEach, expect, it, vi } from "vitest";
import { lookup } from "node:dns/promises";
import http from "node:http";
import https from "node:https";
import { publicRequest } from "../src/broker.js";

vi.mock("node:dns/promises", () => ({ lookup: vi.fn() }));
afterEach(() => vi.restoreAllMocks());

function transport(payload: Buffer, headers: Record<string, string> = {}) {
  const connections: unknown[] = [];
  vi.spyOn(http, "request").mockImplementation(((_url: URL, options: any, respond: any) => {
    const request = new EventEmitter() as any;
    request.write = () => true; request.destroy = () => request;
    request.end = () => {
      options.lookup("example.com", {}, (...args: unknown[]) => connections.push(args));
      const response = new PassThrough() as any; response.headers = headers; response.statusCode = 200;
      queueMicrotask(() => { respond(response); response.end(payload); });
    };
    return request;
  }) as any);
  return connections;
}
it("pins the validated DNS answer without a second resolution and rejects mixed private records", async () => {
  vi.mocked(lookup).mockResolvedValueOnce([{ address: "93.184.216.34", family: 4 }] as never);
  const connections = transport(Buffer.from("hello"));
  expect((await publicRequest(new URL("http://example.com"), "GET", {}, undefined, new AbortController().signal)).text).toBe("hello");
  expect(connections).toEqual([[null, "93.184.216.34", 4]]);
  expect(lookup).toHaveBeenCalledTimes(1);
  vi.mocked(lookup).mockResolvedValueOnce([{ address: "93.184.216.34", family: 4 }, { address: "::ffff:127.0.0.1", family: 6 }] as never);
  await expect(publicRequest(new URL("http://example.com"), "GET", {}, undefined, new AbortController().signal)).rejects.toThrow("non-public");
  expect(connections).toHaveLength(1);
});
it("limits decoded response size, including compressed payloads", async () => {
  vi.mocked(lookup).mockResolvedValue([{ address: "93.184.216.34", family: 4 }] as never);
  transport(gzipSync(Buffer.alloc(2_097_153, 65)), { "content-encoding": "gzip" });
  await expect(publicRequest(new URL("http://example.com"), "GET", {}, undefined, new AbortController().signal)).rejects.toThrow("2 MiB");
});
it("cancels pending DNS without opening a connection", async () => {
  vi.mocked(lookup).mockImplementation(() => new Promise(() => {}));
  const request = vi.spyOn(https, "request"); const controller = new AbortController();
  const pending = publicRequest(new URL("https://example.com"), "GET", {}, undefined, controller.signal);
  controller.abort(new Error("cancelled")); await expect(pending).rejects.toThrow("cancelled");
  expect(request).not.toHaveBeenCalled();
});
