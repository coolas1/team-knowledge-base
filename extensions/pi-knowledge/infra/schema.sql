-- pi-knowledge 派生索引 schema
-- 事实源是 vault 中的文件；本库随时可 drop 后重建。
-- 由 docker-entrypoint-initdb.d 在首次建库时执行。

CREATE EXTENSION IF NOT EXISTS pg_search;
CREATE EXTENSION IF NOT EXISTS vector;

-- 索引元数据（embedding 模型/维度等，摄取时校验一致性）
CREATE TABLE index_meta (
  key   text PRIMARY KEY,
  value text NOT NULL
);

INSERT INTO index_meta (key, value) VALUES
  ('schema_version', '1'),
  ('embedding_dim', '1024');

-- 文档：vault 中的一个文件（文档 / 记忆 / 会话档案）
CREATE TABLE documents (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_path   text NOT NULL UNIQUE,          -- 相对 vault 根的路径（记忆/会话也在 vault 内）
  kind          text NOT NULL,                 -- 'doc' | 'memory' | 'session'
  title         text NOT NULL DEFAULT '',
  content_hash  text NOT NULL,                 -- 影子 md 内容的 sha256，增量摄取判据
  shadow_path   text NOT NULL DEFAULT '',      -- 相对 vault 根的影子 md 路径（.derived/ 下）
  mtime_ms      bigint NOT NULL DEFAULT 0,     -- 源文件 mtime，快速跳过未变更文件
  meta          jsonb NOT NULL DEFAULT '{}',   -- frontmatter / 记忆属性（type,priority,status,derived_from...）
  embedding_model text NOT NULL DEFAULT '',
  indexed_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX documents_kind_idx ON documents (kind);

-- 块：检索的基本单元
CREATE TABLE chunks (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  doc_id      bigint NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  seq         int NOT NULL,                    -- 文档内顺序
  heading     text NOT NULL DEFAULT '',        -- 标题路径，如 "项目A > 会议纪要"
  content     text NOT NULL,
  asset_path  text NOT NULL DEFAULT '',        -- 图片块回指原图（相对 vault 根）
  embedding   vector(1024),
  meta        jsonb NOT NULL DEFAULT '{}'
);

CREATE INDEX chunks_doc_idx ON chunks (doc_id);

-- BM25 全文索引（jieba 中文分词，ParadeDB v0.22 语法）
CREATE INDEX chunks_bm25_idx ON chunks
USING bm25 (id, (content::pdb.jieba), (heading::pdb.jieba))
WITH (key_field='id');

-- 向量索引（cosine；doubao-embedding 截取前 1024 维并归一化）
CREATE INDEX chunks_embedding_idx ON chunks
USING hnsw (embedding vector_cosine_ops);

-- 图谱抽取任务队列（LLM 实体关系抽取是异步的）
CREATE TABLE graph_jobs (
  doc_id      bigint PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  status      text NOT NULL DEFAULT 'pending', -- 'pending' | 'running' | 'done' | 'failed'
  attempts    int NOT NULL DEFAULT 0,
  last_error  text NOT NULL DEFAULT '',
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX graph_jobs_status_idx ON graph_jobs (status);

-- 实体向量（LightRAG 式：实体描述的语义索引，支持 high-level 检索）
CREATE TABLE entity_vectors (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name        text NOT NULL UNIQUE,             -- 实体名（与 Neo4j Entity.name 对应）
  type        text NOT NULL DEFAULT '',
  description text NOT NULL DEFAULT '',
  embedding   vector(1024)
);

CREATE INDEX entity_vectors_embedding_idx ON entity_vectors
USING hnsw (embedding vector_cosine_ops);

-- 关系向量（关系描述的语义索引）
CREATE TABLE relation_vectors (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  head        text NOT NULL,                    -- 头实体名
  rel_type    text NOT NULL,                    -- 关系类型
  tail        text NOT NULL,                    -- 尾实体名
  description text NOT NULL DEFAULT '',
  embedding   vector(1024),
  UNIQUE (head, rel_type, tail)
);

CREATE INDEX relation_vectors_embedding_idx ON relation_vectors
USING hnsw (embedding vector_cosine_ops);
