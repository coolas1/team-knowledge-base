import { useState } from 'react'
import { api } from '../api/client'

export function SearchPage() {
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<{ chunks: any[]; related_entities: any[]; related_docs: any[] } | null>(null)
  const [loading, setLoading] = useState(false)

  const run = async () => {
    if (!query.trim()) return
    setLoading(true)
    try {
      setResult(await api.search(query))
    } catch (err: any) {
      alert('搜索失败: ' + err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && run()}
          placeholder="输入搜索内容..."
          style={{ flex: 1, padding: '8px 12px', borderRadius: 6, border: '1px solid #d9d9d9', fontSize: 14 }}
        />
        <button onClick={run} disabled={loading} style={{ padding: '6px 16px', borderRadius: 6, border: '1px solid #1890ff', background: '#1890ff', color: '#fff' }}>
          {loading ? '搜索中...' : '搜索'}
        </button>
      </div>

      {result && (
        <div>
          <h3>相关片段 ({result.chunks.length})</h3>
          {result.chunks.map((c, i) => (
            <div key={i} style={{ padding: 12, marginBottom: 8, border: '1px solid #eee', borderRadius: 6 }}>
              <div style={{ fontWeight: 500, marginBottom: 4 }}>{c.title}</div>
              <div style={{ fontSize: 13, color: '#666' }}>{c.chunk_text}</div>
            </div>
          ))}
          {result.related_entities.length > 0 && (
            <>
              <h3>相关实体</h3>
              <div>{result.related_entities.map((e: any) => e.name).join(', ')}</div>
            </>
          )}
          {result.related_docs.length > 0 && (
            <>
              <h3>相关文档</h3>
              {result.related_docs.map((d: any, i) => <div key={i}>{d.title} ({d.relation_type})</div>)}
            </>
          )}
        </div>
      )}
    </div>
  )
}
