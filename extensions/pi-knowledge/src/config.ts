import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export interface KnowledgeConfig {
  /** 知识库 vault 根目录（唯一事实源） */
  vaultDir: string;
  /** 影子 md 输出目录（相对 vaultDir） */
  derivedDirName: string;
  pg: {
    host: string;
    port: number;
    user: string;
    password: string;
    database: string;
  };
  neo4j: {
    url: string;
    user: string;
    password: string;
  };
  ark: {
    /** 火山方舟 API Key，默认取 ARK_API_KEY 环境变量 */
    apiKey: string;
    baseUrl: string;
    /** 多模态 embedding 模型（doubao-embedding-vision 系列，文本/图片同一向量空间） */
    embeddingModel: string;
    /** 入库向量维度（服务端 dimensions 参数降维），必须与 schema 的 vector(N) 一致 */
    embeddingDim: number;
    /** 图片理解 VLM 模型 */
    visionModel: string;
    /** 实体关系抽取用的小模型 */
    extractionModel: string;
  };
  /** 开场记忆注入的 token 预算（粗略按字符/4 估算） */
  injectTokenBudget: number;
}

const DEFAULTS: KnowledgeConfig = {
  vaultDir: "D:\\knowledge-vault",
  derivedDirName: ".derived",
  pg: {
    host: "127.0.0.1",
    port: 5433,
    user: "pi",
    password: "pi_knowledge",
    database: "knowledge",
  },
  neo4j: {
    url: "bolt://127.0.0.1:7688",
    user: "neo4j",
    password: "pi_knowledge",
  },
  ark: {
    apiKey: "",
    baseUrl: "https://ark.cn-beijing.volces.com/api/v3",
    embeddingModel: "doubao-embedding-vision-251215",
    embeddingDim: 1024,
    visionModel: "doubao-seed-2-0-mini-260428",
    extractionModel: "doubao-seed-2-0-lite-260428",
  },
  injectTokenBudget: 1500,
};

/** 用户配置文件：~/.pi/agent/knowledge.json（可只写需要覆盖的字段，深合并） */
function configFilePath(): string {
  return path.join(os.homedir(), ".pi", "agent", "knowledge.json");
}

function deepMerge<T>(base: T, override: unknown): T {
  if (override === null || typeof override !== "object" || Array.isArray(override)) {
    return base;
  }
  const result: Record<string, unknown> = { ...(base as Record<string, unknown>) };
  for (const [key, value] of Object.entries(override as Record<string, unknown>)) {
    const baseValue = result[key];
    if (
      baseValue !== null &&
      typeof baseValue === "object" &&
      !Array.isArray(baseValue) &&
      value !== null &&
      typeof value === "object" &&
      !Array.isArray(value)
    ) {
      result[key] = deepMerge(baseValue, value);
    } else if (value !== undefined) {
      result[key] = value;
    }
  }
  return result as T;
}

let cached: KnowledgeConfig | undefined;

export function loadConfig(): KnowledgeConfig {
  if (cached) return cached;
  let fromFile: unknown = {};
  const file = configFilePath();
  if (fs.existsSync(file)) {
    fromFile = JSON.parse(fs.readFileSync(file, "utf8"));
  }
  const merged = deepMerge(DEFAULTS, fromFile);
  if (!merged.ark.apiKey) {
    merged.ark.apiKey = process.env.ARK_API_KEY ?? "";
  }
  cached = merged;
  return merged;
}

export function resetConfigCache(): void {
  cached = undefined;
}

export function derivedDir(config: KnowledgeConfig): string {
  return path.join(config.vaultDir, config.derivedDirName);
}
