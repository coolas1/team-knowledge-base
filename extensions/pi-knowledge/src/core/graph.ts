import neo4j, { type Driver } from "neo4j-driver";
import type { KnowledgeConfig } from "../config.ts";

/** Neo4j driver。惰性创建，进程内单例，session_shutdown 时关闭。 */
let driver: Driver | undefined;

export function getDriver(config: KnowledgeConfig): Driver {
  if (!driver) {
    driver = neo4j.driver(
      config.neo4j.url,
      neo4j.auth.basic(config.neo4j.user, config.neo4j.password),
    );
  }
  return driver;
}

export async function closeDriver(): Promise<void> {
  if (driver) {
    await driver.close();
    driver = undefined;
  }
}

/** 执行写 Cypher（自动管理 session） */
export async function runCypher(
  config: KnowledgeConfig,
  cypher: string,
  params: Record<string, unknown> = {},
): Promise<Record<string, unknown>[]> {
  const session = getDriver(config).session();
  try {
    const result = await session.run(cypher, params);
    return result.records.map((record) => record.toObject());
  } finally {
    await session.close();
  }
}

/** 幂等建约束（容器重建后由代码自愈，不依赖手工初始化） */
export async function ensureGraphSchema(config: KnowledgeConfig): Promise<void> {
  await runCypher(
    config,
    "CREATE CONSTRAINT entity_name IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE",
  );
  await runCypher(
    config,
    "CREATE CONSTRAINT doc_path IF NOT EXISTS FOR (d:Doc) REQUIRE d.path IS UNIQUE",
  );
  await runCypher(
    config,
    "CREATE CONSTRAINT community_id IF NOT EXISTS FOR (c:Community) REQUIRE c.id IS UNIQUE",
  );
}

/** 连通性检查，返回问题描述列表（空 = 正常） */
export async function checkGraph(config: KnowledgeConfig): Promise<string[]> {
  try {
    await runCypher(config, "RETURN 1");
    return [];
  } catch (error) {
    return [`Neo4j 连接失败: ${error instanceof Error ? error.message : String(error)}`];
  }
}
