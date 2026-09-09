import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import MDEditor from '@uiw/react-md-editor'
import { AlertCircle, LoaderCircle, RefreshCw } from 'lucide-react'
import { ApiError, api, type Document, type DocumentVersion, type PipelineProgress } from '../api/client'
import { StatusBadge } from '../components/StatusBadge'

const STAGE_LABELS: Record<string, string> = {
  extracting: '提取文本',
  chunking: '文本分块',
  overview: '生成摘要',
  analyzing_chunks: '分析实体与关系',
  embedding: '生成嵌入向量',
  writing_postgres: '写入数据库',
  writing_neo4j: '写入知识图谱',
  done: '完成',
  failed: '失败',
}

function PipelineBar({ pipeline, now }: { pipeline: PipelineProgress; now: number }) {
  const pct = pipeline.total > 0 ? Math.round((pipeline.current / pipeline.total) * 100) : 0
  const elapsed = Math.max(0, Math.floor(now / 1000 - pipeline.started_at))
  const label = STAGE_LABELS[pipeline.stage] || pipeline.stage
  const indeterminate = pipeline.total === 0

  return (
    <div style={{ padding: 16, background: '#f6f8fa', borderRadius: 8, marginBottom: 16 }}>
      <style>{`
        @keyframes pipeline-stripes {
          from { background-position: 0 0; }
          to { background-position: 28px 0; }
        }
      `}</style>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>{label}</span>
        <span style={{ fontSize: 12, color: '#999' }}>已用 {elapsed}s</span>
      </div>
      <div style={{ background: '#e8e8e8', borderRadius: 4, height: 8, overflow: 'hidden' }}>
        <div style={
          indeterminate
            ? {
                width: '100%',
                background: 'repeating-linear-gradient(45deg, #1890ff 0, #1890ff 6px, #40a9ff 6px, #40a9ff 12px)',
                backgroundSize: '28px 28px',
                animation: 'pipeline-stripes 0.8s linear infinite',
                height: '100%',
              }
            : {
                width: `${pct}%`,
                background: '#1890ff',
                height: '100%',
                transition: 'width 0.3s ease',
              }
        } />
      </div>
      <div style={{ fontSize: 12, color: '#666', marginTop: 4 }}>
        {pipeline.detail || (indeterminate ? '' : `${pipeline.current}/${pipeline.total}`)}
      </div>
    </div>
  )
}

export function DocumentDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [doc, setDoc] = useState<Document | null>(null)
  const [versions, setVersions] = useState<DocumentVersion[]>([])
  const [editing, setEditing] = useState(false)
  const [editContent, setEditContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [polling, setPolling] = useState(false)
  const [now, setNow] = useState(Date.now())
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState('')

  const loadDoc = async () => {
    if (!id) return
    try {
      const d = await api.getDocument(id)
      setDoc(d)
      // 版本链（有多个版本时才有意义，失败静默）
      try {
        const v = await api.listVersions(id)
        setVersions(v.versions || [])
      } catch {
        setVersions([])
      }
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

  // 每秒刷新已用时间（独立于 2s 轮询）
  useEffect(() => {
    if (!doc?.pipeline) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [doc?.pipeline])

  const handleSave = async () => {
    if (!id) return
    setSaving(true)
    try {
      // 版本化编辑：保存生成新版本，跳转到新版本详情页
      const newDoc = await api.editContent(id, editContent)
      setEditing(false)
      if (newDoc.id && newDoc.id !== id) {
        navigate(`/documents/${newDoc.id}`)
      } else {
        loadDoc()
      }
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
  const showProgress = doc.pipeline && (doc.status === 'pending' || doc.status === 'processing')

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

      {/* Pipeline 进度条 */}
      {showProgress && doc.pipeline && (
        <PipelineBar pipeline={doc.pipeline} now={now} />
      )}

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

      {/* 版本链（纵向迭代） */}
      {versions.length > 1 && (
        <div style={{ padding: 16, background: '#f6f8fa', borderRadius: 8, marginBottom: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 14 }}>
            版本历史（共 {versions.length} 个版本）
          </div>
          {versions.map((v) => (
            <div
              key={v.id}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 8,
                padding: '6px 0',
                borderBottom: '1px solid #eee',
                cursor: v.id === doc.id ? 'default' : 'pointer',
                opacity: v.id === doc.id ? 1 : 0.75,
              }}
              onClick={() => { if (v.id !== doc.id) navigate(`/documents/${v.id}`) }}
            >
              <span
                style={{
                  fontSize: 12,
                  fontWeight: 600,
                  color: v.is_current ? '#1890ff' : '#999',
                  minWidth: 28,
                }}
              >
                v{v.version_number}
              </span>
              {v.is_current && (
                <span style={{ fontSize: 11, color: '#1890ff', background: '#e6f7ff', padding: '0 6px', borderRadius: 8 }}>当前</span>
              )}
              <span style={{ flex: 1, fontSize: 12, color: '#555' }}>
                {v.change_summary || v.overview || '（无变更摘要）'}
              </span>
              <span style={{ fontSize: 11, color: '#999' }}>
                {v.created_at ? new Date(v.created_at).toLocaleString() : ''}
              </span>
            </div>
          ))}
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
