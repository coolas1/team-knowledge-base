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
  onNodeClick: (node: GraphNode | null) => void
  selectedNodeId: string | null
}

export function KnowledgeGraph({
  nodes,
  links,
  searchQuery,
  onNodeClick,
  selectedNodeId,
}: Props) {
  const graphRef = useRef<any>(null)

  // 转换数据格式
  const fgNodes: FgNode[] = useMemo(
    () =>
      nodes.map((n) => ({
        id: n.id || n.name,
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
    if (!selectedNodeId) return null
    const set = new Set<string>([selectedNodeId])
    for (const l of fgLinks) {
      const src = typeof l.source === 'string' ? l.source : (l.source as FgNode).id
      const tgt = typeof l.target === 'string' ? l.target : (l.target as FgNode).id
      if (src === selectedNodeId) set.add(tgt)
      if (tgt === selectedNodeId) set.add(src)
    }
    return set
  }, [selectedNodeId, fgLinks])

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
      const fontSize = Math.max(10 / globalScale, 2)
      const r = 6 / globalScale

      // 节点圆
      ctx.beginPath()
      ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
      ctx.fillStyle = nodeColor(n)
      ctx.fill()

      // 选中节点的边框
      if (n.id === selectedNodeId) {
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
    [nodeColor, selectedNodeId, matchSet]
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
        id: n.id,
        name: n.name,
        namespace: nodes.find((item) => item.id === n.id)?.namespace || '',
        type: n.type,
        description: n.description,
        sources: n.sources,
      })
    },
    [onNodeClick, nodes]
  )

  return (
    <ForceGraph2D
      ref={graphRef}
      graphData={{ nodes: fgNodes, links: fgLinks }}
      nodeId="id"
      nodeColor={nodeColor}
      nodeCanvasObject={nodeCanvasObject}
      linkColor={linkColor}
      linkLabel={linkLabel}
      linkDirectionalArrowLength={4}
      linkDirectionalArrowRelPos={0.9}
      onNodeClick={handleClick}
      onBackgroundClick={() => onNodeClick(null)}
      cooldownTicks={100}
    />
  )
}
