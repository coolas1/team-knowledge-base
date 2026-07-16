# 文档版本管理与目录监控系统设计

## 概述

为知识库系统新增**文档版本管理**与**本地目录自动同步**能力：监控用户指定的本地目录，自动感知文件创建/修改/删除/重命名事件，建立完整的文档版本历史（支持 diff 对比与回滚），同时保持向量索引与知识图谱仅针对最新版本构建。

## 背景

### 当前问题

1. **无目录感知能力**：文档只能通过 REST API 手动上传，无法自动同步本地目录
2. **无版本历史**：每次编辑/重新上传直接覆盖 `raw_text`，旧内容不可恢复
3. **无变更追溯**：`content_hash` 仅用于幂等校验，不记录变更历史
4. **无 diff 能力**：无法对比同一文档不同版本之间的内容差异

### 目标

1. 监控配置的本地目录，自动感知文件变更（创建/修改/删除/重命名）
2. 为每个文档建立完整的版本历史链，支持 IDE 式的多版本浏览
3. 支持版本间 unified diff 对比与一键回滚
4. 向量索引与知识图谱仅构建最新版本，旧版本不参与检索
5. Pipeline 按需执行（定时 + 手动），避免频繁保存时的 LLM token 浪费

## 设计决策

| # | 维度 | 决策 | 理由 |
|---|------|------|------|
| 1 | 扫描触发 | 启动全量扫描 + watchdog 实时监听 | 全量扫描保证不漏，watchdog 近实时感知增量变化 |
| 2 | 文档身份 | 文件名 + 相对路径 | 最直觉，零配置，与 content_hash 幂等校验正交 |
| 3 | 重命名检测 | hash 配对（主）+ watchdog move 事件（快速路径） | 跨平台可靠 + 最佳情况下零延迟 |
| 4 | 重命名+改内容 | 当作新文档，旧文档标记 disappeared | 简单可靠，避免复杂的模糊匹配 |
| 5 | 版本存储 | DB 全量 raw_text + 磁盘原始文件快照 | 查询简单，磁盘占用可接受 |
| 6 | 索引范围 | 向量/图谱仅最新版 | 旧版本不参与检索，节省存储和计算 |
| 7 | diff 算法 | 提取后纯文本 + difflib unified diff | 统一所有格式，Python 内置零依赖 |
| 8 | 目录配置 | YAML 多目录 + 排除规则 | 灵活表达多目录和 glob 排除，与现有 YAML 配置模式一致 |
| 9 | 文件删除 | 软删除（disappeared 状态） | 保留版本历史，文件放回自动恢复 |
| 10 | 版本回滚 | 旧版本作为新最新版，创建新版本记录 | 类似 Git revert，历史链不断 |
| 11 | 文档源 | source_type（manual/watch）+ source_path | 统一版本管理能力，不绑定入库方式 |
| 12 | Pipeline 触发 | 12h 定时 + 手动触发 | 避免频繁保存的 token 浪费，用户有完全控制权 |
| 13 | 待索引检索 | 返回旧索引 + 标记"索引待更新" | 旧索引仍有参考价值，标记提醒用户 |
| 14 | 版本保留 | 全量保留，不设上限 | 团队知识库规模可控，版本历史是核心价值 |
| 15 | 前端交互 | 嵌入文档详情页 | 符合"看文档顺便看历史"的心智模型 |
| 16 | 存量迁移 | 启动时自动迁移 | 用户无感知，补建 version=1 |
| 17 | 状态模型 | index_status + file_status 正交拆分 | 两个维度互不干扰，查询和筛选更清晰 |

## 系统架构

### 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        文件系统                               │
│  config/watch_config.yaml    D:/docs/   D:/notes/           │
└──────────────┬───────────────────────────┬──────────────────┘
               │                           │
       ┌───────┴────────┐          ┌───────┴────────┐
       │  启动全量扫描    │          │  Watchdog 监听  │
       │  (lifespan)    │          │  (后台线程)     │
       └───────┬────────┘          └───────┬────────┘
               │                           │
               └───────────┬───────────────┘
                           │
                    ┌──────┴──────┐
                    │ FileWatcher │  ← 感知层（轻量）
                    │ 事件处理器   │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
         新文件        内容变更      文件消失
              │            │            │
              ▼            ▼            ▼
        创建版本记录   创建版本记录   标记 disappeared
        标记 stale    标记 stale
              │            │
              └─────┬──────┘
                    │
             pending 队列（DB）
                    │
          ┌─────────┴─────────┐
     12h 定时任务          手动触发
          └─────────┬─────────┘
                    │
             ┌──────┴──────┐
             │   Pipeline   │  ← 处理层（重量）
             │ LLM+Embed+写 │
             └──────┬──────┘
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
       PostgreSQL  Neo4j   版本快照
       (向量+chunks) (图谱)  (磁盘文件)
```

### 分层职责

| 层 | 组件 | 职责 | 开销 |
|----|------|------|------|
| **感知层** | FileWatcher（启动扫描 + watchdog） | 检测文件变更，创建版本记录，标记状态 | 轻量（hash 计算 + DB 写入） |
| **调度层** | 定时任务 + 手动触发 API | 决定何时执行 Pipeline | 无计算开销 |
| **处理层** | Pipeline | 提取→分块→LLM→Embedding→写入索引 | 重量（LLM 调用） |
| **存储层** | PostgreSQL + Neo4j + 磁盘 | 持久化版本、索引、图谱 | — |

## 数据模型变更

### Document 表（修改）

```sql
ALTER TABLE documents
  ADD COLUMN source_type TEXT NOT NULL DEFAULT 'manual',   -- 'manual' | 'watch'
  ADD COLUMN source_path TEXT,                              -- 监控目录下的相对路径，手动上传为 NULL
  ADD COLUMN watch_dir TEXT,                                -- 所属监控目录绝对路径，手动上传为 NULL
  ADD COLUMN index_status TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'processing' | 'indexed' | 'failed' | 'stale'
  ADD COLUMN file_status TEXT NOT NULL DEFAULT 'active';    -- 'active' | 'disappeared'

-- 原 status 字段废弃，由 index_status + file_status 替代
-- 迁移脚本：UPDATE documents SET index_status = status, file_status = 'active', source_type = 'manual';
-- ALTER TABLE documents DROP COLUMN status;
```

### DocumentVersion 表（新增）

```sql
CREATE TABLE document_versions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  doc_id      UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version     INTEGER NOT NULL,                            -- 版本号（从 1 递增）
  raw_text    TEXT NOT NULL,                               -- 提取后的纯文本（用于 diff）
  content_hash TEXT NOT NULL,                              -- SHA256
  file_path   TEXT,                                        -- 磁盘快照路径
  change_type TEXT NOT NULL DEFAULT 'create',              -- 'create' | 'modify' | 'rename' | 'rollback'
  change_summary TEXT,                                     -- 变更摘要（可选，如 "+12 -3 行"）
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE(doc_id, version)
);

CREATE INDEX idx_versions_doc_id ON document_versions(doc_id);
CREATE INDEX idx_versions_doc_version ON document_versions(doc_id, version DESC);
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | INTEGER | 文档内自增版本号，从 1 开始 |
| `raw_text` | TEXT | extractor 提取后的纯文本，用于 diff 计算和回溯展示 |
| `content_hash` | TEXT | 文件内容的 SHA256，用于变更检测和重命名识别 |
| `file_path` | TEXT | 磁盘快照路径：`uploads/{doc_id}/versions/v{version}_{filename}` |
| `change_type` | TEXT | 变更类型：首次创建/内容修改/重命名/回滚 |
| `change_summary` | TEXT | 可选的变更统计，如 difflib 的 "+12 -3" |

## 目录监控配置

### 配置文件

`config/watch_config.yaml`：

```yaml
watch:
  enabled: true
  directories:
    - path: "D:/docs"
      recursive: true
    - path: "D:/notes"
      recursive: true
  exclude_patterns:
    - "*.tmp"
    - ".git/**"
    - "node_modules/**"
    - "~$*"              # Office 临时文件
    - "*.swp"            # Vim 临时文件
    - ".DS_Store"
  pipeline:
    schedule_hours: 12   # 自动同步间隔（小时），0 表示禁用
    enabled: true
```

### 配置加载

在 `src/main.py` 的 `lifespan` 中加载，与现有 `model_config.yaml` 和 `entity_schema.yaml` 模式一致。

## 核心模块设计

### FileWatcher（新增：`src/watcher/watcher.py`）

**职责**：感知层，检测文件变更并更新版本记录。

```python
class FileWatcher:
    """文件系统变更监听器。"""
    
    def __init__(self, config: WatchConfig):
        self._config = config
        self._observer: Observer | None = None
    
    async def start(self) -> None:
        """启动：执行全量扫描 + 启动 watchdog 后台线程。"""
        await self._full_scan()
        self._start_watchdog()
    
    async def stop(self) -> None:
        """停止 watchdog 后台线程。"""
    
    async def _full_scan(self) -> None:
        """启动时全量扫描：对比目录文件与 DB 记录，标记差异。"""
        # 1. 遍历所有配置目录，收集文件列表 + content_hash
        # 2. 与 DB 中 source_path + content_hash 对比
        # 3. 新文件 → 创建 Document + Version(version=1), index_status=pending
        # 4. 内容变了 → 创建新 Version, index_status=stale
        # 5. 文件消失 → file_status=disappeared
        # 6. 重命名检测：hash 配对
    
    def _start_watchdog(self) -> None:
        """启动 watchdog Observer 后台线程。"""
        # 注册 _WatchdogHandler，监听 create/modify/delete/move 事件
    
    def _on_file_changed(self, path: Path) -> None:
        """文件变更回调（watchdog 线程中执行）。"""
        # 1. 检查排除规则
        # 2. 计算 content_hash
        # 3. 查找 DB 中对应文档
        # 4. hash 相同 → 跳过（幂等）
        # 5. hash 不同 → 创建新 Version, 标记 index_status=stale
        # 6. 新文件 → 创建 Document + Version
    
    def _on_file_deleted(self, path: Path) -> None:
        """文件删除回调。"""
        # 标记 file_status=disappeared
    
    def _on_file_moved(self, src: Path, dest: Path) -> None:
        """文件移动/重命名回调。"""
        # 1. 快速路径：watchdog 提供 src/dest
        # 2. 更新 source_path + title
        # 3. 若 hash 没变 → 不创建新版本，只更新元数据
    
    async def _detect_rename_by_hash(self, new_path: Path, new_hash: str) -> Document | None:
        """通过 hash 配对检测重命名（降级路径）。"""
        # 在 DB 中查找 hash 相同但 source_path 已失效的文档
```

### Pipeline 调度器（新增：`src/watcher/scheduler.py`）

**职责**：管理定时任务和手动触发的 Pipeline 执行。

```python
class PipelineScheduler:
    """Pipeline 执行调度器。"""
    
    async def start(self) -> None:
        """启动定时任务（asyncio 定时循环）。"""
    
    async def stop(self) -> None:
        """停止定时任务。"""
    
    async def trigger_manual(self) -> dict:
        """手动触发：立即处理所有 pending/stale 文档。"""
    
    async def _scheduled_run(self) -> None:
        """定时执行：处理所有 pending/stale 文档。"""
        # 1. 查询 index_status IN ('pending', 'stale') 的文档
        # 2. 逐个执行 Pipeline
        # 3. 更新 index_status → indexed / failed
```

### Pipeline 改造（修改：`src/pipeline/pipeline.py`）

**核心变更**：Pipeline 执行前创建版本快照，只处理最新版。

```python
async def process_file(self, doc_id, file_path, title, file_type):
    # ... 现有逻辑 ...
    
    # 新增：执行前将当前 raw_text 存入 document_versions
    # （如果感知层已创建版本记录，这里跳过）
    
    # 新增：将原始文件复制到版本快照目录
    # uploads/{doc_id}/versions/v{version}_{filename}
    
    # 现有：提取→分块→LLM→Embedding→写入 PG+Neo4j
    
    # 修改：更新 index_status 而非 status
```

### 版本管理服务（新增：`src/core/version_manager.py`）

**职责**：版本查询、diff 计算、回滚。

```python
class VersionManager:
    """文档版本管理。"""
    
    async def list_versions(self, doc_id: UUID) -> list[VersionSummary]:
        """获取文档的版本列表（按 version DESC）。"""
    
    async def get_version(self, doc_id: UUID, version: int) -> VersionDetail:
        """获取指定版本的详细内容（raw_text、file_path）。"""
    
    async def get_diff(self, doc_id: UUID, from_version: int, to_version: int) -> DiffResult:
        """计算两个版本之间的 unified diff。"""
        # 使用 difflib.unified_diff()
    
    async def rollback(self, doc_id: UUID, target_version: int) -> dict:
        """回滚到指定版本：创建新版本（内容等于目标版本），触发 re-index。"""
        # 1. 获取 target_version 的 raw_text
        # 2. 创建新版本记录（change_type='rollback'）
        # 3. 触发 Pipeline re-index
```

### Diff 计算

```python
import difflib

def compute_unified_diff(old_text: str, new_text: str, 
                          old_label: str = "旧版本", 
                          new_label: str = "新版本") -> str:
    """计算 unified diff。"""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=old_label, tofile=new_label,
        lineterm=""
    )
    return "".join(diff)
```

## API 设计

### 版本管理端点

#### `GET /documents/{doc_id}/versions`

返回文档的版本列表。

**响应：**
```json
{
  "doc_id": "uuid",
  "current_version": 3,
  "versions": [
    {
      "version": 3,
      "change_type": "modify",
      "change_summary": "+12 -3",
      "content_hash": "abc123...",
      "created_at": "2025-01-15T10:30:00Z"
    },
    {
      "version": 2,
      "change_type": "modify",
      "change_summary": "+5 -1",
      "content_hash": "def456...",
      "created_at": "2025-01-14T15:00:00Z"
    },
    {
      "version": 1,
      "change_type": "create",
      "change_summary": null,
      "content_hash": "ghi789...",
      "created_at": "2025-01-13T09:00:00Z"
    }
  ]
}
```

#### `GET /documents/{doc_id}/versions/{version}`

获取指定版本的详细内容。

**响应：**
```json
{
  "version": 2,
  "raw_text": "文档全文...",
  "content_hash": "def456...",
  "file_path": "uploads/xxx/versions/v2_文件名.md",
  "change_type": "modify",
  "change_summary": "+5 -1",
  "created_at": "2025-01-14T15:00:00Z"
}
```

#### `GET /documents/{doc_id}/versions/diff?from=2&to=3`

计算两个版本之间的 unified diff。

**响应：**
```json
{
  "from_version": 2,
  "to_version": 3,
  "diff": "--- 旧版本\n+++ 新版本\n@@ -1,5 +1,8 @@\n 第一行\n-删除的行\n+新增的行\n...",
  "stats": { "added": 12, "removed": 3 }
}
```

#### `POST /documents/{doc_id}/versions/{version}/rollback`

回滚到指定版本。

**响应：**
```json
{
  "new_version": 4,
  "rolled_back_from": 3,
  "rolled_back_to_content_of": 2,
  "index_status": "processing"
}
```

### 同步控制端点

#### `POST /sync`

手动触发 Pipeline，处理所有 pending/stale 文档。

**响应：**
```json
{
  "triggered": true,
  "pending_count": 5,
  "message": "已触发 5 个文档的索引任务"
}
```

#### `GET /sync/status`

获取当前同步状态。

**响应：**
```json
{
  "watch_enabled": true,
  "watch_directories": ["D:/docs", "D:/notes"],
  "last_scan_at": "2025-01-15T08:00:00Z",
  "last_pipeline_at": "2025-01-15T08:00:00Z",
  "pending_count": 3,
  "schedule_hours": 12,
  "next_scheduled_at": "2025-01-15T20:00:00Z"
}
```

### 现有端点适配

| 端点 | 变更 |
|------|------|
| `GET /documents` | 新增 `file_status` 筛选参数，默认 `file_status=active` |
| `GET /documents/{doc_id}` | 响应新增 `index_status`、`file_status`、`source_type`、`version_count` |
| `POST /search` | 搜索结果新增 `index_status` 字段，前端据此显示"索引待更新"标记 |
| `DELETE /documents/{doc_id}` | 改为软删除：`file_status=disappeared`，保留版本历史和索引 |

## 前端设计

### 文档详情页改造

在 `DocumentDetailPage` 中嵌入版本历史面板：

```
┌──────────────────────────────────────────────────────────────────┐
│  ← 系统架构设计.md  [indexed] [active]  md · 8 chunks · v3      │
│                                       [同步] [编辑] [删除]       │
├──────────┬───────────────────────────────────────────────────────┤
│ 版本历史  │  ┌─ [内容] [Diff] ──────────────────────────────┐   │
│           │  │                                              │   │
│ ● v3 当前 │  │  # 系统架构设计                               │   │
│   modify  │  │  ## 1. 概述                                  │   │
│   01-15   │  │  本系统采用微服务架构...                      │   │
│           │  │                                              │   │
│ ○ v2      │  │                                              │   │
│   modify  │  │                                              │   │
│   01-14   │  │                                              │   │
│   +5 -1   │  │                                              │   │
│           │  │                                              │   │
│ ○ v1      │  │                                              │   │
│   create  │  │                                              │   │
│   01-13   │  └──────────────────────────────────────────────┘   │
│           │  [回滚到 v2]                                         │
└──────────┴──────────────────────────────────────────────────────┘
```

### 版本列表项

每个版本项显示：
- 版本号 + 当前标记
- 变更类型图标（create/modify/rename/rollback）
- 变更统计（+N -M 行）
- 创建时间

### Diff 视图

选中某个版本后，切换"内容"和"Diff"Tab：
- **内容**：展示该版本的 `raw_text`
- **Diff**：展示该版本与上一版本的 unified diff（添加行绿色，删除行红色）

### 文档列表页

新增状态指示器：
- `file_status=disappeared` 的文档灰显，标注"文件已消失"
- `index_status=stale` 的文档标注"⚠ 索引待更新"
- 新增筛选器：按 `file_status` 和 `index_status` 筛选

### 同步控制

在文档列表页顶部新增同步状态栏：

```
┌──────────────────────────────────────────────────────────┐
│ 📂 监控: D:/docs, D:/notes  |  上次同步: 2h前  | [立即同步] │
│ ⏳ 待索引: 3 个文档                                     │
└──────────────────────────────────────────────────────────┘
```

## 状态流转

### index_status 状态机

```
                  ┌──────────┐
       新文档 ──→ │ pending  │
                  └────┬─────┘
                       │ Pipeline 开始
                       ▼
                  ┌──────────┐
                  │processing│
                  └────┬─────┘
                  ┌────┴────┐
                  │         │
              成功 │         │ 失败
                  ▼         ▼
            ┌────────┐ ┌────────┐
            │indexed │ │ failed │
            └───┬────┘ └────────┘
                │ 文件变更
                ▼
            ┌────────┐
            │ stale  │ ──→ Pipeline 重新执行 → processing → indexed
            └────────┘
```

### file_status 状态机

```
            ┌────────┐
   新文件 ─→│ active │
            └───┬────┘
                │ watchdog 检测到删除
                ▼
            ┌─────────────┐
            │ disappeared │
            └──────┬──────┘
                   │ 文件放回目录
                   ▼
            ┌────────┐
            │ active │（创建新版本）
            └────────┘
```

## 重命名检测流程

```
watchdog 事件
    │
    ├─ FileMovedEvent (含 src_path + dest_path)
    │       │
    │       ▼
    │   更新 source_path + title
    │   hash 不变 → 不创建新版本
    │   hash 变了 → 当作新文档（旧 disappeared）
    │
    ├─ FileDeleted + FileCreated (降级)
    │       │
    │       ▼
    │   计算新文件 hash
    │   在 DB 中查找 hash 相同 + file_status=disappeared 的文档
    │       │
    │       ├─ 找到 → 匹配为重命名，更新 source_path + title
    │       │
    │       └─ 未找到 → 当作新文件处理
    │
    └─ 超时未配对（deleted 无对应 created）
            │
            ▼
        标记 disappeared
```

## 文件存储结构

```
uploads/
├── {doc_id}/
│   ├── 当前文件名.ext              ← 最新版原始文件
│   └── versions/
│       ├── v1_文件名.ext           ← 版本 1 快照
│       ├── v2_文件名.ext           ← 版本 2 快照
│       └── v3_文件名.ext           ← 版本 3 快照
└── ...
```

**约束**：文件快照存储在项目目录内（`uploads/`），不写入 C 盘。

## 存量数据迁移

在服务启动的 `lifespan` 中执行：

```python
async def migrate_legacy_documents(session):
    """将老数据迁移到版本管理模型。"""
    # 1. 为所有无 source_type 的文档设置 source_type='manual'
    # 2. 将 status 字段值迁移到 index_status
    # 3. 设置 file_status='active'
    # 4. 为每个文档创建 version=1 的记录（用当前 raw_text + content_hash）
    # 5. 幂等：已有 version 记录的文档跳过
```

## 依赖变更

### 后端新增

| 包 | 用途 |
|---|------|
| `watchdog` | 跨平台文件系统事件监听 |
| `apscheduler` | 定时任务调度（可选，也可用 asyncio 原生定时） |

### 前端新增

无新增依赖。Diff 展示使用 `<pre>` + 行级着色（内联样式），与项目零 CSS 架构一致。

## 涉及文件

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/db/models.py` | 修改 | Document 表改造 + 新增 DocumentVersion 模型 |
| `src/db/postgres.py` | 修改 | 迁移脚本 |
| `config/watch_config.yaml` | 新增 | 目录监控配置 |
| `src/watcher/__init__.py` | 新增 | 模块入口 |
| `src/watcher/watcher.py` | 新增 | FileWatcher 感知层 |
| `src/watcher/scheduler.py` | 新增 | Pipeline 调度器 |
| `src/core/version_manager.py` | 新增 | 版本管理业务逻辑 |
| `src/core/knowledge_base.py` | 修改 | 集成版本管理，适配新状态模型 |
| `src/pipeline/pipeline.py` | 修改 | 版本快照创建，适配 index_status |
| `src/api/routes.py` | 修改 | 新增版本管理/同步端点，适配新状态 |
| `src/main.py` | 修改 | lifespan 中初始化 FileWatcher + Scheduler + 迁移 |
| `frontend/src/api/client.ts` | 修改 | 新增版本管理/同步 API 方法 |
| `frontend/src/pages/DocumentDetailPage.tsx` | 修改 | 嵌入版本历史面板 |
| `frontend/src/pages/DocumentListPage.tsx` | 修改 | 同步状态栏 + 状态筛选 |
| `frontend/src/components/VersionHistory.tsx` | 新增 | 版本历史面板组件 |
| `frontend/src/components/DiffView.tsx` | 新增 | Diff 展示组件 |

## 已知限制

1. **watchdog move 事件在 Windows 上不总是可靠**：已通过 hash 配对降级方案兜底
2. **大文件版本的磁盘占用**：每次 Pipeline 都存一份原始文件快照，大文件长期积累可能占用较多磁盘空间
3. **diff 仅支持纯文本层面**：PDF/DOCX 的格式变更（如排版、图表位置）在 diff 中不可见
4. **无并发编辑保护**：多人同时编辑同一监控目录中的文件时，以最后保存者为准
5. **定时任务精度**：基于 asyncio 的定时循环，精度约秒级，非实时

## 验证

| 测试项 | 方法 | 预期结果 |
|--------|------|---------|
| 启动扫描 | 启动服务，监控目录有新文件 | DB 中出现新文档，index_status=pending |
| watchdog 监听 | 运行中在监控目录创建文件 | DB 中出现新文档，index_status=pending |
| 内容变更 | 修改监控目录中的文件 | 创建新版本，index_status=stale |
| 文件删除 | 删除监控目录中的文件 | file_status=disappeared，版本历史保留 |
| 文件放回 | 将已删除文件放回目录 | file_status=active，创建新版本 |
| 重命名 | 重命名监控目录中的文件 | source_path 更新，title 更新 |
| 手动同步 | 调用 POST /sync | 所有 pending/stale 文档执行 Pipeline |
| 定时同步 | 等待 12h | 自动执行 Pipeline |
| 版本列表 | GET /documents/{id}/versions | 返回完整版本链 |
| diff | GET /documents/{id}/versions/diff | 返回正确的 unified diff |
| 回滚 | POST /documents/{id}/versions/{v}/rollback | 新版本内容等于目标版本 |
| 搜索标记 | 搜索含 stale 文档 | 返回旧索引 + "索引待更新"标记 |
| 存量迁移 | 首次启动 | 老文档自动补建 version=1 |
| 前端版本面板 | 点击版本列表项 | 展示内容/diff |
