import http from "node:http";
import type { Duplex } from "node:stream";
import { RunnerError } from "./contract.js";

/** Docker is accessible only to this trusted gateway, never the job or Pi. */
export class Docker {
  constructor(readonly socketPath = process.env.RUNNER_DOCKER_SOCKET ?? (process.platform === "win32" ? "//./pipe/docker_engine" : "/var/run/docker.sock")) {}
  async request<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
    return new Promise((resolve, reject) => {
      const json = body === undefined ? undefined : JSON.stringify(body);
      const req = http.request({ socketPath: this.socketPath, method, path: `/v1.45${path}`,
        headers: json ? { "content-type": "application/json", "content-length": Buffer.byteLength(json) } : {},
        timeout: 10_000 }, res => {
        const chunks: Buffer[] = []; let size = 0;
        res.on("data", (chunk: Buffer) => { size += chunk.length; if (size > 2_097_152) req.destroy(new Error("Docker response too large")); else chunks.push(chunk); });
        res.on("error", reject);
        res.on("end", () => {
          if ((res.statusCode ?? 500) >= 300) { reject(new RunnerError("runner_unavailable", `Docker operation failed (${res.statusCode})`, 503)); return; }
          try { const text = Buffer.concat(chunks).toString(); resolve((text ? JSON.parse(text) : undefined) as T); } catch (error) { reject(error); }
        });
      });
      req.on("timeout", () => req.destroy(new Error("Docker request timed out")));
      req.on("error", reject); req.end(json);
    });
  }
  async attach(id: string): Promise<{ socket: Duplex; head: Buffer }> {
    return new Promise((resolve, reject) => {
      const req = http.request({ socketPath: this.socketPath, method: "POST",
        path: `/v1.45/containers/${id}/attach?stream=1&stdin=1&stdout=1&stderr=1`,
        headers: { Connection: "Upgrade", Upgrade: "tcp" }, timeout: 10_000 });
      req.on("upgrade", (_res, socket, head) => { req.setTimeout(0); resolve({ socket, head }); });
      req.on("response", res => { res.resume(); reject(new Error(`Docker attach failed (${res.statusCode})`)); });
      req.on("timeout", () => req.destroy(new Error("Docker attach timed out")));
      req.on("error", reject); req.end();
    });
  }
}

/** Parse Docker's 8-byte multiplex headers with a bound before buffering a frame. */
export class DockerFrames {
  private buffer: Buffer = Buffer.alloc(0);
  constructor(private readonly receive: (stream: number, chunk: Buffer) => void, private readonly maximum = 262_144) {}
  push(chunk: Buffer): void {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    while (this.buffer.length >= 8) {
      const length = this.buffer.readUInt32BE(4);
      if (length > this.maximum || ![1, 2].includes(this.buffer[0]!)) throw new Error("Invalid Docker stream frame");
      if (this.buffer.length < length + 8) break;
      this.receive(this.buffer[0]!, this.buffer.subarray(8, length + 8));
      this.buffer = this.buffer.subarray(length + 8);
    }
    if (this.buffer.length > this.maximum + 8) throw new Error("Docker stream buffer limit");
  }
}
