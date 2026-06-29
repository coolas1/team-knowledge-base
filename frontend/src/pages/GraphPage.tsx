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
