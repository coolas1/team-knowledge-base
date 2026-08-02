import fs from "node:fs/promises";
import path from "node:path";
import matter from "gray-matter";
import type { KnowledgeConfig } from "../config.ts";
import { arkChat } from "./ark.ts";
import { getPool } from "./db.ts";
import { ingestFile } from "./ingest.ts";

/**
 * 记忆模块（语义层）：
 * - 记忆是 vault/memories/<type>/ 下的结构化 md 文件（frontmatter 携带属性），一等公民
 * - 保存后立即走增量摄取入三路索引；用户可用任意编辑器直接改，改动由摄取管线自动收敛
 * - 会话结束时 LLM 反思提取新记忆，直接入库（不设 pending 审核）
 * - 开场只注入压缩视图：高优先级全文 + 其余一行索引，受 token 预算控制
 */

export type MemoryType = "fact" | "preference" | "experience" | "decision";

export const MEMORY_TYPES: MemoryType[] = ["fact", "preference", "experience", "decision"];

export interface MemoryInput {
  type: MemoryType;
  title: string;
  content: string;
  /** high 记忆在开场视图中注入全文 */
  priority?: "high" | "normal";
  /** 溯源：来源会话的标识（session 文件相对路径或会话 id） */
  derivedFrom?: string;
}

export interface MemoryEntry {
  sourcePath: string;
  title: string;
  type: string;
  priority: string;
  status: string;
  derivedFrom: string;
  updatedAt: string;
}

/** 标题转文件名：去掉路径非法字符，空白折叠为连字符 */
function slugify(title: string): string {
  const cleaned = title
    .replace(/[\\/:*?"<>|#]/g, "")
    .trim()
    .replace(/\s+/g, "-");
  return cleaned === "" ? `memory-${Date.now()}` : cleaned.slice(0, 80);
}

/** 保存（或覆盖同名）记忆文件并立即索引，返回相对路径 */
export async function saveMemory(config: KnowledgeConfig, input: MemoryInput): Promise<string> {
  const relPath = `memories/${input.type}/${slugify(input.title)}.md`;
  const absPath = path.join(config.vaultDir, relPath);
  const now = new Date().toISOString();

  let createdAt = now;
  try {
    const existing = matter(await fs.readFile(absPath, "utf8"));
    if (typeof existing.data.created === "string") createdAt = existing.data.created;
  } catch {
    // 新文件
  }

  const frontmatter: Record<string, string> = {
    title: input.title,
    type: input.type,
    priority: input.priority ?? "normal",
    status: "active",
    created: createdAt,
    updated: now,
  };
  if (input.derivedFrom) frontmatter.derived_from = input.derivedFrom;

  await fs.mkdir(path.dirname(absPath), { recursive: true });
  await fs.writeFile(absPath, matter.stringify(`\n${input.content.trim()}\n`, frontmatter), "utf8");
  await ingestFile(config, relPath);
  return relPath;
}

/** 列出已索引的 active 记忆（按 priority、更新时间排序） */
export async function listMemories(config: KnowledgeConfig): Promise<MemoryEntry[]> {
  const result = await getPool(config).query(
    `SELECT source_path, title, meta, indexed_at FROM documents
     WHERE kind = 'memory' AND meta->>'status' IS DISTINCT FROM 'superseded'
     ORDER BY (meta->>'priority' = 'high') DESC, meta->>'updated' DESC NULLS LAST`,
  );
  return result.rows.map((row) => ({
    sourcePath: row.source_path,
    title: row.title,
    type: String(row.meta?.type ?? ""),
    priority: String(row.meta?.priority ?? "normal"),
    status: String(row.meta?.status ?? "active"),
    derivedFrom: String(row.meta?.derived_from ?? ""),
    updatedAt: String(row.meta?.updated ?? ""),
  }));
}

/** 读取记忆正文（不含 frontmatter） */
async function readMemoryBody(config: KnowledgeConfig, relPath: string): Promise<string> {
  const raw = await fs.readFile(path.join(config.vaultDir, relPath), "utf8");
  return matter(raw).content.trim();
}

/**
 * 开场压缩视图：高优先级记忆全文 + 其余一行索引目录。
 * 预算按字符/4 粗估 token；超预算时先砍索引行，再砍全文条目。
 */
export async function buildMemoryView(config: KnowledgeConfig): Promise<string> {
  const memories = await listMemories(config);
  if (memories.length === 0) return "";
  const budgetChars = config.injectTokenBudget * 4;

  const fullSections: string[] = [];
  const indexLines: string[] = [];
  for (const memory of memories) {
    if (memory.priority === "high") {
      try {
        const body = await readMemoryBody(config, memory.sourcePath);
        fullSections.push(`### ${memory.title} (${memory.type})\n${body}`);
        continue;
      } catch {
        // 文件被手工删除等，降级为索引行
      }
    }
    indexLines.push(`- ${memory.title} (${memory.type}, ${memory.sourcePath})`);
  }

  const parts: string[] = ["## 记忆"];
  let used = parts[0].length;
  for (const section of fullSections) {
    if (used + section.length > budgetChars) break;
    parts.push(section);
    used += section.length;
  }
  if (indexLines.length > 0) {
    const header = "### 其他记忆索引（可用检索工具按路径深入）";
    const kept: string[] = [];
    for (const line of indexLines) {
      if (used + header.length + line.length > budgetChars) break;
      kept.push(line);
      used += line.length;
    }
    if (kept.length > 0) parts.push(`${header}\n${kept.join("\n")}`);
  }
  return parts.join("\n\n");
}

const REFLECT_PROMPT = `你是记忆提取助手。从给定的会话内容中提取值得长期记住的信息。

提取范围（type 取值）：
- fact：用户/项目的客观事实（环境、配置、身份、约定）
- preference：用户表达的偏好与习惯
- experience：踩坑教训、调试结论、可复用的做法
- decision：明确做出的技术/方案决策及理由

要求：
- 只提取跨会话仍有价值的信息；一次性的操作细节、寒暄、过程性内容不提取
- 每条记忆自包含：脱离本会话也能看懂，写清主语和上下文
- content 用 1-4 句陈述句，不写代码大段
- 与"已有记忆标题"语义重复的不再提取
- priority 仅对影响每次会话行为的关键信息用 high，其余 normal
- 没有值得提取的内容时返回空数组

只输出 JSON，格式：
{"memories":[{"type":"fact|preference|experience|decision","title":"简短标题","content":"...","priority":"high|normal"}]}`;

interface ReflectedMemory {
  type: MemoryType;
  title: string;
  content: string;
  priority: "high" | "normal";
}

/** 解析反思输出（容忍围栏与杂文） */
export function parseReflection(raw: string): ReflectedMemory[] {
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start === -1 || end <= start) return [];
  const parsed = JSON.parse(raw.slice(start, end + 1)) as { memories?: unknown };
  if (!Array.isArray(parsed.memories)) return [];
  const results: ReflectedMemory[] = [];
  for (const item of parsed.memories as Array<Record<string, unknown>>) {
    if (
      typeof item?.title === "string" &&
      item.title.trim() !== "" &&
      typeof item?.content === "string" &&
      item.content.trim() !== "" &&
      MEMORY_TYPES.includes(item.type as MemoryType)
    ) {
      results.push({
        type: item.type as MemoryType,
        title: item.title.trim(),
        content: item.content.trim(),
        priority: item.priority === "high" ? "high" : "normal",
      });
    }
  }
  return results;
}

/** 会话反思：LLM 提取记忆并直接入库，返回保存的相对路径列表 */
export async function reflectAndSave(
  config: KnowledgeConfig,
  conversationText: string,
  derivedFrom: string,
): Promise<string[]> {
  if (conversationText.trim() === "") return [];
  const existing = await listMemories(config);
  const existingTitles =
    existing.length > 0
      ? `\n\n已有记忆标题（语义重复的不要再提取）：\n${existing.map((m) => `- ${m.title}`).join("\n")}`
      : "";
  const raw = await arkChat(config, config.ark.extractionModel, [
    { role: "system", content: REFLECT_PROMPT + existingTitles },
    { role: "user", content: conversationText.slice(0, 24000) },
  ]);
  const memories = parseReflection(raw);
  const saved: string[] = [];
  for (const memory of memories) {
    saved.push(await saveMemory(config, { ...memory, derivedFrom }));
  }
  return saved;
}
