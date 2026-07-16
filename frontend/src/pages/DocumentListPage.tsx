import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Document } from '../api/client'
import { StatusBadge } from '../components/StatusBadge'

const FILE_TYPES = ['', 'markdown', 'pdf', 'docx', 'pptx', 'image']
const INDEX_STATUSES = ['', 'pending', 'processing', 'indexed', 'failed', 'stale']
const FILE_STATUSES = ['', 'active', 'disappeared']

interface SyncStatus {
  watch_enabled: boolean
  watch_directories: string[]
  last_pipeline_at: string | null
  pending_count: number
  schedule_hours: number
  next_scheduled_at: string | null
}

export function DocumentListPage() {
  const [docs, setDocs] = useState<Document[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [fileType, setFileType] = useState('')
  const [indexStatus, setIndexStatus] = useState('')
  const [fileStatus, setFileStatus] = useState('')
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null)
  const [syncing, setSyncing] = useState(false)

  const loadDocs = async () => {
    try {
      const data = await api.listDocuments({
        page, page_size: 20,
        file_type: fileType || undefined,
        index_status: indexStatus || undefined,
        file_status: fileStatus || undefined,
      })
      setDocs(data.items)
      setTotal(data.total)
    } catch (err: any) {
      console.error(err)
    }
  }

  useEffect(() => { loadDocs() }, [page, fileType, indexStatus, fileStatus])

  const loadSyncStatus = async () => {
    try {
      const s = await api.getSyncStatus()
      setSyncStatus(s)
    } catch { /* ignore */ }
  }

  useEffect(() => { loadSyncStatus() }, [])

  const handleTriggerSync = async () => {
    setSyncing(true)
    try {
      const res = await api.triggerSync()
      alert(res.message)
      loadSyncStatus()
      setTimeout(loadDocs, 3000) // 延迟刷新文档列表
    } catch (err: any) {
      alert('同步失败: ' + err.message)
    } finally {
      setSyncing(false)
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
        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          <select value={fileType} onChange={(e) => { setFileType(e.target.value); setPage(1) }}
            style={{ flex: 1, padding: '4px 8px', borderRadius: 4, border: '1px solid #d9d9d9', fontSize: 12 }}>
            {FILE_TYPES.map(t => <option key={t} value={t}>{t || '全部类型'}</option>)}
          </select>
          <select value={indexStatus} onChange={(e) => { setIndexStatus(e.target.value); setPage(1) }}
            style={{ flex: 1, padding: '4px 8px', borderRadius: 4, border: '1px solid #d9d9d9', fontSize: 12 }}>
            {INDEX_STATUSES.map(s => <option key={s} value={s}>{s || '全部索引状态'}</option>)}
          </select>
          <select value={fileStatus} onChange={(e) => { setFileStatus(e.target.value); setPage(1) }}
            style={{ flex: 1, padding: '4px 8px', borderRadius: 4, border: '1px solid #d9d9d9', fontSize: 12 }}>
            {FILE_STATUSES.map(s => <option key={s} value={s}>{s || '全部文件状态'}</option>)}
          </select>
        </div>

        {/* 同步状态栏 */}
        {syncStatus && (
          <div style={{
            padding: '8px 10px', marginBottom: 12, borderRadius: 6,
            background: syncStatus.watch_enabled ? '#f6ffed' : '#fff7e6',
            border: `1px solid ${syncStatus.watch_enabled ? '#b7eb8f' : '#ffd591'}`,
            fontSize: 11, color: '#555',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <span style={{ fontWeight: 600 }}>
                {syncStatus.watch_enabled ? '✅ 监控中' : '⏸️ 监控未启用'}
              </span>
              <button
                onClick={handleTriggerSync}
                disabled={syncing}
                style={{
                  padding: '2px 8px', borderRadius: 4, fontSize: 11,
                  border: '1px solid #1890ff', color: '#1890ff', background: '#fff',
                  cursor: syncing ? 'wait' : 'pointer',
                }}
              >{syncing ? '同步中...' : '立即同步'}</button>
            </div>
            {syncStatus.pending_count > 0 && (
              <div>待处理: {syncStatus.pending_count} 个文档</div>
            )}
            {syncStatus.next_scheduled_at && (
              <div>下次自动同步: {new Date(syncStatus.next_scheduled_at).toLocaleString()}</div>
            )}
          </div>
        )}

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
              <StatusBadge status={doc.index_status} />
              {doc.file_status === 'disappeared' && (
                <StatusBadge status="disappeared" type="file" />
              )}
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
