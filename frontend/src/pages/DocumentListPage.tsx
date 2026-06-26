import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Document } from '../api/client'
import { StatusBadge } from '../components/StatusBadge'

const FILE_TYPES = ['', 'markdown', 'pdf', 'docx', 'pptx', 'image']
const STATUSES = ['', 'pending', 'processing', 'indexed', 'failed']

export function DocumentListPage() {
  const [docs, setDocs] = useState<Document[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [fileType, setFileType] = useState('')
  const [status, setStatus] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchResult, setSearchResult] = useState<any>(null)

  const loadDocs = async () => {
    try {
      const data = await api.listDocuments({ page, page_size: 20, file_type: fileType || undefined, status: status || undefined })
      setDocs(data.items)
      setTotal(data.total)
    } catch (err: any) {
      console.error(err)
    }
  }

  useEffect(() => { loadDocs() }, [page, fileType, status])

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    setSearching(true)
    try {
      const result = await api.search(searchQuery)
      setSearchResult(result)
    } catch (err: any) {
      alert('搜索失败: ' + err.message)
    } finally {
      setSearching(false)
    }
  }

  return (
    <>
      {/* 左侧文件树 */}
      <aside
        style={{
          width: 280,
          borderRight: '1px solid #e8e8e8',
          overflow: 'auto',
          padding: 16,
          background: '#fafafa',
        }}
      >
        <div style={{ marginBottom: 12 }}>
          <input
            placeholder="搜索..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            style={{
              width: '100%',
              padding: '8px 12px',
              border: '1px solid #d9d9d9',
              borderRadius: 6,
              fontSize: 14,
              boxSizing: 'border-box',
            }}
          />
        </div>

        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          <select value={fileType} onChange={(e) => { setFileType(e.target.value); setPage(1) }}
            style={{ flex: 1, padding: '4px 8px', borderRadius: 4, border: '1px solid #d9d9d9', fontSize: 12 }}>
            {FILE_TYPES.map(t => <option key={t} value={t}>{t || '全部类型'}</option>)}
          </select>
          <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }}
            style={{ flex: 1, padding: '4px 8px', borderRadius: 4, border: '1px solid #d9d9d9', fontSize: 12 }}>
            {STATUSES.map(s => <option key={s} value={s}>{s || '全部状态'}</option>)}
          </select>
        </div>

        <div style={{ fontSize: 12, color: '#999', marginBottom: 8 }}>
          共 {total} 个文件
        </div>

        {docs.map((doc) => (
          <Link
            key={doc.id}
            to={`/documents/${doc.id}`}
            style={{
              display: 'block',
              padding: '8px 12px',
              marginBottom: 4,
              borderRadius: 6,
              textDecoration: 'none',
              color: '#333',
              background: '#fff',
              border: '1px solid #eee',
            }}
          >
            <div style={{ fontWeight: 500, fontSize: 14, marginBottom: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {doc.title}
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 12, color: '#999' }}>{doc.file_type}</span>
              <StatusBadge status={doc.status} />
            </div>
          </Link>
        ))}

        {total > 20 && (
          <div style={{ textAlign: 'center', marginTop: 12 }}>
            <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} style={{ marginRight: 8 }}>上一页</button>
            <span style={{ fontSize: 12 }}>第 {page} 页</span>
            <button disabled={page * 20 >= total} onClick={() => setPage(p => p + 1)} style={{ marginLeft: 8 }}>下一页</button>
          </div>
        )}
      </aside>

      {/* 右侧内容区 */}
      <main style={{ flex: 1, overflow: 'auto', padding: 24 }}>
        {searchResult ? (
          <SearchResultView result={searchResult} onClear={() => setSearchResult(null)} />
        ) : (
          <div style={{ color: '#999', textAlign: 'center', marginTop: 100 }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>📚</div>
            <div style={{ fontSize: 16 }}>选择左侧文件查看详情，或搜索知识库</div>
          </div>
        )}
      </main>
    </>
  )
}

function SearchResultView({ result, onClear }: { result: any; onClear: () => void }) {
  return (
    <div>
      <button onClick={onClear} style={{ marginBottom: 16, cursor: 'pointer', border: '1px solid #d9d9d9', borderRadius: 4, padding: '4px 12px', background: '#fff' }}>
        ← 返回文件列表
      </button>
      <h3>搜索结果</h3>
      <div style={{ padding: 16, background: '#f6f8fa', borderRadius: 8, marginBottom: 16 }}>
        {result.answer}
      </div>

      {result.sources?.length > 0 && (
        <>
          <h4>引用来源 ({result.sources.length})</h4>
          {result.sources.map((s: any, i: number) => (
            <Link key={i} to={`/documents/${s.doc_id}`} style={{ textDecoration: 'none' }}>
              <div style={{ padding: 12, border: '1px solid #e8e8e8', borderRadius: 8, marginBottom: 8, background: '#fff' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                  <strong>{s.title}</strong>
                  <span style={{ color: '#1890ff', fontSize: 12 }}>相似度 {(s.score * 100).toFixed(1)}%</span>
                </div>
                <div style={{ fontSize: 13, color: '#666', maxHeight: 80, overflow: 'hidden' }}>
                  {s.chunk_text}
                </div>
              </div>
            </Link>
          ))}
        </>
      )}

      {result.related_entities?.length > 0 && (
        <>
          <h4>相关实体 ({result.related_entities.length})</h4>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {result.related_entities.map((e: any, i: number) => (
              <Link key={i} to={`/graph/${encodeURIComponent(e.name)}`} style={{ textDecoration: 'none' }}>
                <div style={{ padding: '8px 12px', border: '1px solid #d9d9d9', borderRadius: 6, background: '#fff' }}>
                  <div style={{ fontWeight: 500 }}>{e.name}</div>
                  <div style={{ fontSize: 12, color: '#999' }}>{e.type}</div>
                </div>
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
