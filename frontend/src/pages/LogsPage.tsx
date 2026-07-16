import { useState, useEffect, useRef, useCallback } from 'react'
import { api, LogEntry } from '../api/client'

// ── 级别着色映射 ────────────────────────────────────────────
const LEVEL_COLORS: Record<string, string> = {
  DEBUG: '#888',
  INFO: '#52c41a',
  WARNING: '#faad14',
  ERROR: '#ff4d4f',
  CRITICAL: '#cf1322',
}

const LEVEL_BG: Record<string, string> = {
  DEBUG: '#f5f5f5',
  INFO: '#f6ffed',
  WARNING: '#fffbe6',
  ERROR: '#fff2f0',
  CRITICAL: '#fff1f0',
}

// ── 辅助：格式化时间 ────────────────────────────────────────
function formatTime(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  })
}

// ── 单条日志组件 ─────────────────────────────────────────────
function LogRow({ entry }: { entry: LogEntry }) {
  const [expanded, setExpanded] = useState(false)
  const color = LEVEL_COLORS[entry.level] || '#333'
  const bg = LEVEL_BG[entry.level] || '#fff'

  return (
    <div
      onClick={() => setExpanded(!expanded)}
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
        padding: '8px 14px',
        borderBottom: '1px solid #f0f0f0',
        background: bg,
        cursor: entry.extra ? 'pointer' : 'default',
        fontSize: 13,
        fontFamily: 'Menlo, Consolas, monospace',
        transition: 'background 0.15s',
      }}
    >
      {/* 时间 */}
      <span style={{ color: '#999', flexShrink: 0, minWidth: 120 }}>
        {formatTime(entry.timestamp)}
      </span>
      {/* 级别徽章 */}
      <span
        style={{
          color: '#fff',
          background: color,
          borderRadius: 3,
          padding: '1px 6px',
          fontSize: 11,
          fontWeight: 600,
          flexShrink: 0,
          minWidth: 56,
          textAlign: 'center',
        }}
      >
        {entry.level}
      </span>
      {/* 模块 */}
      <span style={{ color: '#999', flexShrink: 0, minWidth: 160, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {entry.module}
      </span>
      {/* 消息 */}
      <span style={{ color: '#333', flex: 1, wordBreak: 'break-all', whiteSpace: expanded ? 'pre-wrap' : 'nowrap', overflow: expanded ? 'visible' : 'hidden', textOverflow: expanded ? 'unset' : 'ellipsis' }}>
        {entry.message}
      </span>
      {/* doc_id */}
      {entry.doc_id && (
        <span style={{ color: '#1890ff', flexShrink: 0, fontSize: 11 }}>
          doc:{entry.doc_id.slice(0, 8)}…
        </span>
      )}
      {/* trace_id */}
      {entry.trace_id && (
        <span style={{ color: '#722ed1', flexShrink: 0, fontSize: 11 }}>
          trace:{entry.trace_id}
        </span>
      )}
    </div>
  )
}

// ── 主页面 ───────────────────────────────────────────────────
export function LogsPage() {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)

  // 过滤条件
  const [levelFilter, setLevelFilter] = useState('')
  const [docIdFilter, setDocIdFilter] = useState('')
  const [traceIdFilter, setTraceIdFilter] = useState('')
  const [startTime, setStartTime] = useState('')
  const [endTime, setEndTime] = useState('')

  // 实时模式
  const [isLive, setIsLive] = useState(true)
  const [autoScroll, setAutoScroll] = useState(true)
  const listRef = useRef<HTMLDivElement>(null)
  const eventSourceRef = useRef<EventSource | null>(null)
  const idCounter = useRef(1000000)

  // 加载历史日志
  const fetchLogs = useCallback(async (p: number) => {
    setLoading(true)
    try {
      const result = await api.listLogs({
        page: p,
        page_size: 100,
        level: levelFilter || undefined,
        doc_id: docIdFilter || undefined,
        trace_id: traceIdFilter || undefined,
        start_time: startTime || undefined,
        end_time: endTime || undefined,
      })
      setLogs(result.items)
      setTotal(result.total)
    } catch (e) {
      console.error('加载日志失败', e)
    } finally {
      setLoading(false)
    }
  }, [levelFilter, docIdFilter, traceIdFilter, startTime, endTime])

  // SSE 实时连接
  useEffect(() => {
    if (!isLive) {
      eventSourceRef.current?.close()
      return
    }

    const es = new EventSource(api.getLogStreamUrl())
    eventSourceRef.current = es

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        const entry: LogEntry = {
          ...data,
          id: idCounter.current++,
          extra: null,
        }
        // trace_id 过滤（客户端端过滤）
        if (traceIdFilter && entry.trace_id !== traceIdFilter) return
        setLogs(prev => {
          const next = [entry, ...prev]
          return next.length > 1000 ? next.slice(0, 1000) : next
        })
        setTotal(prev => prev + 1)
      } catch {
        // 忽略心跳等非 data 消息
      }
    }

    es.onerror = () => {
      // 连接断开后 3 秒重连
      setTimeout(() => {
        if (eventSourceRef.current === es) {
          es.close()
          if (isLive) {
            const newEs = new EventSource(api.getLogStreamUrl())
            eventSourceRef.current = newEs
          }
        }
      }, 3000)
    }

    return () => { es.close() }
  }, [isLive])

  // 历史模式加载
  useEffect(() => {
    if (!isLive) fetchLogs(page)
  }, [isLive, page, fetchLogs])

  // 自动滚动
  useEffect(() => {
    if (autoScroll && isLive && listRef.current) {
      listRef.current.scrollTop = 0
    }
  }, [logs, autoScroll, isLive])

  // 初始加载
  useEffect(() => {
    if (isLive) fetchLogs(1)
  }, []) // eslint-disable-line

  // 清理日志
  const handleClear = async () => {
    if (!confirm('确定清理 7 天前的日志？')) return
    try {
      const res = await api.clearLogs(7)
      alert(`已清理 ${res.deleted_count} 条日志`)
      fetchLogs(1)
    } catch (e: any) {
      alert('清理失败: ' + e.message)
    }
  }

  const totalPages = Math.ceil(total / 100)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%' }}>
      {/* 工具栏 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '10px 20px',
          borderBottom: '1px solid #e8e8e8',
          background: '#fafafa',
          flexWrap: 'wrap',
        }}
      >
        {/* 模式切换 */}
        <button
          onClick={() => setIsLive(!isLive)}
          style={{
            padding: '5px 14px',
            borderRadius: 4,
            border: '1px solid',
            borderColor: isLive ? '#52c41a' : '#d9d9d9',
            background: isLive ? '#f6ffed' : '#fff',
            color: isLive ? '#52c41a' : '#666',
            cursor: 'pointer',
            fontSize: 13,
            fontWeight: 600,
          }}
        >
          {isLive ? '● 实时' : '○ 历史'}
        </button>

        {/* 级别过滤 */}
        <select
          value={levelFilter}
          onChange={e => { setLevelFilter(e.target.value); if (!isLive) setPage(1) }}
          style={{ padding: '5px 8px', borderRadius: 4, border: '1px solid #d9d9d9', fontSize: 13 }}
        >
          <option value="">全部级别</option>
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="ERROR">ERROR</option>
          <option value="CRITICAL">CRITICAL</option>
        </select>

        {/* doc_id 过滤 */}
        <input
          placeholder="文档 ID 过滤..."
          value={docIdFilter}
          onChange={e => { setDocIdFilter(e.target.value); if (!isLive) setPage(1) }}
          style={{ padding: '5px 8px', borderRadius: 4, border: '1px solid #d9d9d9', fontSize: 13, width: 180 }}
        />

        {/* trace_id 过滤 */}
        <input
          placeholder="Trace ID 过滤..."
          value={traceIdFilter}
          onChange={e => { setTraceIdFilter(e.target.value); if (!isLive) setPage(1) }}
          style={{ padding: '5px 8px', borderRadius: 4, border: '1px solid #d9d9d9', fontSize: 13, width: 140 }}
        />

        {/* 时间范围 */}
        <input
          type="datetime-local"
          value={startTime}
          onChange={e => { setStartTime(e.target.value); if (!isLive) setPage(1) }}
          style={{ padding: '5px 8px', borderRadius: 4, border: '1px solid #d9d9d9', fontSize: 13 }}
        />
        <span style={{ color: '#999' }}>—</span>
        <input
          type="datetime-local"
          value={endTime}
          onChange={e => { setEndTime(e.target.value); if (!isLive) setPage(1) }}
          style={{ padding: '5px 8px', borderRadius: 4, border: '1px solid #d9d9d9', fontSize: 13 }}
        />

        <div style={{ flex: 1 }} />

        {/* 自动滚动 */}
        {isLive && (
          <label style={{ fontSize: 12, color: '#666', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={e => setAutoScroll(e.target.checked)}
              style={{ marginRight: 4 }}
            />
            自动滚动
          </label>
        )}

        {/* 清理按钮 */}
        <button
          onClick={handleClear}
          style={{
            padding: '5px 12px',
            borderRadius: 4,
            border: '1px solid #ff4d4f',
            background: '#fff',
            color: '#ff4d4f',
            cursor: 'pointer',
            fontSize: 13,
          }}
        >
          清理旧日志
        </button>

        <span style={{ fontSize: 12, color: '#999' }}>
          共 {total} 条
        </span>
      </div>

      {/* 日志列表 */}
      <div
        ref={listRef}
        style={{ flex: 1, overflow: 'auto' }}
      >
        {loading && !isLive ? (
          <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>加载中...</div>
        ) : logs.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无日志</div>
        ) : (
          logs.map(entry => <LogRow key={entry.id} entry={entry} />)
        )}
      </div>

      {/* 分页（仅历史模式） */}
      {!isLive && totalPages > 1 && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            gap: 8,
            padding: '10px 0',
            borderTop: '1px solid #e8e8e8',
          }}
        >
          <button
            disabled={page <= 1}
            onClick={() => setPage(p => p - 1)}
            style={{ padding: '4px 12px', borderRadius: 4, border: '1px solid #d9d9d9', cursor: 'pointer' }}
          >
            上一页
          </button>
          <span style={{ fontSize: 13, color: '#666' }}>
            {page} / {totalPages}
          </span>
          <button
            disabled={page >= totalPages}
            onClick={() => setPage(p => p + 1)}
            style={{ padding: '4px 12px', borderRadius: 4, border: '1px solid #d9d9d9', cursor: 'pointer' }}
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}
