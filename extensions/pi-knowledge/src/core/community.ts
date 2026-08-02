import type { KnowledgeConfig } from "../config.ts";
import { arkChat } from "./ark.ts";
import { runCypher } from "./graph.ts";

/**
 * 社区检测与摘要生成（每日维护时调用）。
 *
 * 当前实现：基于连通分量的社区划分（无需 Neo4j GDS 插件）。
 * 升级路径：安装 Neo4j GDS 后可切换为 Leiden 算法（更精细的模块度优化）。
 *
 * 流程：
 * 1. 用 Cypher 迭代标记连通分量（Union-Find 思路）
 * 2. 过滤：成员 ≥ 3 个实体的分量才生成摘要
 * 3. LLM 为每个社区生成 200 字摘要
 * 4. 写入 Community 节点 + IN_COMMUNITY 边
 */

export interface CommunityStats {
  detected: number;
  summarized: number;
  failed: number;
}

/** 最小社区成员数（低于此值不生成摘要） */
const MIN_COMMUNITY_SIZE = 3;
/** 摘要最大字符数 */
const SUMMARY_MAX_CHARS = 300;

const SUMMARY_PROMPT = `你是知识图谱摘要助手。给定一组实体及其关系，用 2-3 句话概括这个主题社区的核心内容。
要求：
- 概括要具体（提及关键实体名），不要泛泛而谈
- 用中文
- 不超过 ${SUMMARY_MAX_CHARS} 字
只输出摘要文本，不要标题、不要解释。`;

interface CommunityMember {
  name: string;
  type: string;
}

interface DetectedCommunity {
  id: number;
  members: CommunityMember[];
  relations: Array<{ source: string; target: string; type: string }>;
}

/**
 * 连通分量检测：从每个 Entity 出发 BFS，找到所有通过 REL 边相连的实体组。
 * 使用纯 Cypher 实现（不依赖 GDS 插件）。
 */
async function detectCommunities(config: KnowledgeConfig): Promise<DetectedCommunity[]> {
  // 获取所有有 REL 边连接的实体对
  const edges = await runCypher(
    config,
    `MATCH (a:Entity)-[r:REL]-(b:Entity)
     RETURN DISTINCT a.name AS source, b.name AS target, r.type AS type`,
  ) as Array<{ source: string; target: string; type: string }>;

  if (edges.length === 0) return [];

  // Union-Find（JS 侧）
  const parent = new Map<string, string>();
  function find(x: string): string {
    if (!parent.has(x)) parent.set(x, x);
    let root = x;
    while (parent.get(root) !== root) root = parent.get(root)!;
    // 路径压缩
    let cur = x;
    while (cur !== root) {
      const next = parent.get(cur)!;
      parent.set(cur, root);
      cur = next;
    }
    return root;
  }
  function union(a: string, b: string): void {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent.set(ra, rb);
  }

  for (const edge of edges) {
    union(edge.source, edge.target);
  }

  // 分组
  const groups = new Map<string, Set<string>>();
  for (const node of parent.keys()) {
    const root = find(node);
    if (!groups.has(root)) groups.set(root, new Set());
    groups.get(root)!.add(node);
  }

  // 过滤小分量，收集成员信息
  const communities: DetectedCommunity[] = [];
  let id = 0;
  for (const [, members] of groups) {
    if (members.size < MIN_COMMUNITY_SIZE) continue;
    const memberNames = [...members];

    // 获取成员的类型
    const memberInfo = await runCypher(
      config,
      `MATCH (e:Entity) WHERE e.name IN $names RETURN e.name AS name, e.type AS type`,
      { names: memberNames },
    ) as CommunityMember[];

    // 获取组内关系
    const rels = edges.filter(
      (e) => members.has(e.source) && members.has(e.target),
    );

    communities.push({ id: id++, members: memberInfo, relations: rels });
  }

  return communities;
}

/** 为单个社区生成 LLM 摘要 */
async function summarizeCommunity(
  config: KnowledgeConfig,
  community: DetectedCommunity,
): Promise<string> {
  const entityList = community.members
    .map((m) => `${m.name}(${m.type})`)
    .join("、");
  const relList = community.relations
    .slice(0, 20)
    .map((r) => `${r.source} —[${r.type}]→ ${r.target}`)
    .join("\n");

  const input = `实体：${entityList}\n\n关系：\n${relList}`;
  return arkChat(config, config.ark.extractionModel, [
    { role: "system", content: SUMMARY_PROMPT },
    { role: "user", content: input.slice(0, 4000) },
  ]);
}

/** 清除旧社区数据 */
async function clearCommunities(config: KnowledgeConfig): Promise<void> {
  await runCypher(config, "MATCH ()-[r:IN_COMMUNITY]->() DELETE r");
  await runCypher(config, "MATCH (c:Community) DELETE c");
}

/**
 * 社区刷新主入口：检测 → 摘要 → 写入 Neo4j。
 * 由每日维护任务调用。
 */
export async function refreshCommunities(
  config: KnowledgeConfig,
  options: { onProgress?: (message: string) => void } = {},
): Promise<CommunityStats> {
  const stats: CommunityStats = { detected: 0, summarized: 0, failed: 0 };

  const communities = await detectCommunities(config);
  stats.detected = communities.length;
  if (communities.length === 0) return stats;

  await clearCommunities(config);

  for (const community of communities) {
    try {
      const summary = await summarizeCommunity(config, community);
      const memberNames = community.members.map((m) => m.name);

      // 写入 Community 节点
      await runCypher(
        config,
        `MERGE (c:Community {id: $id})
         SET c.summary = $summary, c.member_count = $count, c.updated_at = datetime()`,
        { id: community.id, summary: summary.trim(), count: memberNames.length },
      );

      // 建立 IN_COMMUNITY 边
      await runCypher(
        config,
        `MATCH (e:Entity) WHERE e.name IN $names
         MATCH (c:Community {id: $id})
         MERGE (e)-[:IN_COMMUNITY]->(c)`,
        { names: memberNames, id: community.id },
      );

      stats.summarized++;
      options.onProgress?.(
        `社区 #${community.id}（${memberNames.length} 成员）摘要已生成`,
      );
    } catch {
      stats.failed++;
    }
  }

  return stats;
}

/** 查询时：匹配社区摘要（全局问题路由用） */
export async function matchCommunitySummaries(
  config: KnowledgeConfig,
  query: string,
  limit = 5,
): Promise<Array<{ id: number; summary: string; memberCount: number }>> {
  try {
    const rows = await runCypher(
      config,
      `MATCH (c:Community)
       RETURN c.id AS id, c.summary AS summary, c.member_count AS memberCount
       ORDER BY c.member_count DESC
       LIMIT $limit`,
      { limit },
    ) as Array<{ id: number; summary: string; memberCount: number }>;
    return rows;
  } catch {
    return [];
  }
}
