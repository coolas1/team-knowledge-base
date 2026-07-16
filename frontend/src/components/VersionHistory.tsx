import { useState, useEffect } from 'react'
import { api } from '../api/client'

interface VersionInfo {
  version: number
  change_type: string
  change_summary: string | null
  content_hash: string
  created_at: string
}

interface VersionDetail {
  version: number
  raw_text: string
  content_hash: string
  file_path: string | null
  change_type: string
  change_summary: string | null
  created_at: string
}

interface DiffResult {
  from_version: number
  to_version: number
  diff: string
  stats: { added: number; removed: number }
}

const CHANGE_TYPE_LABELS: Record<string, string> = {
  create: '🆕 创建',
  modify: '✏️ 修改',
  rename: '📝 重命名',
  rollback: '↩️ 回滚',
}

export function VersionHistory({ docId, onRefreshDoc }: { docId: string; onRefreshDoc?: () => void }) {
  const [versions, setVersions] = useState<VersionInfo[]>([])
  const [currentVersion, setCurrentVersion] = useState(0)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const [tab, setTab] = useState<'content' | 'diff'>('content')
  const [versionDetail, setVersionDetail] = useState<VersionDetail | null>(null)
  const [diffResult, setDiffResult] = useState<DiffResult | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    loadVersions()
  }, [docId])

  useEffect(() => {
    if (selectedVersion !== null) {
      loadVersionDetail(selectedVersion)
    }
  }, [selectedVersion])

  useEffect(() => {
    if (tab === 'diff' && selectedVersion !== null && selectedVersion > 1) {
      loadDiff(selectedVersion - 1, selectedVersion)
    }
  }, [tab, selectedVersion])

  const loadVersions = async () => {
    try {
      const data = await api.getVersions(docId)
      setVersions(data.versions)
      setCurrentVersion(data.current_version)
    } catch (err) {
      console.error('加载版本列表失败:', err)
    }
  }

  const loadVersionDetail = async (version: number) => {
    setLoading(true)
    try {
      const data = await api.getVersion(docId, version)
      setVersionDetail(data)
    } catch (err) {
      console.error('加载版本详情失败:', err)
    } finally {
      setLoading(false)
    }
  }

  const loadDiff = async (from: number, to: number) => {
    try {
      const data = await api.getVersionDiff(docId, from, to)
      setDiffResult(data)
    } catch (err) {
      console.error('加载 diff 失败:', err)
    }
  }

  const handleRollback = async (version: number) => {
    if (!confirm(`确定要回滚到 v${version} 吗？这将创建一个新的版本。`)) return
    try {
      await api.rollbackVersion(docId, version)
      await loadVersions()
      onRefreshDoc?.()
    } catch (err: any) {
      alert('回滚失败: ' + err.message)
    }
  }

  const formatTime = (iso: string) => {
    const d = new Date(iso)
    return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
  }

  return (
    <div style={{ display: 'flex', height: '100%', minHeight: 400 }}>
      {/* 左侧版本列表 */}
      <div style={{
        width: 200, borderRight: '1px solid #e8e8e8', overflow: 'auto',
        padding: '8px 0', background: '#fafafa', flexShrink: 0,
      }}>
        <div style={{ padding: '4px 12px', fontSize: 12, fontWeight: 600, color: '#666', marginBottom: 4 }}>
          版本历史 ({versions.length})
        </div>
        {versions.map((v) => (
          <div
            key={v.version}
            onClick={() => { setSelectedVersion(v.version); setTab('content') }}
            style={{
              padding: '8px 12px', cursor: 'pointer',
              background: selectedVersion === v.version ? '#e6f7ff' : 'transparent',
              borderLeft: selectedVersion === v.version ? '3px solid #1890ff' : '3px solid transparent',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ fontWeight: 600, fontSize: 13 }}>v{v.version}</span>
              {v.version === currentVersion && (
                <span style={{
                  fontSize: 10, padding: '1px 4px', borderRadius: 3,
                  background: '#52c41a', color: '#fff',
                }}>当前</span>
              )}
            </div>
            <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
              {CHANGE_TYPE_LABELS[v.change_type] || v.change_type}
            </div>
            {v.change_summary && (
              <div style={{ fontSize: 11, color: '#666', marginTop: 1 }}>{v.change_summary}</div>
            )}
            <div style={{ fontSize: 10, color: '#bbb', marginTop: 2 }}>{formatTime(v.created_at)}</div>
          </div>
        ))}
      </div>

      {/* 右侧内容区域 */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {selectedVersion !== null ? (
          <>
            {/* Tab 切换 */}
            <div style={{ display: 'flex', borderBottom: '1px solid #e8e8e8', padding: '0 12px' }}>
              <button
                onClick={() => setTab('content')}
                style={{
                  padding: '8px 16px', border: 'none', background: 'none', cursor: 'pointer',
                  borderBottom: tab === 'content' ? '2px solid #1890ff' : '2px solid transparent',
                  color: tab === 'content' ? '#1890ff' : '#666', fontSize: 13,
                }}
              >内容</button>
              <button
                onClick={() => setTab('diff')}
                disabled={selectedVersion <= 1}
                style={{
                  padding: '8px 16px', border: 'none', background: 'none',
                  cursor: selectedVersion > 1 ? 'pointer' : 'not-allowed',
                  borderBottom: tab === 'diff' ? '2px solid #1890ff' : '2px solid transparent',
                  color: tab === 'diff' ? '#1890ff' : selectedVersion > 1 ? '#666' : '#ccc',
                  fontSize: 13,
                }}
              >Diff</button>
              <div style={{ flex: 1 }} />
              {selectedVersion !== currentVersion && (
                <button
                  onClick={() => handleRollback(selectedVersion)}
                  style={{
                    padding: '4px 12px', margin: '4px 0', borderRadius: 4,
                    border: '1px solid #fa8c16', color: '#fa8c16', background: '#fff',
                    cursor: 'pointer', fontSize: 12,
                  }}
                >回滚到 v{selectedVersion}</button>
              )}
            </div>

            {/* 内容 */}
            <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
              {loading ? (
                <div style={{ color: '#999', textAlign: 'center', padding: 40 }}>加载中...</div>
              ) : tab === 'content' ? (
                versionDetail && (
                  <pre style={{
                    whiteSpace: 'pre-wrap', fontFamily: 'Consolas, monospace', fontSize: 13,
                    padding: 12, background: '#f6f8fa', borderRadius: 6, margin: 0,
                  }}>
                    {versionDetail.raw_text || '(无内容)'}
                  </pre>
                )
              ) : (
                diffResult ? (
                  <DiffView diff={diffResult.diff} stats={diffResult.stats} />
                ) : (
                  <div style={{ color: '#999', textAlign: 'center', padding: 40 }}>加载 diff 中...</div>
                )
              )}
            </div>
          </>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, color: '#999' }}>
            点击左侧版本查看详情
          </div>
        )}
      </div>
    </div>
  )
}

function DiffView({ diff, stats }: { diff: string; stats: { added: number; removed: number } }) {
  if (!diff) {
    return <div style={{ color: '#999', textAlign: 'center', padding: 40 }}>无差异</div>
  }

  const lines = diff.split('\n')

  return (
    <div>
      <div style={{ fontSize: 12, color: '#666', marginBottom: 8 }}>
        <span style={{ color: '#52c41a' }}>+{stats.added} 行</span>
        {' '}
        <span style={{ color: '#ff4d4f' }}>-{stats.removed} 行</span>
      </div>
      <pre style={{
        fontFamily: 'Consolas, monospace', fontSize: 12, padding: 12,
        background: '#f6f8fa', borderRadius: 6, margin: 0, overflow: 'auto',
      }}>
        {lines.map((line, i) => {
          let bg = 'transparent'
          let color = '#333'
          if (line.startsWith('+') && !line.startsWith('+++')) {
            bg = '#e6ffed'; color = '#22863a'
          } else if (line.startsWith('-') && !line.startsWith('---')) {
            bg = '#ffeef0'; color = '#cb2431'
          } else if (line.startsWith('@@')) {
            bg = '#f1f8ff'; color = '#0366d6'
          } else if (line.startsWith('---') || line.startsWith('+++')) {
            color = '#666'
          }
          return (
            <div key={i} style={{ background: bg, color, padding: '1px 4px', margin: '0 -12px', paddingLeft: 12, paddingRight: 12 }}>
              {line || ' '}
            </div>
          )
        })}
      </pre>
    </div>
  )
}
