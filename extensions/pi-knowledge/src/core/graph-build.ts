import path from "node:path";
import type { KnowledgeConfig } from "../config.ts";
import { arkChat } from "./ark.ts";
import { getPool, toVectorLiteral } from "./db.ts";
import { embed } from "./embedding.ts";
import { runCypher } from "./graph.ts";

/**
 * 图谱构建：消费 graph_jobs 队列，把文档派生进 Neo4j。
 * - 结构边：Doc 节点、目录层级（Dir/IN_DIR）、时间（Month/MODIFIED_IN）
 * - 语义边：LLM 抽取实体（Entity/MENTIONS）与实体间关系（REL）
 * 图谱是纯派生索引：所有本文档产生的边可按 path 清除重建，孤儿实体自动回收。
 */

const MAX_ATTEMPTS = 3;
/** 单文档送入抽取模型的内容上限（字符） */
const EXTRACT_INPUT_CHARS = 8000;

export interface GraphBuildStats {
  processed: number;
  done: number;
  failed: Array<{ path: string; error: string }>;
}

interface ExtractedEntity {
  name: string;
  type: string;
  chunks: number[];
  description: string;
}

interface ExtractedRelation {
  source: string;
  target: string;
  relation: string;
  description: string;
}

interface Extraction {
  entities: ExtractedEntity[];
  relations: ExtractedRelation[];
}

/** 13 种节点类型 */
const ENTITY_TYPES = [
  "Person", "Organization", "Project", "Technology", "System",
  "Document", "Event", "Role", "Metric", "Feature",
  "Milestone", "Risk", "Task",
] as const;

/** 推荐关系类型（半开放：LLM 可自创，维护时归一化） */
const RELATION_TYPES = [
  "WORKS_AT", "HAS_ROLE", "REPORTS_TO", "PARTICIPATES", "RESPONSIBLE",
  "DEPENDS_ON", "DEPLOYED_ON", "PART_OF", "IMPLEMENTS", "REQUIRES",
  "AFFECTS", "ASSIGNED_TO", "BLOCKS", "DECIDED_IN", "HAS_METRIC",
  "VERSION_OF", "DERIVED_FROM", "RELATES_TO",
] as const;

const EXTRACT_PROMPT = `你是知识图谱构建助手。从给定文档中抽取实体和实体间关系。
文档内容按 [chunk:N] 标记分段，N 为段落序号。

## 实体类型（13 种，必须从中选择）
Person（人物）、Organization（组织/部门）、Project（项目/专项）、
Technology（通用技术/工具/框架）、System（具体系统/服务实例）、
Document（文档/交付物）、Event（事件/决策/会议决议）、
Role（角色/职位）、Metric（数值/指标/金额）、
Feature（功能/需求）、Milestone（里程碑/版本节点）、
Risk（风险/问题）、Task（任务/Action Item）

## 抽取规则
- 只抽取对检索有价值的具体实体；忽略"文档""系统""方法"等泛化词
- 实体名用原文中最完整、最常用的称呼，不翻译、不缩写
- 每个实体标注它出现在哪些 chunk 中（chunks 数组，从 0 开始）
- Technology 是通用技术（PostgreSQL、Kafka），System 是具体部署实例（星辰ERP V8.2、OA系统）
- Metric 节点需包含具体数值（如"预算480万""许可费380万/年"）
- 最多 30 个实体、30 条关系；无可抽取内容时返回空数组

## 关系规则
- 优先使用推荐关系类型：${RELATION_TYPES.join(", ")}
- 如果推荐列表中没有合适的，可自创简短中文动词短语（如"对比""包含"）
- 关系两端必须都出现在 entities 列表中
- 关系用大写英文（推荐列表中的）或简短中文（自创的）

## 描述规则
- 每个实体附带 description：10-25 字简要概括（如"星辰科技技术总监，负责数据中台"）
- 每条关系附带 description：10-20 字说明关系含义（如"李明辉向王建国汇报运维工作"）

只输出 JSON，不要解释，格式：
{"entities":[{"name":"...","type":"...","chunks":[0,2],"description":"..."}],"relations":[{"source":"...","target":"...","relation":"...","description":"..."}]}`;

/** 合法实体类型集合（小写匹配后规范化） */
const TYPE_MAP: Record<string, string> = Object.fromEntries(
  ENTITY_TYPES.map((t) => [t.toLowerCase(), t]),
);
// 兼容旧类型名
TYPE_MAP["person"] = "Person";
TYPE_MAP["org"] = "Organization";
TYPE_MAP["project"] = "Project";
TYPE_MAP["tech"] = "Technology";
TYPE_MAP["concept"] = "Technology";

/** 修复截断的 JSON：找到最后一个完整的 } 或 ] 截断 */
function repairTruncatedJson(raw: string): string {
  const start = raw.indexOf("{");
  if (start === -1) return raw;
  let text = raw.slice(start);
  // 如果已经是合法 JSON，直接返回
  try { JSON.parse(text); return text; } catch { /* 需要修复 */ }
  // 从末尾向前找最后一个合法的关闭点
  // 策略：截断到最后一个 "}" 或 "]"  然后补全括号
  const lastBrace = text.lastIndexOf("}");
  const lastBracket = text.lastIndexOf("]");
  const cutTo = Math.max(lastBrace, lastBracket);
  if (cutTo > 0) {
    text = text.slice(0, cutTo + 1);
  }
  // 补全可能缺失的 ] 和 }
  const openBraces = (text.match(/\{/g) ?? []).length;
  const closeBraces = (text.match(/\}/g) ?? []).length;
  const openBrackets = (text.match(/\[/g) ?? []).length;
  const closeBrackets = (text.match(/\]/g) ?? []).length;
  // 如果数组未关闭，先补 ]
  if (openBrackets > closeBrackets) text += "]";
  // 如果对象未关闭，补 }
  const finalOpen = (text.match(/\{/g) ?? []).length;
  const finalClose = (text.match(/\}/g) ?? []).length;
  for (let i = 0; i < finalOpen - finalClose; i++) text += "}";
  return text;
}

/** 解析模型输出（容忍代码围栏、前后杂文、截断），失败抛错走重试 */
export function parseExtraction(raw: string): Extraction {
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start === -1 || end <= start) throw new Error(`抽取输出不含 JSON: ${raw.slice(0, 200)}`);
  let jsonStr = raw.slice(start, end + 1);
  // 尝试解析，失败则修复截断
  let parsed: { entities?: unknown; relations?: unknown };
  try {
    parsed = JSON.parse(jsonStr);
  } catch {
    jsonStr = repairTruncatedJson(raw);
    parsed = JSON.parse(jsonStr); // 修复后仍失败则抛错走重试
  }
  const entities: ExtractedEntity[] = [];
  if (Array.isArray(parsed.entities)) {
    for (const item of parsed.entities as Array<Record<string, unknown>>) {
      if (typeof item?.name === "string" && item.name.trim() !== "") {
        const rawType = typeof item.type === "string" ? item.type.trim().toLowerCase() : "";
        const type = TYPE_MAP[rawType] ?? "Technology";
        const chunks: number[] = Array.isArray(item.chunks)
          ? (item.chunks as unknown[]).filter(
              (c): c is number => typeof c === "number" && Number.isInteger(c) && c >= 0,
            )
          : [];
        const description = typeof item.description === "string" ? item.description.trim() : "";
        entities.push({ name: item.name.trim(), type, chunks, description });
      }
    }
  }
  const names = new Set(entities.map((e) => e.name));
  const relations: ExtractedRelation[] = [];
  if (Array.isArray(parsed.relations)) {
    for (const item of parsed.relations as Array<Record<string, unknown>>) {
      if (
        typeof item?.source === "string" &&
        typeof item?.target === "string" &&
        typeof item?.relation === "string" &&
        names.has(item.source.trim()) &&
        names.has(item.target.trim()) &&
        item.source.trim() !== item.target.trim()
      ) {
        const description = typeof item.description === "string" ? item.description.trim() : "";
        relations.push({
          source: item.source.trim(),
          target: item.target.trim(),
          relation: item.relation.trim(),
          description,
        });
      }
    }
  }
  return { entities, relations };
}

async function extractFromContent(config: KnowledgeConfig, content: string): Promise<Extraction> {
  const raw = await arkChat(config, config.ark.extractionModel, [
    { role: "system", content: EXTRACT_PROMPT },
    { role: "user", content: content.slice(0, EXTRACT_INPUT_CHARS) },
  ]);
  return parseExtraction(raw);
}

/** 清除本文档此前派生的边（MENTIONS、带 source_path 的 REL） */
async function clearDocDerivation(config: KnowledgeConfig, relPath: string): Promise<void> {
  await runCypher(config, "MATCH (d:Doc {path: $path})-[r:MENTIONS]->() DELETE r", {
    path: relPath,
  });
  await runCypher(config, "MATCH ()-[r:REL]->() WHERE r.source_path = $path DELETE r", {
    path: relPath,
  });
}

/** 删除不再被任何边连接的孤儿节点（实体/目录/月份） */
export async function cleanupOrphanNodes(config: KnowledgeConfig): Promise<void> {
  await runCypher(config, "MATCH (e:Entity) WHERE NOT (e)--() DELETE e");
  // 目录链是级联的：删空叶子目录后上级才成孤儿，循环直到不再有删除
  for (;;) {
    const result = await runCypher(
      config,
      "MATCH (n) WHERE (n:Dir OR n:Month) AND NOT (n)--() DELETE n RETURN count(n) AS c",
    );
    if (Number(result[0]?.c ?? 0) === 0) break;
  }
}

/** 结构边：Doc 节点属性、目录层级、修改月份 */
async function buildStructure(
  config: KnowledgeConfig,
  relPath: string,
  title: string,
  kind: string,
  mtimeMs: number,
): Promise<void> {
  await runCypher(
    config,
    `MERGE (d:Doc {path: $path})
     SET d.title = $title, d.kind = $kind, d.mtime_ms = $mtimeMs`,
    { path: relPath, title, kind, mtimeMs },
  );

  // 目录链：a/b/c.md → Dir(a) ← Dir(a/b) ← Doc
  const dirPath = path.posix.dirname(relPath);
  if (dirPath !== ".") {
    const segments = dirPath.split("/");
    let parent = "";
    for (let i = 0; i < segments.length; i++) {
      const current = segments.slice(0, i + 1).join("/");
      await runCypher(config, "MERGE (dir:Dir {path: $path}) SET dir.name = $name", {
        path: current,
        name: segments[i],
      });
      if (parent !== "") {
        await runCypher(
          config,
          `MATCH (child:Dir {path: $child}), (parent:Dir {path: $parent})
           MERGE (child)-[:IN_DIR]->(parent)`,
          { child: current, parent },
        );
      }
      parent = current;
    }
    await runCypher(
      config,
      `MATCH (d:Doc {path: $path}), (dir:Dir {path: $dir})
       MERGE (d)-[:IN_DIR]->(dir)`,
      { path: relPath, dir: dirPath },
    );
  }

  const ym = new Date(mtimeMs).toISOString().slice(0, 7);
  await runCypher(
    config,
    `MATCH (d:Doc {path: $path})
     MERGE (m:Month {ym: $ym})
     MERGE (d)-[:MODIFIED_IN]->(m)`,
    { path: relPath, ym },
  );
}

/** 溯源边：提炼记忆 → 来源会话（目标 Doc 节点缺失时先建占位，会话摄取后补全属性） */
async function buildProvenance(
  config: KnowledgeConfig,
  relPath: string,
  derivedFrom: string,
): Promise<void> {
  await runCypher(
    config,
    `MATCH (d:Doc {path: $path})
     MERGE (s:Doc {path: $from})
     MERGE (d)-[:DERIVED_FROM]->(s)`,
    { path: relPath, from: derivedFrom },
  );
}

/** 语义边：实体、提及（携带 chunk 序号）、实体间关系（边携带 source_path 供重建/清除） */
async function buildSemantics(
  config: KnowledgeConfig,
  relPath: string,
  extraction: Extraction,
): Promise<void> {
  for (const entity of extraction.entities) {
    await runCypher(
      config,
      `MERGE (e:Entity {name: $name})
       ON CREATE SET e.type = $type
       SET e.type = $type
       WITH e
       MATCH (d:Doc {path: $path})
       MERGE (d)-[m:MENTIONS]->(e)
       SET m.chunks = $chunks`,
      { name: entity.name, type: entity.type, path: relPath, chunks: entity.chunks },
    );
  }
  for (const relation of extraction.relations) {
    await runCypher(
      config,
      `MATCH (a:Entity {name: $source}), (b:Entity {name: $target})
       MERGE (a)-[r:REL {type: $relation, source_path: $path}]->(b)`,
      {
        source: relation.source,
        target: relation.target,
        relation: relation.relation,
        path: relPath,
      },
    );
  }
}

/** 实体/关系向量：embed 描述并写入 ParadeDB（同名实体 MERGE 更新） */
async function buildEntityVectors(
  config: KnowledgeConfig,
  extraction: Extraction,
): Promise<void> {
  const pool = getPool(config);

  // 收集需要 embed 的文本
  const entityTexts = extraction.entities
    .filter((e) => e.description !== "")
    .map((e) => `${e.name} | ${e.type} | ${e.description}`);
  const relationTexts = extraction.relations
    .filter((r) => r.description !== "")
    .map((r) => `${r.source} → ${r.relation} → ${r.target}：${r.description}`);

  const allTexts = [...entityTexts, ...relationTexts];
  if (allTexts.length === 0) return;

  const embeddings = await embed(config, allTexts);

  // 写入 entity_vectors（同名 MERGE）
  let idx = 0;
  for (const entity of extraction.entities) {
    if (entity.description === "") continue;
    const vec = toVectorLiteral(embeddings[idx++]);
    await pool.query(
      `INSERT INTO entity_vectors (name, type, description, embedding)
       VALUES ($1, $2, $3, $4::vector)
       ON CONFLICT (name) DO UPDATE SET type = $2, description = $3, embedding = $4::vector`,
      [entity.name, entity.type, entity.description, vec],
    );
  }

  // 写入 relation_vectors（同三元组 MERGE）
  for (const relation of extraction.relations) {
    if (relation.description === "") continue;
    const vec = toVectorLiteral(embeddings[idx++]);
    await pool.query(
      `INSERT INTO relation_vectors (head, rel_type, tail, description, embedding)
       VALUES ($1, $2, $3, $4, $5::vector)
       ON CONFLICT (head, rel_type, tail) DO UPDATE SET description = $4, embedding = $5::vector`,
      [relation.source, relation.relation, relation.target, relation.description, vec],
    );
  }
}

/** 处理单个文档：清旧派生 → 结构边 → LLM 抽取 → 语义边 → 实体向量 */
async function buildDocGraph(config: KnowledgeConfig, docId: number): Promise<string> {
  const pool = getPool(config);
  const doc = await pool.query(
    "SELECT source_path, title, kind, mtime_ms, meta FROM documents WHERE id = $1",
    [docId],
  );
  if (doc.rows.length === 0) throw new Error(`文档不存在: id=${docId}`);
  const { source_path: relPath, title, kind, mtime_ms: mtimeMs, meta } = doc.rows[0];

  const chunks = await pool.query(
    "SELECT seq, heading, content FROM chunks WHERE doc_id = $1 ORDER BY seq",
    [docId],
  );
  // 带 chunk 标记拼接，让 LLM 能标注实体出现在哪个 chunk
  const content = chunks.rows
    .map((row) => {
      const header = row.heading ? `## ${row.heading}\n` : "";
      return `[chunk:${row.seq}]\n${header}${row.content}`;
    })
    .join("\n\n");

  await clearDocDerivation(config, relPath);
  await buildStructure(config, relPath, title, kind, Number(mtimeMs));
  const derivedFrom = typeof meta?.derived_from === "string" ? meta.derived_from : "";
  if (derivedFrom !== "") await buildProvenance(config, relPath, derivedFrom);
  const extraction = await extractFromContent(config, content);
  await buildSemantics(config, relPath, extraction);
  await buildEntityVectors(config, extraction);
  return relPath;
}

/** 消费 pending 任务；limit 控制单轮处理量（LLM 调用较慢，维护任务分批跑） */
export async function processGraphJobs(
  config: KnowledgeConfig,
  options: { limit?: number; onProgress?: (message: string) => void } = {},
): Promise<GraphBuildStats> {
  const pool = getPool(config);
  const limit = options.limit ?? 50;
  const stats: GraphBuildStats = { processed: 0, done: 0, failed: [] };

  const claimed = await pool.query(
    `UPDATE graph_jobs SET status = 'running', updated_at = now()
     WHERE doc_id IN (
       SELECT doc_id FROM graph_jobs
       WHERE status = 'pending' AND attempts < $1
       ORDER BY updated_at
       LIMIT $2
       FOR UPDATE SKIP LOCKED
     )
     RETURNING doc_id`,
    [MAX_ATTEMPTS, limit],
  );

  for (const row of claimed.rows) {
    const docId: number = Number(row.doc_id);
    stats.processed++;
    try {
      const relPath = await buildDocGraph(config, docId);
      await pool.query(
        "UPDATE graph_jobs SET status = 'done', last_error = '', updated_at = now() WHERE doc_id = $1",
        [docId],
      );
      stats.done++;
      options.onProgress?.(`图谱已更新: ${relPath}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const failed = await pool.query(
        `UPDATE graph_jobs
         SET attempts = attempts + 1,
             status = CASE WHEN attempts + 1 >= $2 THEN 'failed' ELSE 'pending' END,
             last_error = $3, updated_at = now()
         WHERE doc_id = $1
         RETURNING (SELECT source_path FROM documents WHERE id = $1) AS source_path`,
        [docId, MAX_ATTEMPTS, message.slice(0, 500)],
      );
      stats.failed.push({ path: failed.rows[0]?.source_path ?? `id=${docId}`, error: message });
    }
  }

  if (stats.done > 0) await cleanupOrphanNodes(config);
  return stats;
}
