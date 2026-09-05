import { randomUUID } from "node:crypto";
import type { Duplex } from "node:stream";
import { StringDecoder } from "node:string_decoder";
import { Docker, DockerFrames } from "./docker.js";
import { Broker } from "./broker.js";
import { LIMITS, RunnerError, cleanError, object, type JobRequest, type JobResult } from "./contract.js";

const OWNER = "team-kb-tool-runner";
export function jobTemplate(image: string, jobId: string, timezone: string) {
  if (!/^sha256:[a-f0-9]{64}$/.test(image)) throw new Error("Job image must be an inspected immutable image ID");
  return {
    Image: image, User: "1000:1000", WorkingDir: "/work", Entrypoint: ["node", "/opt/job/entry.mjs"], Cmd: [],
    Env: [`TZ=${timezone}`, "NODE_ENV=production"], Labels: { "tkb.owner": OWNER, "tkb.job": jobId },
    AttachStdin: true, AttachStdout: true, AttachStderr: true, OpenStdin: true, StdinOnce: false, Tty: false,
    NetworkDisabled: true,
    HostConfig: { NetworkMode: "none", ReadonlyRootfs: true, CapDrop: ["ALL"], SecurityOpt: ["no-new-privileges:true"],
      Memory: 268_435_456, MemorySwap: 268_435_456, NanoCpus: 1_000_000_000, PidsLimit: 32,
      Tmpfs: { "/work": "rw,noexec,nosuid,nodev,size=32m,uid=1000,gid=1000,mode=0700" },
      Ulimits: [{ Name: "nofile", Soft: 128, Hard: 128 }], LogConfig: { Type: "none" }, AutoRemove: false },
  };
}
export class Jobs {
  private readonly active = new Map<string, AbortController>();
  private imageId?: string;
  constructor(readonly docker = new Docker(), readonly broker = new Broker(), readonly image = process.env.RUNNER_JOB_IMAGE ?? "team-kb-tool-job:latest",
    readonly timezone = process.env.PI_AGENT_TIMEZONE ?? "Asia/Shanghai") { new Intl.DateTimeFormat("en", { timeZone: timezone }); }
  async initialize(): Promise<void> {
    const old = await this.docker.request<Array<{ Id: string }>>("GET", `/containers/json?all=1&filters=${encodeURIComponent(JSON.stringify({ label: [`tkb.owner=${OWNER}`] }))}`);
    for (const container of old) await this.docker.request("DELETE", `/containers/${container.Id}?force=1`);
    const image = await this.docker.request<{ Id: string }>("GET", `/images/${encodeURIComponent(this.image)}/json`);
    this.imageId = image.Id;
  }
  async health() {
    await this.docker.request("GET", "/version");
    return { available: !!this.imageId, runtime: this.imageId, language: "javascript", timezone: this.timezone,
      capabilities: this.broker.available(), limits: LIMITS, activeJobs: this.active.size };
  }
  cancel(id: string): boolean { const controller = this.active.get(id); controller?.abort(new RunnerError("cancelled", "Job cancelled")); return !!controller; }
  async close(): Promise<void> { for (const controller of this.active.values()) controller.abort(new Error("Runner stopping")); }
  async run(job: JobRequest, signal?: AbortSignal): Promise<JobResult> {
    if (!this.imageId) throw new RunnerError("runner_unavailable", "Job image is unavailable", 503);
    this.broker.assertCapabilities(job.capabilities);
    if (this.active.size >= 4) throw new RunnerError("runner_busy", "Runner concurrency limit reached", 429);
    const jobId = randomUUID(); const controller = new AbortController();
    this.active.set(jobId, controller);
    const abort = () => controller.abort(new RunnerError("cancelled", "Job cancelled"));
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
    const timer = setTimeout(() => controller.abort(new RunnerError("time_limit", "Job exceeded its time limit")), job.timeoutMs);
    let containerId: string | undefined; let socket: Duplex | undefined; let logs = "";
    try {
      controller.signal.throwIfAborted();
      const created = await this.docker.request<{ Id: string }>("POST", "/containers/create", jobTemplate(this.imageId, jobId, this.timezone));
      containerId = created.Id; controller.signal.throwIfAborted();
      const attached = await this.docker.attach(containerId); socket = attached.socket;
      let bytes = 0; let pending = ""; let calls = 0; let completed = false;
      let terminal = false; let terminalValue: unknown;
      const stdout = new StringDecoder("utf8"); const stderr = new StringDecoder("utf8");
      const rpcIds = new Set<number>();
      const result = new Promise<unknown>((resolve, reject) => {
        const fail = (error: unknown) => { if (!completed) { completed = true; reject(error); } };
        controller.signal.addEventListener("abort", () => fail(controller.signal.reason), { once: true });
        socket!.on("error", fail);
        socket!.on("end", () => {
          if (terminal && !completed) { completed = true; resolve(terminalValue); }
          else fail(new RunnerError("execution_failed", "Job exited without a result"));
        });
        const frames = new DockerFrames((stream, chunk) => {
          bytes += chunk.length;
          if (bytes > LIMITS.output) throw new RunnerError("output_limit", "Job output limit exceeded");
          if (stream === 2) { logs += stderr.write(chunk); return; }
          pending += stdout.write(chunk);
          for (let newline; (newline = pending.indexOf("\n")) !== -1;) {
            const line = pending.slice(0, newline); pending = pending.slice(newline + 1);
            const message = object(JSON.parse(line));
            if (completed || terminal) throw new RunnerError("protocol_error", "Data after terminal result");
            if (message.type === "result") { terminal = true; terminalValue = message.value ?? null; }
            else if (message.type === "error") fail(new RunnerError("execution_failed", typeof message.message === "string" ? message.message : "Program failed"));
            else if (message.type === "request") {
              if (!Number.isSafeInteger(message.id) || rpcIds.has(Number(message.id)) || ++calls > LIMITS.networkCalls) throw new RunnerError("protocol_error", "Invalid or excessive network RPC");
              rpcIds.add(Number(message.id));
              void this.broker.call(message.request, job.capabilities, controller.signal).then(
                value => { if (!controller.signal.aborted && !completed) socket!.write(`${JSON.stringify({ id: message.id, value })}\n`); },
                error => { if (!controller.signal.aborted && !completed) socket!.write(`${JSON.stringify({ id: message.id, error: cleanError(error, this.broker.secrets()) })}\n`); },
              );
            } else throw new RunnerError("protocol_error", "Unknown job message");
          }
        });
        socket!.on("data", chunk => { try { frames.push(chunk); } catch (error) { fail(error); } });
        if (attached.head.length) { try { frames.push(attached.head); } catch (error) { fail(error); } }
      });
      // Observe failures immediately even while Docker start is still pending.
      void result.catch(() => undefined);
      await this.docker.request("POST", `/containers/${containerId}/start`);
      controller.signal.throwIfAborted();
      socket.write(`${JSON.stringify({ code: job.code, input: job.input })}\n`);
      const value = await result;
      return { jobId, status: "succeeded", result: value, logs: cleanError(new Error(logs), this.broker.secrets()), runtime: this.imageId };
    } catch (error) {
      return { jobId, status: "failed", error: { code: error instanceof RunnerError ? error.code : "execution_failed", message: cleanError(error, this.broker.secrets()) },
        logs: cleanError(new Error(logs), this.broker.secrets()), runtime: this.imageId };
    } finally {
      controller.abort(); clearTimeout(timer); signal?.removeEventListener("abort", abort); socket?.destroy();
      // Never report completion until the container is removed; cleanup failure is visible.
      try { if (containerId) await this.docker.request("DELETE", `/containers/${containerId}?force=1`); }
      finally { this.active.delete(jobId); }
    }
  }
}
