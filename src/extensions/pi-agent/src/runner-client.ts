export interface CodeJob { code: string; input: unknown; capabilities: string[]; timeoutMs: number }
export interface CodeResult { jobId: string; status: string; result?: unknown; error?: { code: string; message: string }; logs: string; runtime: string }
export interface RunnerHealth { available: boolean; runtime?: string; language?: string; timezone?: string; capabilities: string[] }
export interface CodeRunner {
  run(job: CodeJob, signal?: AbortSignal): Promise<CodeResult>;
  health(): Promise<RunnerHealth>;
}
export function redact(message: string): string {
  return message.replace(/(?:sk-[\w-]{12,}|Bearer\s+\S+|(?:api[_-]?key|token|password|secret)\s*[=:]\s*[^\s,;}]+)/gi, "[redacted]").slice(0, 240);
}
export class RunnerClient implements CodeRunner {
  constructor(private readonly url: string, private readonly token: string) {
    if (url && !["http:", "https:"].includes(new URL(url).protocol)) throw new Error("Invalid runner URL");
  }
  private async request(route: string, job?: CodeJob, signal?: AbortSignal): Promise<unknown> {
    if (!this.url || this.token.length < 24) throw new Error("runner_unavailable: configure runner URL and authentication token");
    try {
      const response = await fetch(new URL(route, this.url), {
        method: job ? "POST" : "GET", headers: { authorization: `Bearer ${this.token}`, "content-type": "application/json" },
        body: job ? JSON.stringify(job) : undefined,
        signal: AbortSignal.any([AbortSignal.timeout(job ? 30_000 : 3_000), ...(signal ? [signal] : [])]),
      });
      const chunks: Uint8Array[] = []; let size = 0;
      for await (const chunk of response.body ?? []) {
        size += chunk.length;
        if (size > 600_000) throw new Error("Runner response exceeds limit");
        chunks.push(chunk);
      }
      const result = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      if (!response.ok) throw new Error(`${result.code ?? result.error?.code ?? "runner_error"}: ${typeof result.error === "string" ? result.error : result.error?.message ?? response.status}`);
      return result;
    } catch (error) {
      throw new Error(redact(String(error).split(this.token || "\0").join("[redacted]")));
    }
  }
  async run(job: CodeJob, signal?: AbortSignal): Promise<CodeResult> { return await this.request("/jobs", job, signal) as CodeResult; }
  async health(): Promise<RunnerHealth> {
    try { return await this.request("/health") as RunnerHealth; }
    catch { return { available: false, capabilities: [] }; }
  }
}
