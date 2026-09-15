import { useRef, useCallback, useEffect, useMemo } from 'react'
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
  degree: number
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
  const fittedRef = useRef(false)

  const degreeByName = useMemo(() => {
    const degrees = new Map<string, number>()
    for (const link of links) {
      degrees.set(link.source, (degrees.get(link.source) || 0) + 1)
      degrees.set(link.target, (degrees.get(link.target) || 0) + 1)
    }
    return degrees
  }, [links])

  // 转换数据格式
  const fgNodes: FgNode[] = useMemo(
    () =>
      nodes.map((n) => ({
        id: n.name,
        name: n.name,
        type: n.type,
        description: n.description,
        sources: n.sources,
        degree: degreeByName.get(n.name) || 0,
      })),
    [nodes, degreeByName]
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
      const src = typeof l.source === 'string' ? l.source : (l.source as FgNode).id
      const tgt = typeof l.target === 'string' ? l.target : (l.target as FgNode).id
      if (src === selectedNodeName) set.add(tgt)
      if (tgt === selectedNodeName) set.add(src)
    }
    return set
  }, [selectedNodeName, fgLinks])

  const defaultLabelSet = useMemo(() => {
    if (fgNodes.length <= 30) return new Set(fgNodes.map((node) => node.id))
    return new Set(
      [...fgNodes]
        .sort((left, right) => right.degree - left.degree || left.name.localeCompare(right.name))
        .slice(0, 12)
        .map((node) => node.id),
    )
  }, [fgNodes])

  useEffect(() => {
    fittedRef.current = false
    const graph = graphRef.current
    graph?.d3Force('charge')?.strength(-110)
    graph?.d3Force('link')?.distance(70)
    graph?.d3ReheatSimulation()
  }, [fgNodes, fgLinks])

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

  // 指针命中区域：默认识别半径与绘制半径同小（6/globalScale），千级
  // 节点时几乎点不中；放大到 2 倍绘制半径（design D11）。
  const nodePointerAreaPaint = useCallback(
    (node: any, color: string, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const r = 12 / globalScale
      ctx.beginPath()
      ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
      ctx.fillStyle = color
      ctx.fill()
    },
    []
  )

  const nodeCanvasObject = useCallback(
    (node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const n = node as FgNode
      const identity = n.id || n.name
      const label = n.name
      const fontSize = Math.max(10 / globalScale, 2)
      const r = (6 + Math.min(4, Math.log2((n.degree || 0) + 1))) / globalScale

      // 节点圆
      ctx.beginPath()
      ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
      ctx.fillStyle = nodeColor(n)
      ctx.fill()

      // 选中节点的边框
      if (identity === selectedNodeName) {
        ctx.strokeStyle = '#333'
        ctx.lineWidth = 2 / globalScale
        ctx.stroke()
      }

      const showLabel = globalScale >= 1.8
        || defaultLabelSet.has(identity)
        || Boolean(matchSet?.has(identity))
        || Boolean(neighborSet?.has(identity))
      if (!showLabel) return

      // 密集图默认只标注枢纽；搜索、选中邻域和放大后显示其余标签。
      ctx.font = `${fontSize}px Sans-Serif`
      ctx.textAlign = 'left'
      ctx.textBaseline = 'top'
      ctx.fillStyle =
        matchSet && !matchSet.has(identity)
          ? 'rgba(100,100,100,0.15)'
          : '#333'
      ctx.fillText(label, node.x + r + 2 / globalScale, node.y - fontSize / 2)
    },
    [nodeColor, selectedNodeName, matchSet, neighborSet, defaultLabelSet]
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

  const handleEngineStop = useCallback(() => {
    if (fittedRef.current || fgNodes.length === 0) return
    fittedRef.current = true
    graphRef.current?.zoomToFit(600, 60)
  }, [fgNodes.length])

  return (
    <ForceGraph2D
      ref={graphRef}
      graphData={{ nodes: fgNodes, links: fgLinks }}
      nodeId="id"
      nodeColor={nodeColor}
      nodeCanvasObject={nodeCanvasObject}
      nodePointerAreaPaint={nodePointerAreaPaint}
      linkColor={linkColor}
      linkLabel={linkLabel}
      linkDirectionalArrowLength={4}
      linkDirectionalArrowRelPos={0.9}
      onNodeClick={handleClick}
      onBackgroundClick={() => onNodeClick(null)}
      onEngineStop={handleEngineStop}
      cooldownTicks={180}
      d3VelocityDecay={0.35}
    />
  )
}
