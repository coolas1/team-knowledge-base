import { useEffect, useRef, useState } from 'react'
import { api, type AgentSession, type AgentSessionDetail, type PiAgentEvent } from '../api/client'

interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  text: string
}

interface Citation {
  docId: string
  title: string
}

function sortSessions(items: AgentSession[]) {
  return [...items].sort((left, right) => {
    const leftTime = Date.parse(left.modified || left.created || '') || 0
    const rightTime = Date.parse(right.modified || right.created || '') || 0
    return rightTime - leftTime
  })
}

function sessionLabel(session: AgentSession) {
  if (session.name?.trim()) return session.name
  const timestamp = session.modified || session.created
  if (timestamp && Number.isFinite(Date.parse(timestamp))) {
    return `会话 ${new Intl.DateTimeFormat('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(timestamp))}`
  }
  return `会话 ${session.id.slice(0, 8)}`
}

export function AskPage() {
  const [query, setQuery] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [citations, setCitations] = useState<Citation[]>([])
  const [status, setStatus] = useState('就绪')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [sessionLoading, setSessionLoading] = useState(true)
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | undefined>()
  const sessionRef = useRef<string | undefined>(undefined)
  const controllerRef = useRef<AbortController | undefined>(undefined)
  const nextMessageId = useRef(0)

  const restoreMessages = (detail: AgentSessionDetail) => {
    setMessages(
      (detail.messages || []).map((message) => ({
        id: ++nextMessageId.current,
        role: message.role,
        text: message.text,
      })),
    )
  }

  useEffect(() => {
    let cancelled = false

    const load = async () => {
      try {
        const result = await api.listAgentSessions()
        const sorted = sortSessions(result.items)
        if (cancelled) return
        setSessions(sorted)
        if (sorted.length === 0) {
          setStatus('新会话')
          return
        }

        const detail = await api.getAgentSession(sorted[0].id)
        if (cancelled) return
        sessionRef.current = detail.id
        setActiveSessionId(detail.id)
        restoreMessages(detail)
        setStatus('已恢复历史会话')
      } catch (caught) {
        if (cancelled) return
        const message = caught instanceof Error ? caught.message : String(caught)
        setError(message)
        setStatus('会话加载失败')
      } finally {
        if (!cancelled) setSessionLoading(false)
      }
    }

    void load()
    return () => {
      cancelled = true
      controllerRef.current?.abort()
    }
  }, [])

  const updateAssistant = (id: number, update: (text: string) => string) => {
    setMessages((current) =>
      current.map((message) =>
        message.id === id ? { ...message, text: update(message.text) } : message,
      ),
    )
  }

  const handleEvent = (event: PiAgentEvent, assistantId: number) => {
    if (event.type === 'assistant.delta') {
      updateAssistant(assistantId, (text) => text + event.delta)
    } else if (event.type === 'tool.start') {
      setStatus(`正在调用 ${event.toolName}`)
    } else if (event.type === 'tool.result') {
      setStatus(event.isError ? `${event.toolName} 调用失败` : `${event.toolName} 已完成`)
    } else if (event.type === 'citation') {
      setCitations((current) =>
        current.some((item) => item.docId === event.docId)
          ? current
          : [...current, { docId: event.docId, title: event.title }],
      )
    } else if (event.type === 'limit.reached') {
      setStatus(`已达到 ${event.limit === 'time' ? '时间' : '工具调用'}限制`)
    } else if (event.type === 'message.completed') {
      updateAssistant(assistantId, () => event.answer)
      setSessions((current) => {
        const activeId = sessionRef.current
        const active = current.find((session) => session.id === activeId)
        if (!active) return current
        return [
          { ...active, modified: new Date().toISOString() },
          ...current.filter((session) => session.id !== activeId),
        ]
      })
      setStatus(`回答完成 · 调用工具 ${event.toolCalls} 次`)
    } else if (event.type === 'message.failed') {
      setError(event.error)
    }
  }

  const run = async () => {
    const prompt = query.trim()
    if (!prompt || loading || sessionLoading) return

    const userId = ++nextMessageId.current
    const assistantId = ++nextMessageId.current
    setMessages((current) => [
      ...current,
      { id: userId, role: 'user', text: prompt },
      { id: assistantId, role: 'assistant', text: '' },
    ])
    setQuery('')
    setCitations([])
    setError('')
    setStatus('正在连接 Agent')
    setLoading(true)

    const controller = new AbortController()
    controllerRef.current = controller
    try {
      if (!sessionRef.current) {
        const session = await api.createAgentSession()
        sessionRef.current = session.id
        setActiveSessionId(session.id)
        setSessions((current) => sortSessions([session, ...current]))
      }
      setStatus('正在思考')
      await api.streamAgentMessage(
        sessionRef.current,
        prompt,
        (event) => handleEvent(event, assistantId),
        controller.signal,
      )
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') {
        setStatus('已停止')
      } else {
        const message = caught instanceof Error ? caught.message : String(caught)
        setError(message)
        setStatus('请求失败')
      }
    } finally {
      controllerRef.current = undefined
      setLoading(false)
    }
  }

  const stop = async () => {
    controllerRef.current?.abort()
    if (sessionRef.current) {
      await api.cancelAgentSession(sessionRef.current).catch(() => undefined)
    }
    setStatus('已停止')
  }

  const newConversation = () => {
    if (loading || sessionLoading) return
    sessionRef.current = undefined
    setActiveSessionId(undefined)
    setMessages([])
    setCitations([])
    setError('')
    setStatus('新会话')
  }

  const selectConversation = async (sessionId: string) => {
    if (loading || sessionLoading || sessionId === sessionRef.current) return
    setSessionLoading(true)
    setError('')
    setCitations([])
    setStatus('正在加载会话')
    try {
      const detail = await api.getAgentSession(sessionId)
      sessionRef.current = detail.id
      setActiveSessionId(detail.id)
      restoreMessages(detail)
      setStatus('已恢复历史会话')
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught)
      setError(message)
      setStatus('会话加载失败')
    } finally {
      setSessionLoading(false)
    }
  }

  const deleteConversation = async (sessionId: string) => {
    if (loading || sessionLoading) return
    if (!window.confirm('确定删除这个会话吗？此操作无法撤销。')) return
    setSessionLoading(true)
    setError('')
    try {
      await api.deleteAgentSession(sessionId)
      const remaining = sessions.filter((session) => session.id !== sessionId)
      setSessions(remaining)
      if (sessionRef.current === sessionId) {
        sessionRef.current = undefined
        setActiveSessionId(undefined)
        setMessages([])
        setCitations([])
        setStatus('会话已删除')
      }
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught)
      setError(message)
      setStatus('删除失败')
    } finally {
      setSessionLoading(false)
    }
  }

  return (
    <div style={{ flex: 1, display: 'flex', overflow: 'hidden', background: '#f7f8fa' }}>
      <aside style={{ width: 240, overflow: 'auto', padding: 16, borderRight: '1px solid #e8e8e8', background: '#fff' }}>
        <button
          onClick={newConversation}
          disabled={loading || sessionLoading}
          style={{ width: '100%', padding: '8px 12px', border: '1px solid #d9d9d9', borderRadius: 6, background: '#fff', marginBottom: 12 }}
        >
          新会话
        </button>
        <div style={{ color: '#777', fontSize: 12, marginBottom: 8 }}>历史会话</div>
        {sessionLoading && sessions.length === 0 && (
          <div style={{ color: '#999', fontSize: 13 }}>加载中...</div>
        )}
        {!sessionLoading && sessions.length === 0 && (
          <div style={{ color: '#999', fontSize: 13 }}>暂无历史会话</div>
        )}
        {sessions.map((session) => (
          <div key={session.id} style={{ display: 'flex', gap: 4, marginBottom: 6 }}>
            <button
              onClick={() => void selectConversation(session.id)}
              disabled={loading || sessionLoading}
              title={sessionLabel(session)}
              style={{ flex: 1, minWidth: 0, padding: '8px 10px', border: activeSessionId === session.id ? '1px solid #1677ff' : '1px solid transparent', borderRadius: 6, background: activeSessionId === session.id ? '#eaf3ff' : '#fff', textAlign: 'left', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            >
              {sessionLabel(session)}
            </button>
            <button
              onClick={() => void deleteConversation(session.id)}
              disabled={loading || sessionLoading}
              title="删除会话"
              aria-label={`删除 ${sessionLabel(session)}`}
              style={{ padding: '4px 6px', border: 0, background: 'transparent', color: '#999' }}
            >
              删除
            </button>
          </div>
        ))}
      </aside>

      <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 20, fontWeight: 700 }}>知识库 Agent</div>
            <div style={{ color: '#777', fontSize: 13, marginTop: 4 }}>{status}</div>
          </div>
          <div style={{ flex: 1 }} />
        </div>

        <div style={{ minHeight: 360, display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 16 }}>
          {messages.length === 0 && (
            <div style={{ padding: 32, textAlign: 'center', color: '#888', background: '#fff', borderRadius: 8 }}>
              可以查询文件、比较多篇文档，或让 Agent 选择 Fast / Deep 检索。
            </div>
          )}
          {messages.map((message) => (
            <div
              key={message.id}
              style={{
                alignSelf: message.role === 'user' ? 'flex-end' : 'stretch',
                maxWidth: message.role === 'user' ? '75%' : '100%',
                padding: '12px 16px',
                borderRadius: 8,
                background: message.role === 'user' ? '#1677ff' : '#fff',
                color: message.role === 'user' ? '#fff' : '#222',
                whiteSpace: 'pre-wrap',
                border: message.role === 'assistant' ? '1px solid #eee' : 'none',
              }}
            >
              {message.text || (loading && message.role === 'assistant' ? '…' : '')}
            </div>
          ))}
        </div>

        {citations.length > 0 && (
          <div style={{ padding: 12, background: '#fff', border: '1px solid #eee', borderRadius: 8, marginBottom: 12 }}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>引用来源</div>
            {citations.map((citation) => (
              <div key={citation.docId} style={{ fontSize: 13, marginTop: 4 }}>
                <a href={`/documents/${encodeURIComponent(citation.docId)}`}>{citation.title}</a>
                <span style={{ color: '#999', marginLeft: 8 }}>{citation.docId}</span>
              </div>
            ))}
          </div>
        )}

        {error && (
          <div style={{ color: '#b42318', background: '#fef3f2', padding: 10, borderRadius: 6, marginBottom: 12 }}>
            {error}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8 }}>
          <textarea
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void run()
              }
            }}
            disabled={loading || sessionLoading}
            placeholder="向知识库提问，Enter 发送，Shift+Enter 换行"
            rows={3}
            style={{ flex: 1, padding: '10px 12px', borderRadius: 6, border: '1px solid #d9d9d9', resize: 'vertical' }}
          />
          {loading ? (
            <button onClick={() => void stop()} style={{ padding: '6px 18px', borderRadius: 6, border: '1px solid #d92d20', color: '#d92d20', background: '#fff' }}>
              停止
            </button>
          ) : (
            <button onClick={() => void run()} disabled={!query.trim() || sessionLoading} style={{ padding: '6px 18px', borderRadius: 6, border: 0, color: '#fff', background: '#1677ff' }}>
              发送
            </button>
          )}
        </div>
      </div>
      </div>
    </div>
  )
}
