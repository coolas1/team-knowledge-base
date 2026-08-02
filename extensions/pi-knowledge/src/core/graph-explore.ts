import type { KnowledgeConfig } from "../config.ts";
import { runCypher } from "./graph.ts";

/**
 * 图谱探索：供 graph_expand 工具做实体/文档邻域漫游。
 * 只读派生图，返回紧凑的文本视图（直接喂给 LLM）。
 */

const NEIGHBOR_LIMIT = 15;

/** 实体邻域：类型、提及它的文档、与其他实体的关系 */
export async function exploreEntity(config: KnowledgeConfig, name: string): Promise<string> {
  const entity = await runCypher(
    config,
    "MATCH (e:Entity {name: $name}) RETURN e.name AS name, e.type AS type",
    { name },
  );
  if (entity.length === 0) {
    // 精确名未命中时给模糊候选，方便 agent 修正
    const candidates = await runCypher(
      config,
      `MATCH (e:Entity) WHERE toLower(e.name) CONTAINS toLower($name)
       RETURN e.name AS name, e.type AS type LIMIT 10`,
      { name },
    );
    if (candidates.length === 0) return `实体不存在: ${name}`;
    const lines = candidates.map((c) => `- ${c.name} (${c.type})`);
    return `实体 "${name}" 不存在，相近候选:\n${lines.join("\n")}`;
  }

  const docs = await runCypher(
    config,
    `MATCH (d:Doc)-[:MENTIONS]->(e:Entity {name: $name})
     RETURN d.path AS path, d.title AS title LIMIT ${NEIGHBOR_LIMIT}`,
    { name },
  );
  const rels = await runCypher(
    config,
    `MATCH (a:Entity {name: $name})-[r:REL]-(b:Entity)
     RETURN DISTINCT a.name AS from, r.type AS type, b.name AS to,
            startNode(r).name AS start LIMIT ${NEIGHBOR_LIMIT}`,
    { name },
  );

  const lines = [`实体: ${entity[0].name} (${entity[0].type})`];
  if (docs.length > 0) {
    lines.push("", "提及它的文档:");
    for (const doc of docs) lines.push(`- ${doc.path} — ${doc.title}`);
  }
  if (rels.length > 0) {
    lines.push("", "实体关系:");
    for (const rel of rels) {
      const arrow = rel.start === name ? `${rel.from} -[${rel.type}]-> ${rel.to}` : `${rel.to} <-[${rel.type}]- ${rel.from}`;
      lines.push(`- ${arrow}`);
    }
  }
  return lines.join("\n");
}

/** 文档邻域：提及的实体、经共享实体的相关文档、溯源边 */
export async function exploreDoc(config: KnowledgeConfig, docPath: string): Promise<string> {
  const doc = await runCypher(
    config,
    "MATCH (d:Doc {path: $path}) RETURN d.path AS path, d.title AS title, d.kind AS kind",
    { path: docPath },
  );
  if (doc.length === 0) return `文档不在图谱中: ${docPath}`;

  const entities = await runCypher(
    config,
    `MATCH (d:Doc {path: $path})-[:MENTIONS]->(e:Entity)
     RETURN e.name AS name, e.type AS type LIMIT ${NEIGHBOR_LIMIT}`,
    { path: docPath },
  );
  const neighbors = await runCypher(
    config,
    `MATCH (d:Doc {path: $path})-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(n:Doc)
     RETURN n.path AS path, n.title AS title,
            collect(DISTINCT e.name)[..5] AS via, count(DISTINCT e) AS shared
     ORDER BY shared DESC LIMIT ${NEIGHBOR_LIMIT}`,
    { path: docPath },
  );
  const provenance = await runCypher(
    config,
    `MATCH (d:Doc {path: $path})-[:DERIVED_FROM]->(s:Doc) RETURN s.path AS source
     UNION
     MATCH (m:Doc)-[:DERIVED_FROM]->(d:Doc {path: $path}) RETURN m.path AS source`,
    { path: docPath },
  );

  const lines = [`文档: ${doc[0].path}${doc[0].title ? ` — ${doc[0].title}` : ""} (${doc[0].kind ?? "?"})`];
  if (entities.length > 0) {
    lines.push("", "提及的实体:");
    for (const e of entities) lines.push(`- ${e.name} (${e.type})`);
  }
  if (neighbors.length > 0) {
    lines.push("", "相关文档（共享实体）:");
    for (const n of neighbors) {
      lines.push(`- ${n.path} — ${n.title} [via ${(n.via as string[]).join("/")}]`);
    }
  }
  if (provenance.length > 0) {
    lines.push("", "溯源关联:");
    for (const p of provenance) lines.push(`- ${p.source}`);
  }
  return lines.join("\n");
}
