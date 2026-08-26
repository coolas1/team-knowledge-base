import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import MDEditor from '@uiw/react-md-editor'
import { AlertCircle, LoaderCircle, RefreshCw } from 'lucide-react'
import { ApiError, api, type Document } from '../api/client'
import { StatusBadge } from '../components/StatusBadge'

export function DocumentDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [doc, setDoc] = useState<Document | null>(null)
  const [editing, setEditing] = useState(false)
  const [editContent, setEditContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [polling, setPolling] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState('')

  const loadDoc = async () => {
    if (!id) return
    try {
      const d = await api.getDocument(id)
      setDoc(d)
      // 如果正在处理中，持续轮询
      if (d.status === 'pending' || d.status === 'processing') {
        setPolling(true)
      } else {
        setPolling(false)
      }
    } catch (err: any) {
      alert('加载失败: ' + err.message)
    }
  }

  useEffect(() => { loadDoc() }, [id])

  // 轮询状态更新
  useEffect(() => {
    if (!polling) return
    const timer = setInterval(loadDoc, 2000)
    return () => clearInterval(timer)
  }, [polling, id])

  const handleSave = async () => {
    if (!id) return
    setSaving(true)
    try {
      await api.editContent(id, editContent)
      setEditing(false)
      loadDoc()
    } catch (err: any) {
      alert('保存失败: ' + err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!id || !confirm('确定删除此文件？')) return
    try {
      await api.deleteDocument(id)
      navigate('/')
    } catch (err: any) {
      alert('删除失败: ' + err.message)
    }
  }

  const handleRetry = async () => {
    if (!id || retrying) return
    setRetrying(true)
    setRetryError('')
    try {
      await api.retryDocument(id)
      await loadDoc()
    } catch (error) {
      const suggestion = error instanceof ApiError ? error.suggestion : undefined
      const message = error instanceof Error ? error.message : '重新处理失败'
      setRetryError(`${message}${suggestion ? ` · ${suggestion}` : ''}`)
    } finally {
      setRetrying(false)
    }
  }

  if (!doc) return <div style={{ padding: 24 }}>加载中...</div>

  const isMarkdown = doc.file_type === 'markdown'

  return (
    <main style={{ flex: 1, overflow: 'auto', padding: 24 }}>
      {/* 头部 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <button onClick={() => navigate('/')} style={{ cursor: 'pointer', border: '1px solid #d9d9d9', borderRadius: 4, padding: '4px 12px', background: '#fff' }}>←</button>
        <h2 style={{ margin: 0, flex: 1 }}>{doc.title}</h2>
        <StatusBadge status={doc.status} />
        <span style={{ fontSize: 12, color: '#999' }}>{doc.file_type} · {doc.chunk_count} chunks</span>
        {isMarkdown && !editing && (
          <button onClick={() => { setEditing(true); setEditContent(doc.raw_text || '') }}
            style={{ padding: '4px 12px', borderRadius: 4, border: '1px solid #1890ff', color: '#1890ff', background: '#fff', cursor: 'pointer' }}>
            编辑
          </button>
        )}
        <button onClick={handleDelete}
          style={{ padding: '4px 12px', borderRadius: 4, border: '1px solid #ff4d4f', color: '#ff4d4f', background: '#fff', cursor: 'pointer' }}>
          删除
        </button>
      </div>

      {/* 错误信息 */}
      {doc.status === 'failed' && (
        <section
          role="alert"
          style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-start', gap: 10, padding: 12, background: '#fff7f7', border: '1px solid #fecaca', borderRadius: 6, marginBottom: 16, color: '#7f1d1d' }}
        >
          <AlertCircle size={19} aria-hidden="true" style={{ flex: '0 0 auto', marginTop: 1, color: '#dc2626' }} />
          <div style={{ minWidth: 180, flex: '1 1 240px' }}>
            <div style={{ fontWeight: 650, fontSize: 14, marginBottom: 3 }}>文件处理失败</div>
            <div style={{ fontSize: 13, overflowWrap: 'anywhere' }}>
              {doc.error_msg || '处理任务未完成，服务未返回具体原因。'}
            </div>
            <div style={{ marginTop: 5, color: '#9f3a3a', fontSize: 12 }}>
              请确认文件可正常打开，并检查数据库、模型及 OCR 服务；修复后可直接重新处理。
            </div>
            {retryError && <div style={{ marginTop: 6, fontSize: 12 }}>{retryError}</div>}
          </div>
          <button
            type="button"
            onClick={() => void handleRetry()}
            disabled={retrying}
            style={{ display: 'inline-flex', minHeight: 32, flex: '0 0 auto', alignItems: 'center', gap: 6, padding: '5px 10px', borderRadius: 5, border: '1px solid #f1a8a8', color: '#8f1d1d', background: '#fff', cursor: retrying ? 'wait' : 'pointer', fontSize: 12, fontWeight: 650 }}
          >
            {retrying ? <LoaderCircle className="app-spin" size={16} aria-hidden="true" /> : <RefreshCw size={16} aria-hidden="true" />}
            <span>{retrying ? '处理中' : '重新处理'}</span>
          </button>
        </section>
      )}

      {/* Overview */}
      {doc.overview && (
        <div style={{ padding: 16, background: '#f6f8fa', borderRadius: 8, marginBottom: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 14 }}>摘要</div>
          <div style={{ fontSize: 14, color: '#555' }}>{doc.overview}</div>
        </div>
      )}

      {/* 编辑模式 */}
      {editing && isMarkdown ? (
        <div>
          <div style={{ marginBottom: 8, display: 'flex', gap: 8 }}>
            <button onClick={handleSave} disabled={saving}
              style={{ padding: '6px 16px', borderRadius: 4, border: 'none', background: '#1890ff', color: '#fff', cursor: saving ? 'wait' : 'pointer' }}>
              {saving ? '保存中...' : '保存'}
            </button>
            <button onClick={() => setEditing(false)}
              style={{ padding: '6px 16px', borderRadius: 4, border: '1px solid #d9d9d9', background: '#fff', cursor: 'pointer' }}>
              取消
            </button>
          </div>
          <MDEditor value={editContent} onChange={(v) => setEditContent(v || '')} height={500} />
        </div>
      ) : (
        /* 查看模式 */
        <div>
          {isMarkdown && doc.raw_text ? (
            <MDEditor.Markdown source={doc.raw_text} />
          ) : doc.raw_text ? (
            <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', padding: 16, background: '#f9f9f9', borderRadius: 8 }}>
              {doc.raw_text}
            </pre>
          ) : (
            <div style={{ color: '#999', textAlign: 'center', padding: 40 }}>
              {doc.status === 'processing' ? '文件处理中...' : '暂无内容'}
            </div>
          )}
        </div>
      )}
    </main>
  )
}
