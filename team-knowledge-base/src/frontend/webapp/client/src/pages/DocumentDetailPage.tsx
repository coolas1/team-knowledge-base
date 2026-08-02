import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import MDEditor from '@uiw/react-md-editor'
import { api, type Document } from '../api/client'
import { StatusBadge } from '../components/StatusBadge'

export function DocumentDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [doc, setDoc] = useState<Document | null>(null)
  const [editing, setEditing] = useState(false)
  const [editContent, setEditContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [polling, setPolling] = useState(false)

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
      {doc.error_msg && (
        <div style={{ padding: 12, background: '#fff2f0', border: '1px solid #ffccc7', borderRadius: 6, marginBottom: 16, color: '#cf1322' }}>
          错误: {doc.error_msg}
        </div>
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
