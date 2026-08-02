import type { KnowledgeConfig } from "../config.ts";
import { getPool, toVectorLiteral } from "./db.ts";
import { embedOne } from "./embedding.ts";
import { runCypher } from "./graph.ts";

/**
 * 三路并行检索融合：
 * 1. BM25（ParadeDB jieba 分词）
 * 2. 向量（pgvector cosine）
 * 3. 图谱实体匹配（query → 实体名匹配 → 图遍历 → 定位 chunk）
 * RRF 融合排序（不依赖各路分数可比性）
 */

/** 单路召回条数（融合前） */
const RECALL_LIMIT = 20;
/** RRF 平滑常数（标准取值） */
const RRF_K = 60;
/** 图谱路实体匹配上限 */
const GRAPH_ENTITY_LIMIT = 10;
/** 图谱路返回的 chunk 上限 */
const GRAPH_CHUNK_LIMIT = 15;
/** 泛化实体黑名单（出现在几乎所有文档中的实体不作为图谱检索入口） */
const ENTITY_BLACKLIST = new Set([
  "星辰科技", "星辰科技有限公司", "技术架构部", "XINGCHEN TECH",
]);

export interface SearchHit {
  chunkId: number;
  docId: number;
  sourcePath: string;
  title: string;
  kind: string;
  heading: string;
  content: string;
  assetPath: string;
  /** RRF 融合分（图谱补充项为衰减的伴生分） */
  score: number;
  /** 命中信号：bm25 / vector / graph */
  signals: string[];
  /** graph 信号的出处：与已命中文档共享的实体名 */
  viaEntities?: string[];
}

export interface HybridSearchOptions {
  limit?: number;
  /** 限定文档类别（doc/memory/session），缺省不限 */
  kinds?: string[];
  /** 关闭图谱邻居扩展（如图谱尚未构建时） */
  noGraph?: boolean;
}

interface ChunkRow {
  id: string;
  doc_id: string;
  heading: string;
  content: string;
  asset_path: string;
  source_path: string;
  title: string;
  kind: string;
}

function rowToHit(row: ChunkRow): SearchHit {
  return {
    chunkId: Number(row.id),
    docId: Number(row.doc_id),
    sourcePath: row.source_path,
    title: row.title,
    kind: row.kind,
    heading: row.heading,
    content: row.content,
    assetPath: row.asset_path,
    score: 0,
    signals: [],
  };
}

const CHUNK_COLUMNS = `c.id, c.doc_id, c.heading, c.content, c.asset_path,
   d.source_path, d.title, d.kind`;

function kindFilter(kinds: string[] | undefined, paramIndex: number): string {
  return kinds && kinds.length > 0 ? ` AND d.kind = ANY($${paramIndex})` : "";
}

/** 查询侧分词：复用库内 jieba，剔除空白/标点 token（否则 ||| 会因空格 token 全表命中） */
async function tokenizeQuery(config: KnowledgeConfig, query: string): Promise<string[]> {
  const result = await getPool(config).query("SELECT $1::pdb.jieba::text[] AS tokens", [query]);
  const tokens = (result.rows[0]?.tokens ?? []) as string[];
  return [...new Set(tokens.filter((t) => /[\p{L}\p{N}]/u.test(t)))];
}

/** BM25 召回（content 与 heading 任一匹配；token 数组 OR 命中，走 jieba 索引） */
export async function bm25Search(
  config: KnowledgeConfig,
  query: string,
  limit = RECALL_LIMIT,
  kinds?: string[],
): Promise<SearchHit[]> {
  const tokens = await tokenizeQuery(config, query);
  if (tokens.length === 0) return [];
  const params: unknown[] = [tokens, limit];
  if (kinds && kinds.length > 0) params.push(kinds);
  const result = await getPool(config).query(
    `SELECT ${CHUNK_COLUMNS}, pdb.score(c.id) AS raw_score
     FROM chunks c JOIN documents d ON d.id = c.doc_id
     WHERE (c.content ||| $1::text[] OR c.heading ||| $1::text[])${kindFilter(kinds, 3)}
     ORDER BY raw_score DESC
     LIMIT $2`,
    params,
  );
  return result.rows.map((row) => ({
    ...rowToHit(row),
    score: Number(row.raw_score),
    signals: ["bm25"],
  }));
}

/** 向量召回（cosine 相似度） */
export async function vectorSearch(
  config: KnowledgeConfig,
  query: string,
  limit = RECALL_LIMIT,
  kinds?: string[],
): Promise<SearchHit[]> {
  const embedding = await embedOne(config, query);
  const params: unknown[] = [toVectorLiteral(embedding), limit];
  if (kinds && kinds.length > 0) params.push(kinds);
  const result = await getPool(config).query(
    `SELECT ${CHUNK_COLUMNS}, 1 - (c.embedding <=> $1::vector) AS raw_score
     FROM chunks c JOIN documents d ON d.id = c.doc_id
     WHERE c.embedding IS NOT NULL${kindFilter(kinds, 3)}
     ORDER BY c.embedding <=> $1::vector
     LIMIT $2`,
    params,
  );
  return result.rows.map((row) => ({
    ...rowToHit(row),
    score: Number(row.raw_score),
    signals: ["vector"],
  }));
}

/** RRF 融合：score = Σ weight/(k+rank)，同 chunk 多路命中信号合并 */
function fuseRrf(lists: SearchHit[][], weights?: number[]): SearchHit[] {
  const byChunk = new Map<number, SearchHit>();
  for (let i = 0; i < lists.length; i++) {
    const weight = weights?.[i] ?? 1;
    for (let rank = 0; rank < lists[i].length; rank++) {
      const hit = lists[i][rank];
      const contribution = weight / (RRF_K + rank + 1);
      const existing = byChunk.get(hit.chunkId);
      if (existing) {
        existing.score += contribution;
        existing.signals = [...new Set([...existing.signals, ...hit.signals])];
      } else {
        byChunk.set(hit.chunkId, { ...hit, score: contribution });
      }
    }
  }
  return [...byChunk.values()].sort((a, b) => b.score - a.score);
}

/** high-level 实体检索：query embedding → entity_vectors/relation_vectors top-K → 返回实体名列表 */
const HIGH_LEVEL_TOP_K = 5;

async function highLevelEntitySearch(config: KnowledgeConfig, query: string): Promise<string[]> {
  const pool = getPool(config);
  let queryVec: string;
  try {
    queryVec = toVectorLiteral(await embedOne(config, query));
  } catch {
    return [];
  }

  const names = new Set<string>();

  // 搜索实体描述
  try {
    const entityRows = await pool.query(
      `SELECT name, 1 - (embedding <=> $1::vector) AS sim
       FROM entity_vectors
       WHERE embedding IS NOT NULL
       ORDER BY embedding <=> $1::vector
       LIMIT $2`,
      [queryVec, HIGH_LEVEL_TOP_K],
    );
    for (const row of entityRows.rows) {
      if (Number(row.sim) > 0.3 && !ENTITY_BLACKLIST.has(row.name)) {
        names.add(row.name);
      }
    }
  } catch { /* 表不存在时静默降级 */ }

  // 搜索关系描述
  try {
    const relRows = await pool.query(
      `SELECT head, tail, 1 - (embedding <=> $1::vector) AS sim
       FROM relation_vectors
       WHERE embedding IS NOT NULL
       ORDER BY embedding <=> $1::vector
       LIMIT $2`,
      [queryVec, HIGH_LEVEL_TOP_K],
    );
    for (const row of relRows.rows) {
      if (Number(row.sim) > 0.3) {
        if (!ENTITY_BLACKLIST.has(row.head)) names.add(row.head);
        if (!ENTITY_BLACKLIST.has(row.tail)) names.add(row.tail);
      }
    }
  } catch { /* 表不存在时静默降级 */ }

  return [...names].slice(0, GRAPH_ENTITY_LIMIT);
}

/** 图谱实体检索（独立路径）：
 *  low-level：query 分词 → 精确/包含匹配 Entity.name → 图遍历 → chunk
 *  high-level：名称未命中时，query embedding → entity_vectors/relation_vectors top-K → 图遍历 → chunk
 */
export async function graphEntitySearch(
  config: KnowledgeConfig,
  query: string,
  limit = GRAPH_CHUNK_LIMIT,
  kinds?: string[],
): Promise<SearchHit[]> {
  const tokens = await tokenizeQuery(config, query);
  if (tokens.length === 0) return [];

  // 1. low-level：匹配图中已有实体（精确 + 包含）
  let matched: Array<{ name: string; type: string }>;
  try {
    matched = await runCypher(
      config,
      `MATCH (e:Entity)
       WHERE e.name IN $tokens
          OR ANY(t IN $tokens WHERE size(t) >= 2 AND (e.name CONTAINS t OR t CONTAINS e.name))
       RETURN e.name AS name, e.type AS type
       LIMIT ${GRAPH_ENTITY_LIMIT}`,
      { tokens },
    ) as Array<{ name: string; type: string }>;
  } catch {
    return []; // 图谱不可用时静默降级
  }
  let entityNames = matched
    .map((m) => m.name)
    .filter((n) => !ENTITY_BLACKLIST.has(n));

  // 2. high-level fallback：名称未命中时，用向量搜索实体/关系描述
  if (entityNames.length === 0) {
    entityNames = await highLevelEntitySearch(config, query);
    if (entityNames.length === 0) return [];
  }

  // 3. 查找提及这些实体的文档 + chunk 序号
  const mentions = await runCypher(
    config,
    `MATCH (d:Doc)-[m:MENTIONS]->(e:Entity)
     WHERE e.name IN $names
     RETURN d.path AS path, e.name AS entity, m.chunks AS chunks`,
    { names: entityNames },
  ) as Array<{ path: string; entity: string; chunks: number[] | null }>;

  // 4. 1 跳关系遍历：找到与匹配实体有关系的其它实体，再找提及它们的文档
  const relDocs = await runCypher(
    config,
    `MATCH (e:Entity)-[r:REL]-(other:Entity)<-[m2:MENTIONS]-(d2:Doc)
     WHERE e.name IN $names AND NOT other.name IN $names
     RETURN d2.path AS path, other.name AS entity, m2.chunks AS chunks, r.type AS relation
     LIMIT 10`,
    { names: entityNames },
  ) as Array<{ path: string; entity: string; chunks: number[] | null; relation: string }>;

  // 5. 汇总文档路径 + 目标 chunk 序号
  const docChunks = new Map<string, { seqs: Set<number>; entities: string[] }>();
  for (const row of [...mentions, ...relDocs]) {
    const existing = docChunks.get(row.path) ?? { seqs: new Set<number>(), entities: [] };
    if (Array.isArray(row.chunks)) {
      for (const seq of row.chunks) existing.seqs.add(seq);
    }
    if (!existing.entities.includes(row.entity)) existing.entities.push(row.entity);
    docChunks.set(row.path, existing);
  }

  // 6. 从 ParadeDB 拉取具体 chunk 内容
  const hits: SearchHit[] = [];
  for (const [docPath, info] of docChunks) {
    if (hits.length >= limit) break;
    const seqs = [...info.seqs];
    const params: unknown[] = [docPath];
    let seqFilter = "";
    if (seqs.length > 0) {
      seqFilter = " AND c.seq = ANY($2::int[])";
      params.push(seqs);
    }
    if (kinds && kinds.length > 0) {
      params.push(kinds);
      seqFilter += ` AND d.kind = ANY($${params.length}::text[])`;
    }
    const result = await getPool(config).query(
      `SELECT ${CHUNK_COLUMNS}
       FROM chunks c JOIN documents d ON d.id = c.doc_id
       WHERE d.source_path = $1${seqFilter}
       ORDER BY c.seq
       LIMIT 3`,
      params,
    );
    for (const row of result.rows) {
      if (hits.length >= limit) break;
      hits.push({
        ...rowToHit(row),
        score: 0,
        signals: ["graph"],
        viaEntities: info.entities.slice(0, 5),
      });
    }
  }
  return hits;
}

/** 三路并行融合检索入口 */
export async function hybridSearch(
  config: KnowledgeConfig,
  query: string,
  options: HybridSearchOptions = {},
): Promise<SearchHit[]> {
  const limit = options.limit ?? 8;

  const paths: Promise<SearchHit[]>[] = [
    bm25Search(config, query, RECALL_LIMIT, options.kinds),
    vectorSearch(config, query, RECALL_LIMIT, options.kinds),
  ];
  if (!options.noGraph) {
    paths.push(graphEntitySearch(config, query, GRAPH_CHUNK_LIMIT, options.kinds));
  }
  const results = await Promise.all(paths);
  // 图谱路衰减 0.5：辅助定位而不抢占 top-1
  const weights = options.noGraph ? [1, 1] : [1, 1, 0.5];
  const fused = fuseRrf(results, weights);
  return fused.slice(0, limit);
}
