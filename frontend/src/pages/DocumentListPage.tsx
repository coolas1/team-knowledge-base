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
  const [error, setError] = useState('')

  const loadDocs = async () => {
    try {
      setError('')
      const data = await api.listDocuments({ page, page_size: 20, file_type: fileType || undefined, status: status || undefined })
      setDocs(data.items)
      setTotal(data.total)
    } catch (err: any) {
      setDocs([])
      setTotal(0)
      setError(err.message || '加载文档失败')
    }
  }

  useEffect(() => { loadDocs() }, [page, fileType, status])

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

        {error && (
          <div style={{ padding: 10, marginBottom: 10, borderRadius: 6, background: '#fff2f0', color: '#cf1322', fontSize: 12 }}>
            {error}
          </div>
        )}

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
              <span style={{ fontSize: 12, color: doc.scope === 'public' ? '#1677ff' : '#999' }}>
                {doc.file_type} · {doc.scope === 'public' ? `公共 · 所属 ${doc.team_id}` : '团队'}
              </span>
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
        <div style={{ color: '#999', textAlign: 'center', marginTop: 100 }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>📚</div>
          <div style={{ fontSize: 16 }}>选择左侧文件查看详情</div>
        </div>
      </main>
    </>
  )
}
