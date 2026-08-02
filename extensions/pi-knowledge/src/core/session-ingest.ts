import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { KnowledgeConfig } from "../config.ts";
import { chunkMarkdown } from "./chunk.ts";
import { getPool, toVectorLiteral } from "./db.ts";
import { embed } from "./embedding.ts";
import { runCypher } from "./graph.ts";

/**
 * 情景层摄取：pi session JSONL → 对话文本 → BM25/向量双路索引。
 * - 只保留 user 文本与 assistant 回复文本，剔除 toolResult/toolCall/thinking
 * - kind='session'，永不注入上下文，仅供检索命中兜底
 * - 不参与图谱抽取（不写 graph_jobs）；source_path 用虚拟命名空间 sessions/<dir>/<file>
 * - vault 增量扫描不管理 session 文档，本管线自带消失清理
 */

export interface SessionIngestStats {
  scanned: number;
  ingested: number;
  unchanged: number;
  skipped: number;
  removed: number;
  failed: Array<{ path: string; error: string }>;
}

/** pi 默认 session 根目录（所有 cwd 的会话都在其子目录下） */
export function defaultSessionRoot(): string {
  return path.join(os.homedir(), ".pi", "agent", "sessions");
}

/** session 文件 → 虚拟 source_path（跨平台正斜杠） */
export function sessionSourcePath(rootDir: string, absPath: string): string {
  return `sessions/${path.relative(rootDir, absPath).split(path.sep).join("/")}`;
}

/** 会话文件 → 溯源用虚拟路径（不在默认 session 根目录下时降级为文件名） */
export function sessionDerivedFrom(sessionFile: string): string {
  const rel = path.relative(defaultSessionRoot(), sessionFile);
  // 跨盘符时 relative 返回绝对路径而非 .. 前缀，两种都要降级
  if (rel.startsWith("..") || path.isAbsolute(rel)) {
    return `sessions/${path.basename(sessionFile)}`;
  }
  return `sessions/${rel.split(path.sep).join("/")}`;
}

function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((part): part is { type: string; text: string } => {
      return (
        typeof part === "object" &&
        part !== null &&
        (part as Record<string, unknown>).type === "text" &&
        typeof (part as Record<string, unknown>).text === "string"
      );
    })
    .map((part) => part.text)
    .join("\n");
}

/** 剥离 harness 注入的提醒块，保留用户真实输入 */
function stripInjected(text: string): string {
  return text.replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, "").trim();
}

export interface ExtractedSession {
  sessionId: string;
  title: string;
  markdown: string;
}

/** JSONL → 对话 markdown；无有效用户消息时返回 null */
export function extractSessionText(jsonl: string): ExtractedSession | null {
  let sessionId = "";
  let name = "";
  let firstUserText = "";
  const turns: string[] = [];

  for (const line of jsonl.split("\n")) {
    if (line.trim() === "") continue;
    let entry: Record<string, unknown>;
    try {
      entry = JSON.parse(line);
    } catch {
      continue; // 尾部半行等损坏行
    }
    if (entry.type === "session" && typeof entry.id === "string") {
      sessionId = entry.id;
    } else if (entry.type === "session_info" && typeof entry.name === "string") {
      name = entry.name;
    } else if (entry.type === "message") {
      const message = entry.message as Record<string, unknown> | undefined;
      if (!message) continue;
      if (message.role === "user") {
        const text = stripInjected(textOf(message.content));
        if (text === "") continue;
        if (firstUserText === "") firstUserText = text;
        turns.push(`**User:**\n${text}`);
      } else if (message.role === "assistant") {
        const text = textOf(message.content).trim();
        if (text !== "") turns.push(`**Assistant:**\n${text}`);
      }
      // toolResult 整体剔除
    }
  }

  if (firstUserText === "") return null;
  const title = name !== "" ? name : firstUserText.replace(/\s+/g, " ").slice(0, 60);
  return { sessionId, title, markdown: turns.join("\n\n") };
}

/** 摄取单个 session 文件（方案 C：尾部重做增量摄取） */
export async function ingestSessionFile(
  config: KnowledgeConfig,
  rootDir: string,
  absPath: string,
): Promise<"ingested" | "unchanged" | "skipped"> {
  const pool = getPool(config);
  const sourcePath = sessionSourcePath(rootDir, absPath);
  const raw = await fs.readFile(absPath, "utf8");
  const contentHash = crypto.createHash("sha256").update(raw).digest("hex");

  const existing = await pool.query(
    "SELECT id, content_hash, meta FROM documents WHERE source_path = $1",
    [sourcePath],
  );
  if (existing.rows[0]?.content_hash === contentHash) return "unchanged";

  const stat = await fs.stat(absPath);
  const lines = raw.split("\n").filter((l) => l.trim() !== "");
  const prevDoc = existing.rows[0] as { id: number; meta: Record<string, unknown> } | undefined;
  const prevLineCount = Number(prevDoc?.meta?.line_count ?? 0);

  // ─── 增量路径：续聊时只重做尾部 ───
  if (prevDoc && prevLineCount > 0 && lines.length > prevLineCount) {
    const newLines = lines.slice(prevLineCount).join("\n");
    const newExtracted = extractSessionText(newLines);
    const lastChunkRes = await pool.query(
      "SELECT id, seq, content FROM chunks WHERE doc_id = $1 ORDER BY seq DESC LIMIT 1",
      [prevDoc.id],
    );
    if (newExtracted && newExtracted.markdown.trim() !== "" && lastChunkRes.rows.length > 0) {
      const lastChunk = lastChunkRes.rows[0] as { id: number; seq: number; content: string };
      const combined = `${lastChunk.content}\n\n${newExtracted.markdown}`;
      const tailChunks = chunkMarkdown(combined);
      if (tailChunks.length > 0) {
        const embeddings = await embed(config, tailChunks.map((c) => c.content));
        const client = await pool.connect();
        try {
          await client.query("BEGIN");
          await client.query("DELETE FROM chunks WHERE id = $1", [lastChunk.id]);
          for (let i = 0; i < tailChunks.length; i++) {
            await client.query(
              `INSERT INTO chunks (doc_id, seq, heading, content, asset_path, embedding)
               VALUES ($1, $2, $3, $4, '', $5::vector)`,
              [
                prevDoc.id,
                lastChunk.seq + i,
                tailChunks[i].heading,
                tailChunks[i].content,
                toVectorLiteral(embeddings[i]),
              ],
            );
          }
          const newMeta = { ...prevDoc.meta, line_count: lines.length };
          await client.query(
            `UPDATE documents SET content_hash = $1, meta = $2, mtime_ms = $3, indexed_at = now()
             WHERE id = $4`,
            [contentHash, JSON.stringify(newMeta), Math.round(stat.mtimeMs), prevDoc.id],
          );
          await client.query("COMMIT");
        } catch (error) {
          await client.query("ROLLBACK");
          throw error;
        } finally {
          client.release();
        }
        return "ingested";
      }
    }
    // 新增内容无有效文本，仅更新 hash 和 offset
    const newMeta = { ...prevDoc.meta, line_count: lines.length };
    await pool.query("UPDATE documents SET content_hash = $1, meta = $2, mtime_ms = $3 WHERE id = $4", [
      contentHash,
      JSON.stringify(newMeta),
      Math.round(stat.mtimeMs),
      prevDoc.id,
    ]);
    return "unchanged";
  }

  // ─── 全量路径：首次摄取或无法增量时 ───
  const extracted = extractSessionText(raw);
  if (!extracted || extracted.markdown.trim() === "") return "skipped";

  const chunks = chunkMarkdown(extracted.markdown);
  if (chunks.length === 0) return "skipped";
  const embeddings = await embed(config, chunks.map((c) => c.content));

  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const upsert = await client.query(
      `INSERT INTO documents (source_path, kind, title, content_hash, shadow_path, mtime_ms, meta, embedding_model)
       VALUES ($1, 'session', $2, $3, '', $4, $5, $6)
       ON CONFLICT (source_path) DO UPDATE SET
         title = EXCLUDED.title, content_hash = EXCLUDED.content_hash,
         mtime_ms = EXCLUDED.mtime_ms, meta = EXCLUDED.meta,
         embedding_model = EXCLUDED.embedding_model, indexed_at = now()
       RETURNING id`,
      [
        sourcePath,
        extracted.title,
        contentHash,
        Math.round(stat.mtimeMs),
        JSON.stringify({ session_id: extracted.sessionId, file: absPath, line_count: lines.length }),
        config.ark.embeddingModel,
      ],
    );
    const docId: number = upsert.rows[0].id;
    await client.query("DELETE FROM chunks WHERE doc_id = $1", [docId]);
    for (let i = 0; i < chunks.length; i++) {
      await client.query(
        `INSERT INTO chunks (doc_id, seq, heading, content, asset_path, embedding)
         VALUES ($1, $2, $3, $4, '', $5::vector)`,
        [docId, chunks[i].seq, chunks[i].heading, chunks[i].content, toVectorLiteral(embeddings[i])],
      );
    }
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
  return "ingested";
}

async function scanSessionFiles(rootDir: string): Promise<string[]> {
  const results: string[] = [];
  async function walk(dir: string): Promise<void> {
    let entries: import("node:fs").Dirent[];
    try {
      entries = await fs.readdir(dir, { withFileTypes: true });
    } catch {
      return; // 目录不存在
    }
    for (const entry of entries) {
      const abs = path.join(dir, entry.name);
      if (entry.isDirectory()) await walk(abs);
      else if (entry.isFile() && entry.name.endsWith(".jsonl")) results.push(abs);
    }
  }
  await walk(rootDir);
  return results;
}

/** 全量增量扫描 session 根目录：新增/变更摄取，消失清理 */
export async function ingestSessions(
  config: KnowledgeConfig,
  options: { rootDir?: string; onProgress?: (message: string) => void } = {},
): Promise<SessionIngestStats> {
  const rootDir = options.rootDir ?? defaultSessionRoot();
  const stats: SessionIngestStats = {
    scanned: 0,
    ingested: 0,
    unchanged: 0,
    skipped: 0,
    removed: 0,
    failed: [],
  };
  const files = await scanSessionFiles(rootDir);
  stats.scanned = files.length;

  const current = new Set<string>();
  for (const absPath of files) {
    const sourcePath = sessionSourcePath(rootDir, absPath);
    current.add(sourcePath);
    try {
      const result = await ingestSessionFile(config, rootDir, absPath);
      stats[result]++;
      if (result === "ingested") options.onProgress?.(`已索引会话: ${sourcePath}`);
    } catch (error) {
      stats.failed.push({
        path: sourcePath,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  // 清理已删除的会话（图谱中仅可能存在 DERIVED_FROM 占位节点，一并删除）
  const known = await getPool(config).query(
    "SELECT source_path FROM documents WHERE kind = 'session'",
  );
  for (const row of known.rows) {
    if (!current.has(row.source_path)) {
      await getPool(config).query("DELETE FROM documents WHERE source_path = $1", [
        row.source_path,
      ]);
      await runCypher(config, "MATCH (d:Doc {path: $path}) DETACH DELETE d", {
        path: row.source_path,
      });
      stats.removed++;
      options.onProgress?.(`已移除会话: ${row.source_path}`);
    }
  }
  return stats;
}
