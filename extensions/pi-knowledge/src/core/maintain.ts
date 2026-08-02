import fs from "node:fs/promises";
import path from "node:path";
import matter from "gray-matter";
import type { KnowledgeConfig } from "../config.ts";
import { derivedDir } from "../config.ts";
import { type ArkMessage, type ArkTool, arkChatMessage } from "./ark.ts";
import { getPool } from "./db.ts";
import { type GraphBuildStats, processGraphJobs } from "./graph-build.ts";
import { runCypher } from "./graph.ts";
import { type CommunityStats, refreshCommunities } from "./community.ts";
import { type IngestStats, ingestFile, ingestVault } from "./ingest.ts";
import { listMemories } from "./memory.ts";
import { type SessionIngestStats, ingestSessions } from "./session-ingest.ts";

/**
 * 定期维护：
 * 1. 管线收敛：vault 增量摄取 + session 情景层摄取 + 图谱作业消费
 * 2. 记忆维护（agentic 循环）：合并重复/冲突、supersede、清理过期
 * 3. 图谱维护（agentic 循环）：实体归一化合并、文档间关系补边
 * 每次执行生成审计报告存入 vault/maintenance/ 供人工核查（报告本身也入索引）。
 */

const MAX_LOOP_TURNS = 12;
/** 单个工具结果送回模型的字符上限 */
const TOOL_RESULT_CHARS = 6000;

// ============================================================================
// 通用 agentic 循环（ARK function calling）
// ============================================================================

interface MaintainTool {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  /** 变更类操作记入审计报告 */
  mutating?: boolean;
  execute(args: Record<string, unknown>): Promise<string>;
}

export interface LoopResult {
  summary: string;
  actions: string[];
  turns: number;
}

async function runAgenticLoop(
  config: KnowledgeConfig,
  options: {
    system: string;
    user: string;
    tools: MaintainTool[];
    onProgress?: (message: string) => void;
  },
): Promise<LoopResult> {
  const byName = new Map(options.tools.map((tool) => [tool.name, tool]));
  const arkTools: ArkTool[] = options.tools.map((tool) => ({
    type: "function",
    function: { name: tool.name, description: tool.description, parameters: tool.parameters },
  }));
  const messages: ArkMessage[] = [
    { role: "system", content: options.system },
    { role: "user", content: options.user },
  ];
  const actions: string[] = [];

  for (let turn = 1; turn <= MAX_LOOP_TURNS; turn++) {
    const { content, toolCalls } = await arkChatMessage(
      config,
      config.ark.extractionModel,
      messages,
      { tools: arkTools, maxTokens: 4096 },
    );
    if (toolCalls.length === 0) {
      return { summary: content.trim() || "（模型未给出总结）", actions, turns: turn };
    }
    messages.push({ role: "assistant", content, tool_calls: toolCalls });
    for (const call of toolCalls) {
      const name = call.function.name;
      const tool = byName.get(name);
      let result: string;
      try {
        const args = JSON.parse(call.function.arguments || "{}") as Record<string, unknown>;
        if (!tool) {
          result = `未知工具: ${name}`;
        } else {
          result = await tool.execute(args);
          if (tool.mutating) actions.push(`${name} ${call.function.arguments}`);
        }
      } catch (error) {
        result = `执行失败: ${error instanceof Error ? error.message : String(error)}`;
      }
      options.onProgress?.(`[${name}] ${result.split("\n")[0].slice(0, 120)}`);
      messages.push({
        role: "tool",
        content: result.slice(0, TOOL_RESULT_CHARS),
        tool_call_id: call.id,
      });
    }
  }
  return { summary: "达到轮次上限，循环终止。", actions, turns: MAX_LOOP_TURNS };
}

// ============================================================================
// 记忆维护
// ============================================================================

/** 防止 LLM 越界操作 vault 中记忆以外的文件 */
function assertMemoryPath(relPath: string): void {
  const normalized = relPath.split("\\").join("/");
  if (!normalized.startsWith("memories/") || normalized.includes("..")) {
    throw new Error(`非法记忆路径: ${relPath}`);
  }
}

async function rewriteMemoryFrontmatter(
  config: KnowledgeConfig,
  relPath: string,
  mutate: (data: Record<string, unknown>) => void,
  newContent?: string,
): Promise<void> {
  assertMemoryPath(relPath);
  const absPath = path.join(config.vaultDir, relPath);
  const parsed = matter(await fs.readFile(absPath, "utf8"));
  const data = parsed.data as Record<string, unknown>;
  mutate(data);
  data.updated = new Date().toISOString();
  const body = newContent !== undefined ? newContent.trim() : parsed.content.trim();
  await fs.writeFile(absPath, matter.stringify(`\n${body}\n`, data), "utf8");
  await ingestFile(config, relPath);
}

const MEMORY_MAINTAIN_PROMPT = `你是个人知识库的记忆维护员。给定当前全部记忆，你的任务：
1. 用 memory_read 核对可疑条目的全文（列表只有标题）
2. 语义重复的记忆：保留信息最完整的一条（必要时用 memory_update 把要点合并进去），其余用 memory_supersede 标记
3. 相互冲突的记忆：保留 updated 较新的结论，旧的用 memory_supersede 标记
4. 明显一次性、已失效的信息：memory_delete 删除
5. 完成后不再调用工具，直接输出简短中文总结（做了什么、为什么；没有问题就说记忆库健康）

原则：宁可保守，不确定就不动；每个操作前先读全文核实；不允许编造记忆内容。`;

async function maintainMemories(
  config: KnowledgeConfig,
  onProgress?: (message: string) => void,
): Promise<LoopResult> {
  const memories = await listMemories(config);
  if (memories.length < 2) {
    return { summary: "记忆少于 2 条，跳过维护。", actions: [], turns: 0 };
  }
  const listing = memories
    .map((m) => `- ${m.sourcePath} | ${m.type}/${m.priority} | ${m.title} | updated=${m.updatedAt}`)
    .join("\n");

  const tools: MaintainTool[] = [
    {
      name: "memory_read",
      description: "读取一条记忆的全文（含 frontmatter）",
      parameters: {
        type: "object",
        properties: { path: { type: "string", description: "记忆相对路径 memories/..." } },
        required: ["path"],
      },
      async execute(args) {
        const relPath = String(args.path ?? "");
        assertMemoryPath(relPath);
        return await fs.readFile(path.join(config.vaultDir, relPath), "utf8");
      },
    },
    {
      name: "memory_update",
      description: "重写一条记忆的正文（用于把重复记忆的要点合并进保留条目）",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string" },
          content: { type: "string", description: "新的正文（1-4 句自包含陈述）" },
        },
        required: ["path", "content"],
      },
      mutating: true,
      async execute(args) {
        await rewriteMemoryFrontmatter(config, String(args.path ?? ""), () => {}, String(args.content ?? ""));
        return `已更新: ${args.path}`;
      },
    },
    {
      name: "memory_supersede",
      description: "把一条记忆标记为已被取代（不再注入开场视图，但仍可检索到）",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string" },
          by: { type: "string", description: "取代它的记忆路径（可选）" },
        },
        required: ["path"],
      },
      mutating: true,
      async execute(args) {
        await rewriteMemoryFrontmatter(config, String(args.path ?? ""), (data) => {
          data.status = "superseded";
          if (typeof args.by === "string" && args.by !== "") data.superseded_by = args.by;
        });
        return `已标记 superseded: ${args.path}`;
      },
    },
    {
      name: "memory_delete",
      description: "彻底删除一条记忆（文件与索引），仅用于明显失效/一次性的信息",
      parameters: {
        type: "object",
        properties: { path: { type: "string" } },
        required: ["path"],
      },
      mutating: true,
      async execute(args) {
        const relPath = String(args.path ?? "");
        assertMemoryPath(relPath);
        await fs.rm(path.join(config.vaultDir, relPath), { force: true });
        await getPool(config).query("DELETE FROM documents WHERE source_path = $1", [relPath]);
        await runCypher(config, "MATCH (d:Doc {path: $path}) DETACH DELETE d", { path: relPath });
        return `已删除: ${relPath}`;
      },
    },
  ];

  return runAgenticLoop(config, {
    system: MEMORY_MAINTAIN_PROMPT,
    user: `当前全部 active 记忆（${memories.length} 条）：\n${listing}`,
    tools,
    onProgress,
  });
}

// ============================================================================
// 图谱维护
// ============================================================================

const GRAPH_MAINTAIN_PROMPT = `你是知识图谱维护员。给定当前全部实体，你的任务：
1. 找出指向同一现实对象的不同写法（全称/简称、中英文、大小写、错别字），用 graph_merge_entities 归一，保留最完整、最常用的名字
2. 拿不准时先用 graph_entity_docs 查看两个实体各自出现在哪些文档中再判断
3. 若两篇文档明显是同一主题的新旧版本（标题高度相似），可用 graph_link_docs 建立 UPDATES 关系
4. 完成后不再调用工具，直接输出简短中文总结

原则：只合并高置信度的重复实体；同名不同义（如"苹果"公司与水果）绝不合并；不确定就不动。`;

async function maintainGraph(
  config: KnowledgeConfig,
  onProgress?: (message: string) => void,
): Promise<LoopResult> {
  const entities = await runCypher(
    config,
    `MATCH (e:Entity)
     OPTIONAL MATCH (e)<-[:MENTIONS]-(d:Doc)
     RETURN e.name AS name, e.type AS type, count(d) AS mentions
     ORDER BY mentions DESC, name`,
  );
  if (entities.length < 2) {
    return { summary: "实体少于 2 个，跳过维护。", actions: [], turns: 0 };
  }
  const listing = entities
    .map((row) => `- ${row.name} (${row.type}, 提及 ${row.mentions})`)
    .join("\n");

  const tools: MaintainTool[] = [
    {
      name: "graph_entity_docs",
      description: "查看一个实体被哪些文档提及（用于合并前核实）",
      parameters: {
        type: "object",
        properties: { name: { type: "string" } },
        required: ["name"],
      },
      async execute(args) {
        const rows = await runCypher(
          config,
          `MATCH (e:Entity {name: $name})<-[:MENTIONS]-(d:Doc)
           RETURN d.path AS path, d.title AS title LIMIT 20`,
          { name: String(args.name ?? "") },
        );
        if (rows.length === 0) return "无提及文档（或实体不存在）。";
        return rows.map((row) => `- ${row.path} | ${row.title}`).join("\n");
      },
    },
    {
      name: "graph_merge_entities",
      description: "把 duplicate 实体合并进 canonical 实体（重定向所有提及与关系边，duplicate 名字记入别名后删除）",
      parameters: {
        type: "object",
        properties: {
          canonical: { type: "string", description: "保留的实体名" },
          duplicate: { type: "string", description: "被合并删除的实体名" },
        },
        required: ["canonical", "duplicate"],
      },
      mutating: true,
      async execute(args) {
        const canonical = String(args.canonical ?? "");
        const duplicate = String(args.duplicate ?? "");
        if (canonical === "" || duplicate === "" || canonical === duplicate) {
          throw new Error("canonical 与 duplicate 必须是两个不同的非空实体名");
        }
        const exists = await runCypher(
          config,
          "MATCH (c:Entity {name: $canonical}), (d:Entity {name: $duplicate}) RETURN count(*) AS n",
          { canonical, duplicate },
        );
        if (Number(exists[0]?.n ?? 0) === 0) throw new Error("实体不存在，检查名字是否精确匹配");
        await runCypher(
          config,
          `MATCH (d:Entity {name: $duplicate}), (c:Entity {name: $canonical})
           MATCH (doc)-[m:MENTIONS]->(d)
           MERGE (doc)-[:MENTIONS]->(c)
           DELETE m`,
          { canonical, duplicate },
        );
        await runCypher(
          config,
          `MATCH (d:Entity {name: $duplicate}), (c:Entity {name: $canonical})
           MATCH (d)-[r:REL]->(other) WHERE other <> c
           MERGE (c)-[:REL {type: r.type, source_path: r.source_path}]->(other)
           DELETE r`,
          { canonical, duplicate },
        );
        await runCypher(
          config,
          `MATCH (d:Entity {name: $duplicate}), (c:Entity {name: $canonical})
           MATCH (other)-[r:REL]->(d) WHERE other <> c
           MERGE (other)-[:REL {type: r.type, source_path: r.source_path}]->(c)
           DELETE r`,
          { canonical, duplicate },
        );
        await runCypher(
          config,
          `MATCH (d:Entity {name: $duplicate}), (c:Entity {name: $canonical})
           SET c.aliases = [x IN coalesce(c.aliases, []) WHERE x <> $duplicate] + $duplicate
           DETACH DELETE d`,
          { canonical, duplicate },
        );
        return `已合并: ${duplicate} → ${canonical}`;
      },
    },
    {
      name: "graph_link_docs",
      description: "在两篇文档之间建立关系边（如新文档 UPDATES 旧文档）",
      parameters: {
        type: "object",
        properties: {
          from_path: { type: "string", description: "源文档 source_path" },
          to_path: { type: "string", description: "目标文档 source_path" },
          relation: { type: "string", description: "关系类型，如 UPDATES" },
        },
        required: ["from_path", "to_path", "relation"],
      },
      mutating: true,
      async execute(args) {
        const rows = await runCypher(
          config,
          `MATCH (a:Doc {path: $from}), (b:Doc {path: $to})
           MERGE (a)-[:DOC_REL {type: $relation, by: 'maintenance'}]->(b)
           RETURN count(*) AS n`,
          {
            from: String(args.from_path ?? ""),
            to: String(args.to_path ?? ""),
            relation: String(args.relation ?? ""),
          },
        );
        if (Number(rows[0]?.n ?? 0) === 0) throw new Error("文档节点不存在，检查 source_path");
        return `已建边: ${args.from_path} -[${args.relation}]-> ${args.to_path}`;
      },
    },
  ];

  return runAgenticLoop(config, {
    system: GRAPH_MAINTAIN_PROMPT,
    user: `当前全部实体（${entities.length} 个）：\n${listing}`,
    tools,
    onProgress,
  });
}

// ============================================================================
// 维护入口与审计报告
// ============================================================================

export interface MaintenanceReport {
  reportPath: string;
  vault: IngestStats;
  sessions: SessionIngestStats;
  graph: GraphBuildStats;
  community: CommunityStats;
  relationsNormalized: number;
  memoryLoop: LoopResult;
  graphLoop: LoopResult;
  elapsedMs: number;
}

function stateFilePath(config: KnowledgeConfig): string {
  return path.join(derivedDir(config), "maintenance-state.json");
}

export interface MaintenanceState {
  lastRun?: string;
  lastTriggered?: string;
}

export async function readMaintenanceState(config: KnowledgeConfig): Promise<MaintenanceState> {
  try {
    return JSON.parse(await fs.readFile(stateFilePath(config), "utf8")) as MaintenanceState;
  } catch {
    return {};
  }
}

export async function writeMaintenanceState(
  config: KnowledgeConfig,
  patch: MaintenanceState,
): Promise<void> {
  const state = { ...(await readMaintenanceState(config)), ...patch };
  await fs.mkdir(derivedDir(config), { recursive: true });
  await fs.writeFile(stateFilePath(config), JSON.stringify(state, null, 2), "utf8");
}

function loopSection(result: LoopResult): string {
  const lines: string[] = [];
  lines.push(
    result.actions.length === 0
      ? "无变更操作。"
      : `变更操作（${result.actions.length}）：\n${result.actions.map((a) => `- \`${a}\``).join("\n")}`,
  );
  lines.push(`\n总结：${result.summary}`);
  return lines.join("\n");
}

export async function runMaintenance(
  config: KnowledgeConfig,
  options: { skipAgentic?: boolean; onProgress?: (message: string) => void } = {},
): Promise<MaintenanceReport> {
  const startedAt = Date.now();
  const log = options.onProgress;

  log?.("vault 增量摄取...");
  const vault = await ingestVault(config, { onProgress: log });
  log?.("session 情景层摄取...");
  const sessions = await ingestSessions(config, { onProgress: log });
  log?.("图谱作业消费...");
  const graph = await processGraphJobs(config, { limit: 200, onProgress: log });

  log?.("关系归一化...");
  const relationsNormalized = await normalizeRelations(config, log);

  log?.("社区检测与摘要刷新...");
  const community = await refreshCommunities(config, { onProgress: log });

  let memoryLoop: LoopResult = { summary: "跳过（--skip-agentic）。", actions: [], turns: 0 };
  let graphLoop: LoopResult = { summary: "跳过（--skip-agentic）。", actions: [], turns: 0 };
  if (!options.skipAgentic) {
    log?.("记忆维护循环...");
    memoryLoop = await maintainMemories(config, log);
    log?.("图谱维护循环...");
    graphLoop = await maintainGraph(config, log);
  }

  const elapsedMs = Date.now() - startedAt;
  const now = new Date();
  const stamp = now.toISOString().replace(/[:T]/g, "-").slice(0, 16);
  const reportPath = `maintenance/${stamp}.md`;
  const failedLines = [
    ...vault.failed.map((f) => `- vault: ${f.path}: ${f.error}`),
    ...sessions.failed.map((f) => `- session: ${f.path}: ${f.error}`),
    ...graph.failed.map((f) => `- graph: ${f.path}: ${f.error}`),
  ];
  const report = `---
title: 维护报告 ${stamp}
type: maintenance-report
---

# 维护报告 ${stamp}

## 管线收敛

- vault 摄取：扫描 ${vault.scanned}，新增/变更 ${vault.ingested}，移除 ${vault.removed}，失败 ${vault.failed.length}
- 会话摄取：扫描 ${sessions.scanned}，新增/变更 ${sessions.ingested}，跳过 ${sessions.skipped}，移除 ${sessions.removed}，失败 ${sessions.failed.length}
- 图谱作业：处理 ${graph.processed}，成功 ${graph.done}，失败 ${graph.failed.length}
- 关系归一化：合并 ${relationsNormalized} 条冗余关系
- 社区刷新：检测 ${community.detected} 个社区，摘要 ${community.summarized}，失败 ${community.failed}
${failedLines.length > 0 ? `\n失败明细：\n${failedLines.join("\n")}\n` : ""}
## 记忆维护（${memoryLoop.turns} 轮）

${loopSection(memoryLoop)}

## 图谱维护（${graphLoop.turns} 轮）

${loopSection(graphLoop)}

## 耗时

${Math.round(elapsedMs / 1000)} 秒
`;

  const reportAbs = path.join(config.vaultDir, reportPath);
  await fs.mkdir(path.dirname(reportAbs), { recursive: true });
  await fs.writeFile(reportAbs, report, "utf8");
  await ingestFile(config, reportPath);
  await writeMaintenanceState(config, { lastRun: now.toISOString() });

  return { reportPath, vault, sessions, graph, community, relationsNormalized, memoryLoop, graphLoop, elapsedMs };
}

// ============================================================================
// 关系归一化：合并语义重复的自创关系类型为推荐标准类型
// ============================================================================

/** 常见自创关系 → 标准类型映射 */
const RELATION_NORMALIZE_MAP: Record<string, string> = {
  "负责": "RESPONSIBLE",
  "管理": "REPORTS_TO",
  "领导": "REPORTS_TO",
  "带": "REPORTS_TO",
  "汇报": "REPORTS_TO",
  "属于": "PART_OF",
  "包含": "PART_OF",
  "依赖": "DEPENDS_ON",
  "需要": "REQUIRES",
  "影响": "AFFECTS",
  "分配给": "ASSIGNED_TO",
  "阻塞": "BLOCKS",
  "部署在": "DEPLOYED_ON",
  "运行在": "DEPLOYED_ON",
  "实现": "IMPLEMENTS",
  "任职于": "WORKS_AT",
  "担任": "HAS_ROLE",
  "参与": "PARTICIPATES",
};

async function normalizeRelations(
  config: KnowledgeConfig,
  log?: (message: string) => void,
): Promise<number> {
  let normalized = 0;
  try {
    // 查找所有非标准关系类型（小写中文的）
    const rels = await runCypher(
      config,
      `MATCH ()-[r:REL]->() RETURN DISTINCT r.type AS type, count(r) AS cnt`,
    ) as Array<{ type: string; cnt: number }>;

    for (const rel of rels) {
      const standard = RELATION_NORMALIZE_MAP[rel.type];
      if (!standard) continue;
      // 把该类型的所有边改为标准类型
      await runCypher(
        config,
        `MATCH ()-[r:REL {type: $old}]->() SET r.type = $new`,
        { old: rel.type, new: standard },
      );
      normalized += Number(rel.cnt);
      log?.(`关系归一: "${rel.type}" → ${standard}（${rel.cnt} 条）`);
    }
  } catch {
    // 图谱不可用时跳过
  }
  return normalized;
}

