# 知识图谱前端可视化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在前端新增知识图谱可视化页面，展示全局实体和关系的力导向图，支持搜索筛选和实体详情面板。

**Architecture:** 后端新增 `GET /graph/full` API 一次返回全图数据（nodes + links）。前端使用 react-force-graph-2d 渲染力导向图，搜索框实时高亮过滤，点击节点弹出右侧详情面板。

**Tech Stack:** React 19, TypeScript, react-force-graph-2d, FastAPI, Neo4j

## Global Constraints

- react-force-graph-2d 版本 ^1.27.0
- 内联样式（项目无 CSS 框架）
- 排除 Document 节点和 RELATED_TO 边（仅展示实体间语义关系）
- sources 属性存储为 JSON 字符串，API 返回时需解析为对象列表
- Neo4j 2025+ 使用 `NOT x IN` 而非 `x NOT IN`

---

### Task 1: Backend — GET /graph/full API

**Files:**
- Modify: `src/db/neo4j_client.py` (add method after line 399, before `get_related_docs`)
- Modify: `src/core/knowledge_base.py` (add method after line 252)
- Modify: `src/api/routes.py` (add endpoint after line 164)

**Interfaces:**
- Produces: `Neo4jClient.get_full_graph() -> dict` with keys `nodes: list[dict]` and `links: list[dict]`
- Each node: `{name, type, description, sources: list[dict]}`
- Each link: `{source, target, type, description}`

- [ ] **Step 1: Add `get_full_graph()` to Neo4jClient**

In `src/db/neo4j_client.py`, add this method after `find_entities_by_source` (before `get_related_docs`):

```python
    async def get_full_graph(self) -> dict:
        """返回全图数据：所有实体节点 + 所有实体间关系。"""
        async with self._driver.session() as session:
            # 1. 查询所有实体节点（排除 Document）
            node_result = await session.run(
                """
                MATCH (e)
                WHERE NOT e:Document AND e.sources IS NOT NULL
                RETURN e.name AS name, e.description AS description,
                       e.sources AS sources, labels(e) AS labels
                """
            )
            node_records = await node_result.data()
            nodes = []
            for r in node_records:
                labels = r["labels"]
                entity_type = next(
                    (l for l in labels if l != "Document"), "Unknown"
                )
                sources_raw = r["sources"] or "[]"
                nodes.append({
                    "name": r["name"],
                    "type": entity_type,
                    "description": r["description"] or "",
                    "sources": json.loads(sources_raw),
                })

            # 2. 查询所有实体间关系（排除 Document 节点和 RELATED_TO）
            link_result = await session.run(
                """
                MATCH (a)-[r]->(b)
                WHERE NOT a:Document AND NOT b:Document
                  AND type(r) <> 'RELATED_TO'
                RETURN a.name AS source, b.name AS target,
                       type(r) AS type, r.description AS description
                """
            )
            link_records = await link_result.data()
            links = [
                {
                    "source": r["source"],
                    "target": r["target"],
                    "type": r["type"],
                    "description": r["description"] or "",
                }
                for r in link_records
            ]

            return {"nodes": nodes, "links": links}
```

- [ ] **Step 2: Add `get_full_graph()` to KnowledgeBase**

In `src/core/knowledge_base.py`, add this method at the end of the class (after `get_neighbors`):

```python
    async def get_full_graph(self) -> dict[str, Any]:
        """返回全图数据（nodes + links）。"""
        return await self._neo4j.get_full_graph()
```

- [ ] **Step 3: Add `GET /graph/full` endpoint**

In `src/api/routes.py`, add this endpoint **before** the existing `/graph/entity/{name}` endpoint (so it doesn't get matched as a name parameter). Insert after line 141:

```python
@router.get("/graph/full")
async def get_full_graph(
    kb: KnowledgeBase = Depends(get_kb),
) -> dict[str, Any]:
    """返回全图数据（所有实体 + 关系），供前端力导向图渲染。"""
    return await kb.get_full_graph()
```

- [ ] **Step 4: Verify imports and commit**

Run: `cd /Users/caoyurui/study/team-knowledge-base && uv run python -c "from src.api.routes import router; print('imports OK')"`

```bash
git add src/db/neo4j_client.py src/core/knowledge_base.py src/api/routes.py
git commit -m "feat: GET /graph/full — 全图数据 API"
```

---

### Task 2: Frontend — 安装依赖 + API Client

**Files:**
- Modify: `frontend/package.json` (add dependency)
- Modify: `frontend/src/api/client.ts` (add types + method)

**Interfaces:**
- Produces: `GraphData` TypeScript interface and `api.getFullGraph()` method

- [ ] **Step 1: Install react-force-graph-2d**

```bash
cd /Users/caoyurui/study/team-knowledge-base/frontend && npm install react-force-graph-2d@^1.27.0
```

- [ ] **Step 2: Add types and API method to client.ts**

In `frontend/src/api/client.ts`, add these types after the `DocumentList` interface (after line 23):

```typescript
export interface GraphNode {
  name: string
  type: string
  description: string
  sources: Array<{ doc_id: string; doc_title: string; chunk_index: number }>
}

export interface GraphLink {
  source: string
  target: string
  type: string
  description: string
}

export interface GraphData {
  nodes: GraphNode[]
  links: GraphLink[]
}
```

Add this method inside the `api` object, after `getNeighbors` (after line 78):

```typescript
  getFullGraph() {
    return request<GraphData>('/graph/full')
  },
```

- [ ] **Step 3: Verify build**

```bash
cd /Users/caoyurui/study/team-knowledge-base/frontend && npx tsc --noEmit 2>&1 | head -20
```

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/api/client.ts
git commit -m "feat: frontend — react-force-graph-2d + GraphData types"
```

---

### Task 3: Frontend — 图谱组件 + 详情面板

**Files:**
- Create: `frontend/src/components/KnowledgeGraph.tsx`
- Create: `frontend/src/components/EntityDetailPanel.tsx`

**Interfaces:**
- Consumes: `GraphNode`, `GraphLink` from `api/client.ts`
- Produces: `KnowledgeGraph` component (props: `nodes, links, onNodeClick`)
- Produces: `EntityDetailPanel` component (props: `node, links, onClose`)

- [ ] **Step 1: Create EntityDetailPanel.tsx**

Create `frontend/src/components/EntityDetailPanel.tsx`:

```tsx
import { useNavigate } from 'react-router-dom'
import type { GraphNode, GraphLink } from '../api/client'

interface Props {
  node: GraphNode
  links: GraphLink[]
  onClose: () => void
}

export function EntityDetailPanel({ node, links, onClose }: Props) {
  const navigate = useNavigate()

  // 找出与该节点相关的关系（作为 source 或 target）
  const relatedLinks = links.filter(
    (l) => l.source === node.name || l.target === node.name
  )

  return (
    <div
      style={{
        width: 320,
        borderLeft: '1px solid #e8e8e8',
        background: '#fff',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'auto',
      }}
    >
      {/* 头部 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '16px',
          borderBottom: '1px solid #f0f0f0',
        }}
      >
        <div>
          <div style={{ fontWeight: 700, fontSize: 16 }}>{node.name}</div>
          <div style={{ fontSize: 12, color: '#888', marginTop: 2 }}>
            {node.type}
          </div>
        </div>
        <button
          onClick={onClose}
          style={{
            border: 'none',
            background: 'none',
            fontSize: 18,
            cursor: 'pointer',
            color: '#999',
            padding: '4px 8px',
          }}
        >
          ×
        </button>
      </div>

      {/* 描述 */}
      {node.description && (
        <div style={{ padding: 16, borderBottom: '1px solid #f0f0f0' }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
            描述
          </div>
          <div style={{ fontSize: 13, color: '#555', lineHeight: 1.6 }}>
            {node.description}
          </div>
        </div>
      )}

      {/* 来源文档 */}
      {node.sources.length > 0 && (
        <div style={{ padding: 16, borderBottom: '1px solid #f0f0f0' }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
            来源文档
          </div>
          {node.sources.map((s, i) => (
            <div
              key={i}
              onClick={() => navigate(`/documents/${s.doc_id}`)}
              style={{
                fontSize: 13,
                color: '#1890ff',
                cursor: 'pointer',
                padding: '3px 0',
              }}
            >
              · {s.doc_title}
            </div>
          ))}
        </div>
      )}

      {/* 关联关系 */}
      {relatedLinks.length > 0 && (
        <div style={{ padding: 16 }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
            关联关系
          </div>
          {relatedLinks.map((l, i) => {
            const isSource = l.source === node.name
            const otherName = isSource ? l.target : l.source
            const arrow = isSource ? '→' : '←'
            return (
              <div
                key={i}
                style={{ fontSize: 13, color: '#555', padding: '3px 0' }}
              >
                {arrow} {l.type}: {otherName}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Create KnowledgeGraph.tsx**

Create `frontend/src/components/KnowledgeGraph.tsx`:

```tsx
import { useRef, useCallback, useMemo } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import type { GraphNode, GraphLink } from '../api/client'

// 实体类型 → 颜色映射
const TYPE_COLORS: Record<string, string> = {
  Person: '#4A90D9',
  Company: '#E6A23C',
  Facility: '#67C23A',
  Space: '#909399',
  Building: '#909399',
}
const DEFAULT_COLOR = '#B37FEB'

interface FgNode {
  id: string
  name: string
  type: string
  description: string
  sources: GraphNode['sources']
  x?: number
  y?: number
}

interface FgLink {
  source: string | FgNode
  target: string | FgNode
  type: string
  description: string
}

interface Props {
  nodes: GraphNode[]
  links: GraphLink[]
  searchQuery: string
  onNodeClick: (node: GraphNode) => void
  selectedNodeName: string | null
}

export function KnowledgeGraph({
  nodes,
  links,
  searchQuery,
  onNodeClick,
  selectedNodeName,
}: Props) {
  const graphRef = useRef<any>(null)

  // 转换数据格式
  const fgNodes: FgNode[] = useMemo(
    () =>
      nodes.map((n) => ({
        id: n.name,
        name: n.name,
        type: n.type,
        description: n.description,
        sources: n.sources,
      })),
    [nodes]
  )

  const fgLinks: FgLink[] = useMemo(
    () =>
      links.map((l) => ({
        source: l.source,
        target: l.target,
        type: l.type,
        description: l.description,
      })),
    [links]
  )

  // 搜索匹配集合
  const matchSet = useMemo(() => {
    if (!searchQuery.trim()) return null
    const q = searchQuery.toLowerCase()
    return new Set(
      fgNodes.filter((n) => n.name.toLowerCase().includes(q)).map((n) => n.id)
    )
  }, [searchQuery, fgNodes])

  // 选中节点的邻居集合
  const neighborSet = useMemo(() => {
    if (!selectedNodeName) return null
    const set = new Set<string>([selectedNodeName])
    for (const l of fgLinks) {
      const src = typeof l.source === 'string' ? l.source : l.source.id
      const tgt = typeof l.target === 'string' ? l.target : l.target.id
      if (src === selectedNodeName) set.add(tgt)
      if (tgt === selectedNodeName) set.add(src)
    }
    return set
  }, [selectedNodeName, fgLinks])

  const nodeColor = useCallback(
    (node: any) => {
      const n = node as FgNode
      // 搜索过滤：不匹配的节点变透明
      if (matchSet && !matchSet.has(n.id)) return 'rgba(200,200,200,0.15)'
      // 选中高亮：非邻居变灰
      if (neighborSet && !neighborSet.has(n.id)) return 'rgba(200,200,200,0.3)'
      return TYPE_COLORS[n.type] || DEFAULT_COLOR
    },
    [matchSet, neighborSet]
  )

  const nodeCanvasObject = useCallback(
    (node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const n = node as FgNode
      const label = n.name
      const fontSize = 10 / globalScale
      const r = 6 / globalScale

      // 节点圆
      ctx.beginPath()
      ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
      ctx.fillStyle = nodeColor(n)
      ctx.fill()

      // 选中节点的边框
      if (n.id === selectedNodeName) {
        ctx.strokeStyle = '#333'
        ctx.lineWidth = 2 / globalScale
        ctx.stroke()
      }

      // 标签
      ctx.font = `${fontSize}px Sans-Serif`
      ctx.textAlign = 'left'
      ctx.textBaseline = 'top'
      ctx.fillStyle =
        matchSet && !matchSet.has(n.id)
          ? 'rgba(100,100,100,0.15)'
          : '#333'
      ctx.fillText(label, node.x + r + 2 / globalScale, node.y - fontSize / 2)
    },
    [nodeColor, selectedNodeName, matchSet]
  )

  const linkColor = useCallback(
    (link: any) => {
      if (!neighborSet) return 'rgba(150,150,150,0.4)'
      const src = typeof link.source === 'string' ? link.source : link.source.id
      const tgt = typeof link.target === 'string' ? link.target : link.target.id
      if (neighborSet.has(src) && neighborSet.has(tgt)) return '#666'
      return 'rgba(150,150,150,0.1)'
    },
    [neighborSet]
  )

  const linkLabel = useCallback((link: any) => {
    return link.type || ''
  }, [])

  const handleClick = useCallback(
    (node: any) => {
      if (!node) return
      const n = node as FgNode
      onNodeClick({
        name: n.name,
        type: n.type,
        description: n.description,
        sources: n.sources,
      })
    },
    [onNodeClick]
  )

  return (
    <ForceGraph2D
      ref={graphRef}
      graphData={{ nodes: fgNodes, links: fgLinks }}
      nodeId="id"
      nodeColor={nodeColor}
      nodeCanvasObject={nodeCanvasObject}
      nodePointerAreaPaint={nodeCanvasObject}
      linkColor={linkColor}
      linkLabel={linkLabel}
      linkDirectionalArrowLength={4}
      linkDirectionalArrowRelPos={0.9}
      onNodeClick={handleClick}
      onBackgroundClick={() => onNodeClick(null as any)}
      width={undefined}
      height={undefined}
      cooldownTicks={100}
    />
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/EntityDetailPanel.tsx frontend/src/components/KnowledgeGraph.tsx
git commit -m "feat: KnowledgeGraph + EntityDetailPanel 组件"
```

---

### Task 4: Frontend — 页面 + 路由 + 导航

**Files:**
- Create: `frontend/src/pages/GraphPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Layout.tsx`

**Interfaces:**
- Consumes: `KnowledgeGraph`, `EntityDetailPanel`, `api.getFullGraph()`

- [ ] **Step 1: Create GraphPage.tsx**

Create `frontend/src/pages/GraphPage.tsx`:

```tsx
import { useEffect, useState, useCallback } from 'react'
import { api, type GraphNode, type GraphLink } from '../api/client'
import { KnowledgeGraph } from '../components/KnowledgeGraph'
import { EntityDetailPanel } from '../components/EntityDetailPanel'

export function GraphPage() {
  const [nodes, setNodes] = useState<GraphNode[]>([])
  const [links, setLinks] = useState<GraphLink[]>([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)

  useEffect(() => {
    api
      .getFullGraph()
      .then((data) => {
        setNodes(data.nodes)
        setLinks(data.links)
      })
      .catch((err) => alert('加载图谱失败: ' + err.message))
      .finally(() => setLoading(false))
  }, [])

  const handleNodeClick = useCallback((node: GraphNode | null) => {
    setSelectedNode(node?.name ? node : null)
  }, [])

  return (
    <div style={{ display: 'flex', flex: 1, position: 'relative', overflow: 'hidden' }}>
      {/* 搜索框 */}
      <div
        style={{
          position: 'absolute',
          top: 16,
          left: 16,
          zIndex: 10,
        }}
      >
        <input
          type="text"
          placeholder="搜索实体..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{
            width: 240,
            padding: '8px 12px',
            borderRadius: 6,
            border: '1px solid #d9d9d9',
            fontSize: 14,
            outline: 'none',
            boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
          }}
        />
      </div>

      {/* 图谱区域 */}
      <div style={{ flex: 1, position: 'relative' }}>
        {loading ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              color: '#999',
            }}
          >
            加载图谱中...
          </div>
        ) : (
          <KnowledgeGraph
            nodes={nodes}
            links={links}
            searchQuery={searchQuery}
            onNodeClick={handleNodeClick}
            selectedNodeName={selectedNode?.name ?? null}
          />
        )}
      </div>

      {/* 详情面板 */}
      {selectedNode && (
        <EntityDetailPanel
          node={selectedNode}
          links={links}
          onClose={() => setSelectedNode(null)}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 2: Add route in App.tsx**

Modify `frontend/src/App.tsx`:

```tsx
import { Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { DocumentListPage } from './pages/DocumentListPage'
import { DocumentDetailPage } from './pages/DocumentDetailPage'
import { GraphPage } from './pages/GraphPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<DocumentListPage />} />
        <Route path="/documents/:id" element={<DocumentDetailPage />} />
        <Route path="/graph" element={<GraphPage />} />
      </Route>
    </Routes>
  )
}
```

- [ ] **Step 3: Add nav link in Layout.tsx**

In `frontend/src/components/Layout.tsx`, add a Link import and nav link. Change the header section:

Replace the existing `<Link>` and `<div style={{ flex: 1 }} />` block with:

```tsx
        <Link to="/" style={{ fontSize: 18, fontWeight: 700, textDecoration: 'none', color: '#333' }}>
          团队知识库
        </Link>
        <Link
          to="/graph"
          style={{ fontSize: 14, textDecoration: 'none', color: '#666', padding: '4px 8px' }}
        >
          知识图谱
        </Link>
        <div style={{ flex: 1 }} />
```

- [ ] **Step 4: Verify build and commit**

```bash
cd /Users/caoyurui/study/team-knowledge-base/frontend && npx tsc --noEmit 2>&1 | head -20
```

```bash
git add frontend/src/pages/GraphPage.tsx frontend/src/App.tsx frontend/src/components/Layout.tsx
git commit -m "feat: GraphPage + 路由 + 导航"
```

---

### Task 5: 端到端验证

**Files:** 无代码修改，仅验证。

- [ ] **Step 1: 启动后端服务**

```bash
cd /Users/caoyurui/study/team-knowledge-base && uv run uvicorn src.main:app --host 127.0.0.1 --port 8001
```

- [ ] **Step 2: 验证 GET /graph/full API**

```bash
curl -s http://127.0.0.1:8001/graph/full | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'nodes: {len(d[\"nodes\"])}')
print(f'links: {len(d[\"links\"])}')
for n in d['nodes'][:5]:
    print(f'  - {n[\"name\"]} ({n[\"type\"]})')
"
```

Expected: nodes > 0, links > 0

- [ ] **Step 3: 启动前端 dev server**

```bash
cd /Users/caoyurui/study/team-knowledge-base/frontend && npm run dev
```

- [ ] **Step 4: 浏览器验证**

打开 `http://localhost:5173/graph`，确认：
1. 力导向图正确渲染所有实体节点
2. 搜索框输入关键词能高亮匹配节点
3. 点击节点弹出右侧详情面板
4. 详情面板显示描述、来源文档、关联关系
5. 点击来源文档可跳转到文件详情页
