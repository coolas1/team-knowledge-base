import http from "node:http";
import https from "node:https";
import { lookup } from "node:dns/promises";
import { createBrotliDecompress, createGunzip, createInflate } from "node:zlib";
import ipaddr from "ipaddr.js";
import { LIMITS, RunnerError, cleanError, object } from "./contract.js";

export interface CapabilityProfile {
  origin: string;
  path: string;
  method: "GET" | "HEAD" | "POST";
  secretEnv?: string;
  header?: string;
  prefix?: string;
}
export interface WebResult { url: string; status: number; contentType: string; text: string; retrievedAt: string }
export function publicAddress(address: string): boolean {
  try {
    const parsed = ipaddr.process(address);
    return parsed.range() === "unicast";
  } catch { return false; }
}
export function publicUrl(value: string): URL {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new RunnerError("network_denied", "Only public HTTP(S) URLs without credentials are permitted");
  const hostname = url.hostname.replace(/^\[|\]$/g, "");
  if (hostname === "localhost" || hostname.endsWith(".localhost") || (ipaddr.isValid(hostname) && !publicAddress(hostname))) {
    throw new RunnerError("network_denied", "Non-public destination denied");
  }
  return url;
}

/** Resolve once, validate every address, then pin the connection to the validated answer. */
export async function publicRequest(url: URL, method: string, headers: Record<string, string>, body: string | undefined, signal: AbortSignal): Promise<WebResult & { location?: string }> {
  const hostname = url.hostname.replace(/^\[|\]$/g, "");
  let abort: () => void = () => {};
  const addresses = await Promise.race([
    lookup(hostname, { all: true, verbatim: true }),
    new Promise<never>((_, reject) => { abort = () => reject(signal.reason); if (signal.aborted) abort(); else signal.addEventListener("abort", abort, { once: true }); }),
  ]).finally(() => signal.removeEventListener("abort", abort));
  signal.throwIfAborted();
  if (!addresses.length || addresses.some(a => !publicAddress(a.address))) throw new RunnerError("network_denied", "DNS resolved to a non-public address");
  const selected = addresses[0]!;
  return new Promise((resolve, reject) => {
    const request = (url.protocol === "https:" ? https : http).request(url, {
      method, headers: { "accept-encoding": "gzip, deflate, br", ...headers }, signal,
      // The HTTP Host header and TLS servername remain the original hostname.
      lookup: ((_name: string, options: { all?: boolean }, callback: (...args: unknown[]) => void) => {
        if (options.all) callback(null, [selected]); else callback(null, selected.address, selected.family);
      }) as NonNullable<http.RequestOptions["lookup"]>,
    }, response => {
      const encoding = response.headers["content-encoding"];
      const decoder = encoding === "gzip" ? createGunzip() : encoding === "deflate" ? createInflate() : encoding === "br" ? createBrotliDecompress() : undefined;
      const stream = decoder ? response.pipe(decoder) : response;
      let size = 0;
      const chunks: Buffer[] = [];
      const fail = (error: Error) => { response.destroy(); decoder?.destroy(); request.destroy(); reject(error); };
      response.on("error", fail);
      stream.on("error", fail);
      stream.on("data", (chunk: Buffer) => {
        size += chunk.length;
        if (size > LIMITS.networkBytes) fail(new RunnerError("network_size_limit", "Response exceeds 2 MiB"));
        else chunks.push(chunk);
      });
      stream.on("end", () => resolve({ url: url.href, status: response.statusCode ?? 0,
        contentType: String(response.headers["content-type"] ?? ""), text: Buffer.concat(chunks).toString("utf8"),
        location: response.headers.location, retrievedAt: new Date().toISOString() }));
    });
    request.on("error", reject);
    if (body) request.write(body);
    request.end();
  });
}

export class Broker {
  constructor(readonly allowPublic = true, readonly profiles: Record<string, CapabilityProfile> = {},
    private readonly env: NodeJS.ProcessEnv = process.env, private readonly request = publicRequest) {
    for (const [name, profile] of Object.entries(profiles)) {
      if (!/^[a-z][a-z0-9_-]{0,63}$/.test(name) || name === "public_http") throw new Error("Invalid profile name");
      const url = publicUrl(profile.origin);
      if (url.protocol !== "https:" || url.origin !== profile.origin || !profile.path.startsWith("/") || profile.path.startsWith("//") || !["GET", "HEAD", "POST"].includes(profile.method)) throw new Error("Invalid capability profile");
      if (profile.secretEnv && (!env[profile.secretEnv] || !profile.header || !/^[A-Za-z0-9-]+$/.test(profile.header))) throw new Error(`Missing profile credential: ${name}`);
    }
  }
  available(): string[] { return [...(this.allowPublic ? ["public_http"] : []), ...Object.keys(this.profiles)]; }
  assertCapabilities(caps: string[]): void {
    if (caps.some(c => !this.available().includes(c))) throw new RunnerError("capability_unavailable", "Requested capability is not configured");
  }
  secrets(): string[] { return Object.values(this.profiles).map(p => p.secretEnv ? this.env[p.secretEnv] ?? "" : "").filter(Boolean); }
  async call(value: unknown, capabilities: string[], signal: AbortSignal): Promise<WebResult> {
    const data = object(value);
    const capability = typeof data.capability === "string" ? data.capability : "public_http";
    if (!capabilities.includes(capability)) throw new RunnerError("network_denied", "Capability was not declared by this job");
    this.assertCapabilities([capability]);
    let url: URL;
    let method = "GET";
    let body: string | undefined;
    const headers: Record<string, string> = {};
    const profile = this.profiles[capability];
    if (profile) {
      url = new URL(profile.path, profile.origin);
      if (url.origin !== profile.origin) throw new RunnerError("network_denied", "Profile origin mismatch");
      method = profile.method;
      if (data.query !== undefined) for (const [k, v] of Object.entries(object(data.query))) {
        if (typeof v !== "string" || v.length > 4000) throw new RunnerError("invalid_input", "Profile query must contain short strings");
        url.searchParams.set(k, v);
      }
      if (data.body !== undefined) {
        if (method !== "POST") throw new RunnerError("network_denied", "Body not permitted");
        body = JSON.stringify(data.body); headers["content-type"] = "application/json";
      }
      if (profile.secretEnv) headers[profile.header!] = `${profile.prefix ?? ""}${this.env[profile.secretEnv]}`;
    } else {
      if (typeof data.url !== "string" || data.url.length > 8192) throw new RunnerError("invalid_input", "Invalid URL");
      url = publicUrl(data.url);
      method = data.method === "HEAD" ? "HEAD" : "GET";
      if (data.method && !["GET", "HEAD"].includes(String(data.method))) throw new RunnerError("network_denied", "Method not permitted");
    }
    try {
      for (let hop = 0; hop <= 3; hop++) {
        signal.throwIfAborted();
        const response = await this.request(publicUrl(url.href), method, headers, body, signal);
        if ([301, 302, 303, 307, 308].includes(response.status)) {
          if (profile || hop === 3 || !response.location) throw new RunnerError("network_denied", "Redirect not permitted or redirect limit reached");
          url = publicUrl(new URL(response.location, url).href); continue;
        }
        if (response.status < 200 || response.status >= 300) throw new RunnerError("http_error", `Remote HTTP ${response.status}`);
        // Do not return response headers or reflected configured credentials.
        const strip = (value: string) => this.secrets().reduce((text, secret) => text.split(secret).join("[redacted]"), value);
        return { url: strip(response.url), status: response.status, contentType: strip(response.contentType), text: strip(response.text), retrievedAt: response.retrievedAt };
      }
      throw new RunnerError("network_denied", "Redirect limit reached");
    } catch (error) { throw new RunnerError(error instanceof RunnerError ? error.code : "network_error", cleanError(error, this.secrets())); }
  }
}
