# 知识图谱前端可视化设计

## 概述

在现有三层知识图谱（chunk 级抽取 → 文档内聚合 → 跨文档关联）基础上，新增前端图谱可视化功能。用户可以在浏览器中浏览全局知识图谱，搜索筛选实体，查看实体详情和溯源信息。

## 设计决策

| 维度 | 决策 |
|------|------|
| 展示范围 | 全局单一图谱（所有实体和关系） |
| 加载策略 | 全量加载 + 搜索筛选 |
| 可视化库 | react-force-graph-2d |
| 节点交互 | 点击展开右侧详情面板 |

## 页面结构

新增 `/graph` 路由，独立图谱浏览页。Header 导航栏添加「知识图谱」链接。

布局：左侧力导向图全占满，右侧可收起的详情面板（320px），顶部浮动搜索栏。

```
┌─────────────────────────────────────────────────┐
│  团队知识库    [文件] [知识图谱]      [上传文件]  │
├─────────────────────────────────────────────────┤
│  ┌─ 搜索框(浮动) ──────────────┐               │
│  │                             │  详情面板       │
│  │      力 导 向 图            │  (可收起)       │
│  │    (react-force-graph)      │  实体名/类型    │
│  │                             │  描述           │
│  │                             │  来源文档列表    │
│  │                             │  关联关系列表    │
│  └─────────────────────────────┘               │
└─────────────────────────────────────────────────┘
```

## 后端 API

### 新增 `GET /graph/full`

一次返回全图数据，供前端力导向图渲染。

**Response:**

```json
{
  "nodes": [
    {
      "name": "李明",
      "type": "Person",
      "description": "A栋楼管，负责与停车场相关的事务",
      "sources": [
        {"doc_id": "uuid-1", "doc_title": "test-doc-a.md", "chunk_index": 0},
        {"doc_id": "uuid-2", "doc_title": "test-doc-b.md", "chunk_index": 0}
      ]
    }
  ],
  "links": [
    {
      "source": "李明",
      "target": "A栋",
      "type": "RESPONSIBLE_FOR",
      "description": "李明作为A栋楼管，负责A栋的日常运维"
    }
  ]
}
```

**实现要点：**
- 在 `Neo4jClient` 新增 `get_full_graph()` 方法
- 查询所有带 sources 属性的实体节点 + 所有实体间关系
- 排除 Document 节点（不参与图谱拓扑）
- 关系边只返回实体间的语义关系（排除 RELATED_TO）

**调用链：** `routes.py → knowledge_base.py → neo4j_client.get_full_graph()`

### 前端 API Client 新增

```typescript
getFullGraph() {
  return request<GraphData>('/graph/full')
}
```

## 前端组件

### 新增文件

| 文件 | 职责 |
|------|------|
| `pages/GraphPage.tsx` | 图谱页面容器，管理数据和面板状态 |
| `components/KnowledgeGraph.tsx` | 封装 react-force-graph-2d，处理渲染和交互 |
| `components/EntityDetailPanel.tsx` | 右侧实体详情面板 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `App.tsx` | 新增 `/graph` 路由 |
| `components/Layout.tsx` | Header 添加「知识图谱」导航链接 |
| `api/client.ts` | 新增 `getFullGraph()` 方法 + `GraphData` 类型 |

## 交互设计

### 搜索筛选

- 顶部浮动搜索框，输入关键词实时过滤
- 匹配节点正常显示 + 高亮边框
- 非匹配节点降低透明度（opacity 0.15）
- 空搜索时显示全部节点

### 节点视觉

按 entity type 分配颜色（固定映射）：

| 类型 | 颜色 |
|------|------|
| Person | #4A90D9（蓝） |
| Company | #E6A23C（橙） |
| Facility | #67C23A（绿） |
| Space / Building | #909399（灰） |
| 其他 | #B37FEB（紫） |

- 节点大小：统一半径 6px
- 节点标签：显示实体 name（字号 10px，偏移显示）
- 关系标签：显示关系 type（字号 8px，边中间位置）

### 节点点击

1. 高亮该节点 + 直接关联的边 + 邻居节点
2. 右侧滑出详情面板（320px 宽度）
3. 点击画布空白处或面板关闭按钮收起面板

### 详情面板内容

```
┌──────────────────────────┐
│ 李明                 [×] │
│ Person                   │
├──────────────────────────┤
│ 📝 描述                  │
│ A栋楼管，负责与停车场相   │
│ 关的事务                  │
├──────────────────────────┤
│ 📄 来源文档              │
│ · test-doc-a.md          │
│ · test-doc-b.md          │
├──────────────────────────┤
│ 🔗 关联关系              │
│ → RESPONSIBLE_FOR: A栋   │
│ → RESPONSIBLE_FOR:       │
│   阳光科技园区地下停车场   │
└──────────────────────────┘
```

- 来源文档列表：显示 doc_title，可点击跳转到 `/documents/:doc_id`
- 关联关系列表：显示方向箭头 + 关系类型 + 目标实体名

### 拖拽 / 缩放 / 平移

react-force-graph-2d 内置支持，无需额外实现。

## 依赖变更

```json
// 新增
"react-force-graph-2d": "^1.27.0"
```

无其他新增依赖。

## 不包含

- 3D 可视化
- 图谱编辑（增删节点/关系）
- 导出/分享图谱
- Document↔Document RELATED_TO 边的可视化（这些是元数据关系，不属于知识图谱拓扑）
