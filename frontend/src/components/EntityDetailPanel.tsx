import { useNavigate } from 'react-router-dom'
import type { GraphNode, GraphLink } from '../api/client'

interface Props {
  node: GraphNode
  nodes: GraphNode[]
  links: GraphLink[]
  onClose: () => void
}

export function EntityDetailPanel({ node, nodes, links, onClose }: Props) {
  const navigate = useNavigate()

  // 找出与该节点相关的关系（作为 source 或 target）
  const relatedLinks = links.filter(
    (l) => l.source === node.id || l.target === node.id
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
            {node.type} · {node.namespace === 'public' ? '公共知识' : '团队知识'}
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
            const isSource = l.source === node.id
            const otherId = isSource ? l.target : l.source
            const otherName = nodes.find((candidate) => candidate.id === otherId)?.name || otherId
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
