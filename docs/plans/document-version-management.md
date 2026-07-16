# 文档版本管理与目录监控系统实施计划

> **参考设计文档:** `docs/specs/document-version-management-design.md`

**Goal:** 为知识库新增目录监控、文档版本管理（diff + 回滚）、Pipeline 调度能力。

**依赖链:** 数据模型 → 配置 → 感知层(FileWatcher) → 调度层(Scheduler) → Pipeline 改造 → 版本管理 → API → 前端

---

## Phase 1: 数据模型 + 配置

### Task 1: 数据模型改造

**产出:** Document 表新增字段 + DocumentVersion 新表 + 迁移脚本

**文件:**
- `src/db/models.py` — Document 新增 `source_type`, `source_path`, `watch_dir`, `index_status`, `file_status`；新增 `DocumentVersion` 模型
- `src/db/postgres.py` — `init_db()` 适配新表；新增 `migrate_legacy_documents()` 迁移函数

**步骤:**
1. Document 模型：新增 `source_type`（默认 `manual`）、`source_path`、`watch_dir`
2. Document 模型：将 `status` 重命名为 `index_status`，新增 `file_status`（默认 `active`）
3. 新增 `DocumentVersion` 模型：id, doc_id(FK), version, raw_text, content_hash, file_path, change_type, change_summary, created_at
4. `init_db()` 中 `create_all` 自动建表
5. 新增 `migrate_legacy_documents(session)`：补 source_type、迁移 status→index_status、创建 version=1
6. 验证：`docker compose restart` postgres → 服务启动 → 老文档自动迁移

**验证:**
```bash
# 启动服务后检查
uv run python -c "import asyncio; from src.db.postgres import async_session_factory; ..."
# 检查 documents 表有 index_status/file_status 字段
# 检查 document_versions 表已创建
# 检查老文档有 version=1 记录
```

---

### Task 2: 目录监控配置

**产出:** `config/watch_config.yaml` + 配置加载模块

**文件:**
- `config/watch_config.yaml` — 监控配置
- `src/watcher/__init__.py` — 模块入口
- `src/watcher/config.py` — WatchConfig 数据类 + 加载函数

**步骤:**
1. 创建 `config/watch_config.yaml`（默认模板，enabled=false）
2. 创建 `WatchConfig` 数据类（directories, exclude_patterns, pipeline.schedule_hours）
3. 创建 `load_watch_config()` 函数，从 YAML 加载
4. 验证：加载配置 → 打印配置内容

---

## Phase 2: 感知层 + 调度层

### Task 3: 添加 watchdog 依赖

**产出:** watchdog 包安装完成

**步骤:**
1. `uv add watchdog`
2. 验证：`uv run python -c "from watchdog.observers import Observer; print('ok')"`

---

### Task 4: FileWatcher 感知层

**产出:** 文件变更自动检测 + 版本记录创建 + 重命名识别

**文件:**
- `src/watcher/watcher.py` — FileWatcher 类

**步骤:**
1. 实现 `FileWatcher.__init__(config)` — 接收配置
2. 实现 `_full_scan()` — 遍历配置目录，对比 DB，创建/更新 Document + Version 记录
3. 实现 `_start_watchdog()` — 启动 Observer 后台线程
4. 实现 `_WatchdogHandler` — 处理 on_created/on_modified/on_deleted/on_moved
5. 实现 `_on_file_changed(path)` — 计算 hash、查找文档、创建版本、标记 stale
6. 实现 `_on_file_deleted(path)` — 标记 file_status=disappeared
7. 实现 `_on_file_moved(src, dest)` — 更新 source_path/title，hash 配对降级
8. 实现 `_detect_rename_by_hash()` — hash 配对检测重命名
9. 实现排除规则过滤（fnmatch 匹配 exclude_patterns）
10. 实现 `start()` / `stop()`

**依赖:** Task 1 (models), Task 2 (config), Task 3 (watchdog)

**验证:**
```bash
# 1. 配置 enabled=true + 一个测试目录
# 2. 启动服务
# 3. 在测试目录创建 md 文件
# 4. 检查 DB 出现新 Document + Version
# 5. 修改文件 → 新版本
# 6. 删除文件 → disappeared
```

---

### Task 5: Pipeline 调度器

**产出:** 定时任务 + 手动触发 API

**文件:**
- `src/watcher/scheduler.py` — PipelineScheduler 类

**步骤:**
1. 实现 `PipelineScheduler.__init__(kb)` — 接收 KnowledgeBase 实例
2. 实现 `start()` — 启动 asyncio 定时循环（每 N 小时执行一次）
3. 实现 `stop()` — 取消定时任务
4. 实现 `_scheduled_run()` — 查询 pending/stale 文档，逐个执行 Pipeline
5. 实现 `trigger_manual()` — 立即执行一次
6. 实现 Pipeline 互斥锁（同一文档不并发执行）

**依赖:** Task 1 (models)

**验证:**
```bash
# 1. 手动触发 POST /sync
# 2. 检查 pending 文档执行 Pipeline → index_status=indexed
```

---

## Phase 3: Pipeline 改造 + 版本管理

### Task 6: Pipeline 适配版本管理

**产出:** Pipeline 执行时创建版本快照，状态字段切换为 index_status

**文件:**
- `src/pipeline/pipeline.py` — process_file / reindex_document 改造

**步骤:**
1. `process_file` 中：将原始文件复制到 `uploads/{doc_id}/versions/v{version}_{filename}`
2. 将所有 `status` 引用改为 `index_status`
3. Pipeline 成功后：`index_status=indexed`（不再是 `status=indexed`）
4. Pipeline 失败后：`index_status=failed`
5. 验证：上传文件 → Pipeline 执行 → 版本快照存在

**依赖:** Task 1 (models)

---

### Task 7: 版本管理核心逻辑

**产出:** 版本查询、diff 计算、回滚功能

**文件:**
- `src/core/version_manager.py` — VersionManager 类

**步骤:**
1. 实现 `list_versions(doc_id)` — 查询版本列表（version DESC）
2. 实现 `get_version(doc_id, version)` — 获取版本详情
3. 实现 `get_version_file(doc_id, version)` — 返回版本快照文件路径
4. 实现 `get_diff(doc_id, from_version, to_version)` — difflib.unified_diff
5. 实现 `rollback(doc_id, target_version)` — 创建新版本 + 触发 re-index
6. 实现 `create_version(doc_id, raw_text, content_hash, file_path, change_type)` — 内部方法

**依赖:** Task 1 (models), Task 6 (pipeline)

**验证:**
```bash
# 1. 上传同一文件两次（不同内容）→ 2 个版本
# 2. GET versions → 列表正确
# 3. GET diff → unified diff 正确
# 4. POST rollback → 新版本创建
```

---

### Task 8: KnowledgeBase 集成

**产出:** 核心业务层适配版本管理和新状态模型

**文件:**
- `src/core/knowledge_base.py` — upload_file / get_document / list_documents / delete_document / search 改造

**步骤:**
1. `upload_file`：设置 `source_type=manual`，创建 version=1
2. `get_document`：响应新增 `index_status`、`file_status`、`source_type`、`version_count`
3. `list_documents`：新增 `file_status` 筛选参数，默认 `file_status=active`
4. `delete_document`：改为软删除（`file_status=disappeared`），保留数据
5. `search`：结果新增 `index_status` 字段
6. 新增 `hard_delete_document()`：保留原有硬删除逻辑，供管理命令使用

**依赖:** Task 1 (models), Task 7 (version_manager)

---

## Phase 4: API 层

### Task 9: 版本管理 + 同步 API

**产出:** 版本查询/diff/回滚 + 同步控制端点

**文件:**
- `src/api/routes.py` — 新增端点

**步骤:**
1. `GET /documents/{doc_id}/versions` — 版本列表
2. `GET /documents/{doc_id}/versions/{version}` — 版本详情
3. `GET /documents/{doc_id}/versions/{version}/file` — 版本快照文件下载
4. `GET /documents/{doc_id}/versions/diff?from=2&to=3` — diff
5. `POST /documents/{doc_id}/versions/{version}/rollback` — 回滚
6. `POST /sync` — 手动触发 Pipeline
7. `GET /sync/status` — 同步状态
8. 适配现有端点的状态字段变更

**依赖:** Task 7 (version_manager), Task 8 (knowledge_base)

---

### Task 10: 生命周期集成

**产出:** 服务启动时初始化所有新组件

**文件:**
- `src/main.py` — lifespan 改造

**步骤:**
1. lifespan 中加载 WatchConfig
2. 执行 `migrate_legacy_documents()`
3. 初始化 FileWatcher + 启动
4. 初始化 PipelineScheduler + 启动
5. 关闭时 stop FileWatcher + Scheduler

**依赖:** Task 4 (watcher), Task 5 (scheduler)

---

## Phase 5: 前端

### Task 11: API Client 扩展

**产出:** 前端 API 层新增版本管理和同步方法

**文件:**
- `frontend/src/api/client.ts`

**步骤:**
1. 新增 `getVersions(docId)` — GET versions
2. 新增 `getVersion(docId, version)` — GET version detail
3. 新增 `getVersionDiff(docId, from, to)` — GET diff
4. 新增 `rollbackVersion(docId, version)` — POST rollback
5. 新增 `triggerSync()` — POST sync
6. 新增 `getSyncStatus()` — GET sync status
7. 适配 Document 类型：新增 `index_status`, `file_status`, `source_type`

**依赖:** Task 9 (API)

---

### Task 12: 版本历史面板组件

**产出:** VersionHistory + DiffView 组件

**文件:**
- `frontend/src/components/VersionHistory.tsx` — 版本列表面板
- `frontend/src/components/DiffView.tsx` — diff 展示组件

**步骤:**
1. VersionHistory：左侧版本列表（版本号、变更类型、时间、统计）
2. VersionHistory：点击版本切换右侧内容（内容/diff Tab）
3. DiffView：解析 unified diff 格式，行级着色（+绿 -红）
4. "回滚到此版本"按钮 + 确认弹窗
5. 内联样式，与零 CSS 架构一致

**依赖:** Task 11 (client)

---

### Task 13: 文档详情页集成

**产出:** DocumentDetailPage 嵌入版本历史面板

**文件:**
- `frontend/src/pages/DocumentDetailPage.tsx`

**步骤:**
1. 左右分栏布局：左侧 VersionHistory + 右侧内容/Diff
2. 头部新增 `index_status` + `file_status` 状态徽章
3. 适配新状态字段显示
4. disappeared 文档的视觉处理（灰显 + 提示）

**依赖:** Task 12 (components)

---

### Task 14: 文档列表页改造

**产出:** 同步状态栏 + 状态筛选

**文件:**
- `frontend/src/pages/DocumentListPage.tsx`

**步骤:**
1. 顶部同步状态栏（监控目录、上次同步时间、待索引数量、立即同步按钮）
2. 新增 file_status / index_status 筛选器
3. disappeared 文档灰显
4. stale 文档标注"⚠ 索引待更新"

**依赖:** Task 11 (client)

---

## Phase 6: 搜索适配

### Task 15: 搜索结果状态标记

**产出:** 搜索结果中 stale 文档显示"索引待更新"标记

**文件:**
- `frontend/src/pages/DocumentListPage.tsx`（或搜索结果组件）

**步骤:**
1. 搜索结果渲染中检查 `index_status`
2. stale 文档结果添加 ⚠ 标记
3. 点击标记提示"索引可能不是最新，点击立即同步"

**依赖:** Task 9 (API)

---

## 实施顺序总结

```
Phase 1 ─────────────────────────────────────────────
  Task 1: 数据模型 ──┐
  Task 2: 配置 ──────┤
                     │
Phase 2 ─────────────┼───────────────────────────────
  Task 3: watchdog ──┤
  Task 4: FileWatcher┤ (依赖 1,2,3)
  Task 5: Scheduler ─┤ (依赖 1)
                     │
Phase 3 ─────────────┼───────────────────────────────
  Task 6: Pipeline ──┤ (依赖 1)
  Task 7: VersionMgr─┤ (依赖 1,6)
  Task 8: KB集成 ────┘ (依赖 1,7)

Phase 4 ─────────────────────────────────────────────
  Task 9: API ─────── (依赖 7,8)
  Task 10: 生命周期 ── (依赖 4,5)

Phase 5 ─────────────────────────────────────────────
  Task 11: Client ─── (依赖 9)
  Task 12: 版本组件 ── (依赖 11)
  Task 13: 详情页 ──── (依赖 12)
  Task 14: 列表页 ──── (依赖 11)

Phase 6 ─────────────────────────────────────────────
  Task 15: 搜索适配 ── (依赖 9)
```

## 预估工作量

| Phase | 预估 | 说明 |
|-------|------|------|
| Phase 1 | 1-2h | 数据模型 + 配置 |
| Phase 2 | 3-4h | FileWatcher + Scheduler |
| Phase 3 | 2-3h | Pipeline + 版本管理 + KB 集成 |
| Phase 4 | 1-2h | API 端点 + 生命周期 |
| Phase 5 | 3-4h | 前端组件 |
| Phase 6 | 1h | 搜索适配 |
| **合计** | **11-16h** | |
