import { createHash } from "node:crypto";
import path from "node:path";
import type { PiAgentConfig, TkbAdapterConfig } from "./config.js";
import { PiAgentRuntime, type AgentRuntimeApi } from "./runtime.js";

export class ScopeAuthorizationError extends Error {
  constructor() { super("scope credential is not authorized"); }
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b))
      .map(([key, child]) => `${JSON.stringify(key)}:${canonical(child)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

const defaults = {
  bank_id: "default-team", visibility: null, write_tags: [], subject_id: null,
  agent_name: null, policy_version: 1, observation_scopes: [],
};

export function scopeNamespace(binding: Record<string, unknown>): string {
  const identity = canonical({ ...defaults, ...binding });
  return identity === canonical(defaults) ? "default-team"
    : createHash("sha256").update(identity).digest("hex");
}

/** Immutable configuration; credential rotation takes effect on process restart.
 * Persistence is keyed by the binding, not by its bearer credential. */
export class ScopedRuntimeRegistry {
  private readonly runtimes = new Map<string, Promise<AgentRuntimeApi>>();
  private readonly bindings: Record<string, Record<string, unknown>>;

  constructor(
    private readonly shared: AgentRuntimeApi,
    private readonly config: PiAgentConfig,
    private readonly adapter: TkbAdapterConfig,
    bindingsJson = process.env.MEMORY_SCOPE_BINDINGS ?? "{}",
    private readonly create: (config: PiAgentConfig, adapter: TkbAdapterConfig) => AgentRuntimeApi
      = (config, adapter) => new PiAgentRuntime(config, adapter),
  ) {
    try { this.bindings = JSON.parse(bindingsJson); }
    catch { throw new Error("MEMORY_SCOPE_BINDINGS must be valid JSON"); }
    if (!this.bindings || Array.isArray(this.bindings) || typeof this.bindings !== "object") {
      throw new Error("MEMORY_SCOPE_BINDINGS must be an object");
    }
    for (const [digest, binding] of Object.entries(this.bindings)) {
      if (!/^[a-f0-9]{64}$/.test(digest) || !binding || Array.isArray(binding)
        || typeof binding !== "object"
        || Object.keys(binding).some((key) => !Object.hasOwn(defaults, key))) {
        throw new Error("invalid MEMORY_SCOPE_BINDINGS entry");
      }
    }
  }

  async resolve(token: string | string[] | undefined): Promise<AgentRuntimeApi> {
    if (token === undefined) return this.shared;
    if (typeof token !== "string" || !token || token.length > 4096) throw new ScopeAuthorizationError();
    const binding = this.bindings[createHash("sha256").update(token).digest("hex")];
    if (!binding) throw new ScopeAuthorizationError();
    const namespace = scopeNamespace(binding);
    if (namespace === "default-team") return this.shared;
    let pending = this.runtimes.get(namespace);
    if (!pending) {
      const root = path.join(this.config.dataDir, "scopes", namespace);
      const runtime = this.create({
        ...this.config, dataDir: root, sessionDir: path.join(root, "sessions"),
        transcriptDir: path.join(root, "transcripts"), toolLibraryDir: path.join(root, "tool-library"),
      }, { ...this.adapter, scopeToken: token, scopeKey: namespace, strictContract: true });
      pending = runtime.initialize().then(() => runtime).catch(async () => {
        this.runtimes.delete(namespace);
        await runtime.close().catch(() => undefined);
        throw new ScopeAuthorizationError();
      });
      this.runtimes.set(namespace, pending);
    }
    return pending;
  }

  async close(): Promise<void> {
    await Promise.all([...this.runtimes.values()].map(async (pending) => {
      const runtime = await pending.catch(() => undefined);
      await runtime?.close();
    }));
    this.runtimes.clear();
  }

  async initializeDeliveryScopes(tokensJson = process.env.PI_AGENT_MEMORY_DELIVERY_TOKENS ?? "[]"): Promise<void> {
    if (!this.adapter.conversationMemoryReliableDelivery) return;
    let tokens: unknown;
    try { tokens = JSON.parse(tokensJson); }
    catch { throw new Error("PI_AGENT_MEMORY_DELIVERY_TOKENS must be valid JSON"); }
    if (!Array.isArray(tokens) || tokens.some((token) => typeof token !== "string")) {
      throw new Error("PI_AGENT_MEMORY_DELIVERY_TOKENS must be an array of configured credentials");
    }
    const provided = new Map<string, string>();
    for (const token of tokens as string[]) {
      const binding = this.bindings[createHash("sha256").update(token).digest("hex")];
      if (!binding) throw new ScopeAuthorizationError();
      provided.set(scopeNamespace(binding), token);
    }
    const required = new Set(Object.values(this.bindings).map(scopeNamespace));
    required.delete("default-team");
    for (const namespace of required) {
      if (!provided.has(namespace)) throw new Error("reliable delivery requires a startup credential for every isolated scope");
    }
    for (const namespace of required) await this.resolve(provided.get(namespace)!);
  }
}
