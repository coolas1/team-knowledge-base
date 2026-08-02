import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import matter from "gray-matter";
import type { KnowledgeConfig } from "../config.ts";
import { derivedDir } from "../config.ts";
import { chunkMarkdown } from "./chunk.ts";
import { convertToShadow, isSupported } from "./convert/index.ts";
import { imageMime } from "./convert/image.ts";
import { getPool, toVectorLiteral } from "./db.ts";
import { embed, embedImage } from "./embedding.ts";
import { cleanupOrphanNodes } from "./graph-build.ts";
import { runCypher } from "./graph.ts";

/**
 * 增量摄取管线：vault 源文件 → 影子 md → chunk → BM25/向量入库。
 * 增量判据：源文件字节 sha256（转换前判断，避免重复跑 VLM/解析）。
 * 图谱抽取不在此处执行，只置 graph_jobs 待处理（kb-07 异步消费）。
 */

export interface IngestStats {
  scanned: number;
  ingested: number;
  unchanged: number;
  removed: number;
  failed: Array<{ path: string; error: string }>;
}

/** 扫描时跳过的目录/文件 */
function isExcluded(name: string, derivedDirName: string): boolean {
  return name.startsWith(".") || name === "node_modules" || name === derivedDirName;
}

/** 递归收集 vault 内受支持的源文件（相对路径，正斜杠） */
export async function scanVault(config: KnowledgeConfig): Promise<string[]> {
  const results: string[] = [];
  async function walk(dirAbs: string, dirRel: string): Promise<void> {
    const entries = await fs.readdir(dirAbs, { withFileTypes: true });
    for (const entry of entries) {
      if (isExcluded(entry.name, config.derivedDirName)) continue;
      const rel = dirRel === "" ? entry.name : `${dirRel}/${entry.name}`;
      if (entry.isDirectory()) {
        await walk(path.join(dirAbs, entry.name), rel);
      } else if (entry.isFile() && isSupported(entry.name)) {
        results.push(rel);
      }
    }
  }
  await walk(config.vaultDir, "");
  return results;
}

/** 相对路径推断文档类别：memories/ 下是记忆，sessions/ 下是会话档案，其余是文档 */
export function kindOf(relPath: string): string {
  if (relPath.startsWith("memories/")) return "memory";
  if (relPath.startsWith("sessions/")) return "session";
  return "doc";
}

function shadowPathOf(relPath: string): string {
  return `${relPath}.md`;
}

async function sha256File(absPath: string): Promise<string> {
  const buffer = await fs.readFile(absPath);
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

export type IngestResult = "ingested" | "unchanged" | "skipped";

/** 摄取单个文件；force 跳过 hash 短路 */
export async function ingestFile(
  config: KnowledgeConfig,
  relPath: string,
  force = false,
): Promise<IngestResult> {
  const pool = getPool(config);
  const absPath = path.join(config.vaultDir, relPath);
  const contentHash = await sha256File(absPath);
  const stat = await fs.stat(absPath);

  if (!force) {
    const existing = await pool.query("SELECT content_hash FROM documents WHERE source_path = $1", [
      relPath,
    ]);
    if (existing.rows[0]?.content_hash === contentHash) return "unchanged";
  }

  const shadow = await convertToShadow(config, absPath, relPath);
  if (!shadow || shadow.markdown.trim() === "") return "skipped";

  // md 文件的 frontmatter 进 meta，正文用于索引
  let meta: Record<string, unknown> = {};
  let markdown = shadow.markdown;
  if (/\.(md|markdown)$/i.test(relPath)) {
    const parsed = matter(markdown);
    meta = parsed.data;
    markdown = parsed.content.trim();
    if (markdown === "") return "skipped";
  }
  const title =
    (typeof meta.title === "string" && meta.title) || shadow.title || path.basename(relPath);

  // 影子文件落盘（镜像目录树）
  const shadowRel = shadowPathOf(relPath);
  const shadowAbs = path.join(derivedDir(config), shadowRel);
  await fs.mkdir(path.dirname(shadowAbs), { recursive: true });
  await fs.writeFile(shadowAbs, markdown, "utf8");

  const chunks = chunkMarkdown(markdown);
  if (chunks.length === 0) return "skipped";

  // 图片文件用原图跨模态嵌入（更强），失败回退文本描述嵌入
  let embeddings: number[][];
  if (shadow.assetPath && imageMime(path.extname(relPath))) {
    try {
      const buffer = await fs.readFile(absPath);
      const mime = imageMime(path.extname(relPath));
      const imageVector = await embedImage(config, `data:${mime};base64,${buffer.toString("base64")}`);
      embeddings = chunks.map(() => imageVector);
    } catch {
      embeddings = await embed(config, chunks.map((c) => c.content));
    }
  } else {
    embeddings = await embed(config, chunks.map((c) => c.content));
  }

  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const upsert = await client.query(
      `INSERT INTO documents (source_path, kind, title, content_hash, shadow_path, mtime_ms, meta, embedding_model)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
       ON CONFLICT (source_path) DO UPDATE SET
         kind = EXCLUDED.kind, title = EXCLUDED.title, content_hash = EXCLUDED.content_hash,
         shadow_path = EXCLUDED.shadow_path, mtime_ms = EXCLUDED.mtime_ms, meta = EXCLUDED.meta,
         embedding_model = EXCLUDED.embedding_model, indexed_at = now()
       RETURNING id`,
      [
        relPath,
        kindOf(relPath),
        title,
        contentHash,
        shadowRel,
        Math.round(stat.mtimeMs),
        JSON.stringify(meta),
        config.ark.embeddingModel,
      ],
    );
    const docId: number = upsert.rows[0].id;
    await client.query("DELETE FROM chunks WHERE doc_id = $1", [docId]);
    for (let i = 0; i < chunks.length; i++) {
      await client.query(
        `INSERT INTO chunks (doc_id, seq, heading, content, asset_path, embedding)
         VALUES ($1, $2, $3, $4, $5, $6::vector)`,
        [
          docId,
          chunks[i].seq,
          chunks[i].heading,
          chunks[i].content,
          shadow.assetPath ?? "",
          toVectorLiteral(embeddings[i]),
        ],
      );
    }
    await client.query(
      `INSERT INTO graph_jobs (doc_id, status) VALUES ($1, 'pending')
       ON CONFLICT (doc_id) DO UPDATE SET status = 'pending', attempts = 0, last_error = '', updated_at = now()`,
      [docId],
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

/** 删除已不存在于 vault 的文档：索引行（级联 chunks）、影子文件、图谱派生 */
async function removeDocument(config: KnowledgeConfig, relPath: string): Promise<void> {
  const pool = getPool(config);
  await pool.query("DELETE FROM documents WHERE source_path = $1", [relPath]);
  await fs.rm(path.join(derivedDir(config), shadowPathOf(relPath)), { force: true });
  await runCypher(config, "MATCH ()-[r:REL]->() WHERE r.source_path = $path DELETE r", {
    path: relPath,
  });
  await runCypher(config, "MATCH (d:Doc {path: $path}) DETACH DELETE d", { path: relPath });
}

/** 全量增量扫描：新增/变更文件摄取，消失文件清理 */
export async function ingestVault(
  config: KnowledgeConfig,
  options: { force?: boolean; onProgress?: (message: string) => void } = {},
): Promise<IngestStats> {
  const stats: IngestStats = { scanned: 0, ingested: 0, unchanged: 0, removed: 0, failed: [] };
  const files = await scanVault(config);
  stats.scanned = files.length;

  for (const relPath of files) {
    try {
      const result = await ingestFile(config, relPath, options.force);
      if (result === "ingested") {
        stats.ingested++;
        options.onProgress?.(`已索引: ${relPath}`);
      } else {
        stats.unchanged++;
      }
    } catch (error) {
      stats.failed.push({
        path: relPath,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  // 清理消失的文件（session 档案由情景层管线独立管理，不在此清理）
  const known = await getPool(config).query(
    "SELECT source_path FROM documents WHERE kind != 'session'",
  );
  const current = new Set(files);
  for (const row of known.rows) {
    if (!current.has(row.source_path)) {
      await removeDocument(config, row.source_path);
      stats.removed++;
      options.onProgress?.(`已移除: ${row.source_path}`);
    }
  }
  if (stats.removed > 0) await cleanupOrphanNodes(config);
  return stats;
}
