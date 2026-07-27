import { useState } from 'react'
import { api } from '../api/client'

export function AskPage() {
  const [query, setQuery] = useState('')
  const [answer, setAnswer] = useState<{ query: string; answer: string; sources: any } | null>(null)
  const [loading, setLoading] = useState(false)

  const run = async () => {
    if (!query.trim()) return
    setLoading(true)
    try {
      setAnswer(await api.ask(query))
    } catch (err: any) {
      alert('提问失败: ' + err.message)
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
          placeholder="向知识库提问..."
          style={{ flex: 1, padding: '8px 12px', borderRadius: 6, border: '1px solid #d9d9d9', fontSize: 14 }}
        />
        <button onClick={run} disabled={loading} style={{ padding: '6px 16px', borderRadius: 6, border: '1px solid #1890ff', background: '#1890ff', color: '#fff' }}>
          {loading ? '思考中...' : '提问'}
        </button>
      </div>

      {answer && (
        <div style={{ padding: 16, border: '1px solid #eee', borderRadius: 6, background: '#fafafa' }}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>回答</div>
          <div style={{ whiteSpace: 'pre-wrap' }}>{answer.answer}</div>
        </div>
      )}
    </div>
  )
}
