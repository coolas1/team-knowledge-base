import pg from "pg";
import type { KnowledgeConfig } from "../config.ts";

/** ParadeDB 连接池。惰性创建，进程内单例，session_shutdown 时关闭。 */
let pool: pg.Pool | undefined;

export function getPool(config: KnowledgeConfig): pg.Pool {
  if (!pool) {
    pool = new pg.Pool({
      host: config.pg.host,
      port: config.pg.port,
      user: config.pg.user,
      password: config.pg.password,
      database: config.pg.database,
      max: 5,
    });
  }
  return pool;
}

export async function closePool(): Promise<void> {
  if (pool) {
    await pool.end();
    pool = undefined;
  }
}

/** 连通性 + schema 一致性检查，返回问题描述列表（空 = 正常） */
export async function checkDatabase(config: KnowledgeConfig): Promise<string[]> {
  const problems: string[] = [];
  try {
    const result = await getPool(config).query(
      "SELECT value FROM index_meta WHERE key = 'embedding_dim'",
    );
    const dim = Number(result.rows[0]?.value);
    if (dim !== config.ark.embeddingDim) {
      problems.push(
        `embedding 维度不一致: schema=${dim}, 配置=${config.ark.embeddingDim}（改维度需重建索引库）`,
      );
    }
  } catch (error) {
    problems.push(`ParadeDB 连接失败: ${error instanceof Error ? error.message : String(error)}`);
  }
  return problems;
}

/** 把 number[] 序列化为 pgvector 字面量 */
export function toVectorLiteral(embedding: number[]): string {
  return `[${embedding.join(",")}]`;
}
