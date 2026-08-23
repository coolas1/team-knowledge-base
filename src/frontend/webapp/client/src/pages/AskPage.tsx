import { useEffect, useRef, useState } from 'react'
import MDEditor from '@uiw/react-md-editor'
import {
  AlertCircle,
  BookOpen,
  Bot,
  LoaderCircle,
  Menu,
  Plus,
  Send,
  Square,
  Trash2,
  UserRound,
  X,
} from 'lucide-react'
import { api, type AgentSession, type AgentSessionDetail, type PiAgentEvent } from '../api/client'
import './AskPage.css'

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
    return `对话 ${new Intl.DateTimeFormat('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(timestamp))}`
  }
  return `对话 ${session.id.slice(0, 8)}`
}

function sessionMeta(session: AgentSession) {
  if (session.streaming) return '正在回答'
  if (session.messageCount === 0) return '空对话'
  return `${session.messageCount} 条记录`
}

export function AskPage() {
  const [query, setQuery] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [citations, setCitations] = useState<Citation[]>([])
  const [status, setStatus] = useState('正在恢复对话')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [sessionLoading, setSessionLoading] = useState(true)
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | undefined>()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const sessionRef = useRef<string | undefined>(undefined)
  const controllerRef = useRef<AbortController | undefined>(undefined)
  const nextMessageId = useRef(0)
  const messagesEndRef = useRef<HTMLDivElement>(null)

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
          setStatus('新对话')
          return
        }

        const detail = await api.getAgentSession(sorted[0].id)
        if (cancelled) return
        sessionRef.current = detail.id
        setActiveSessionId(detail.id)
        restoreMessages(detail)
        setStatus('历史对话已恢复')
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

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: 'end' })
  }, [messages, loading])

  const updateAssistant = (id: number, update: (text: string) => string) => {
    setMessages((current) =>
      current.map((message) =>
        message.id === id ? { ...message, text: update(message.text) } : message,
      ),
    )
  }

  const handleEvent = (event: PiAgentEvent, assistantId: number) => {
    if (event.type === 'message.start' && event.name) {
      setSessions((current) =>
        current.map((session) =>
          session.id === event.sessionId ? { ...session, name: event.name } : session,
        ),
      )
    } else if (event.type === 'assistant.delta') {
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
    setStatus('新对话')
    setSidebarOpen(false)
  }

  const selectConversation = async (sessionId: string) => {
    if (loading || sessionLoading || sessionId === sessionRef.current) {
      setSidebarOpen(false)
      return
    }
    setSessionLoading(true)
    setError('')
    setCitations([])
    setStatus('正在加载对话')
    try {
      const detail = await api.getAgentSession(sessionId)
      sessionRef.current = detail.id
      setActiveSessionId(detail.id)
      restoreMessages(detail)
      setStatus('历史对话已恢复')
      setSidebarOpen(false)
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

  const busy = loading || sessionLoading
  const statusTone = error ? 'error' : busy ? 'busy' : 'ready'

  return (
    <section className={`ask-shell${sidebarOpen ? ' ask-shell--sidebar-open' : ''}`}>
      <button
        type="button"
        className="ask-sidebar-backdrop"
        aria-label="关闭对话列表背景"
        onClick={() => setSidebarOpen(false)}
      />

      <aside className="ask-sidebar" aria-label="对话列表">
        <div className="ask-sidebar-head">
          <div className="ask-sidebar-title">对话</div>
          <button
            type="button"
            className="ask-icon-button ask-sidebar-close"
            aria-label="关闭对话列表"
            title="关闭对话列表"
            onClick={() => setSidebarOpen(false)}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        <button
          type="button"
          className="ask-new-button"
          onClick={newConversation}
          disabled={busy}
        >
          <Plus size={17} aria-hidden="true" />
          <span>新建对话</span>
        </button>

        <div className="ask-session-heading">最近对话</div>
        <div className="ask-session-list">
          {sessionLoading && sessions.length === 0 && (
            <div className="ask-session-placeholder">
              <LoaderCircle className="ask-spin" size={16} aria-hidden="true" />
              <span>正在加载</span>
            </div>
          )}
          {!sessionLoading && sessions.length === 0 && (
            <div className="ask-session-placeholder">暂无对话</div>
          )}
          {sessions.map((session) => (
            <div
              key={session.id}
              className={`ask-session-row${activeSessionId === session.id ? ' is-active' : ''}`}
            >
              <button
                type="button"
                className="ask-session-select"
                onClick={() => void selectConversation(session.id)}
                disabled={busy}
                title={sessionLabel(session)}
              >
                <span className="ask-session-name">{sessionLabel(session)}</span>
                <span className="ask-session-meta">{sessionMeta(session)}</span>
              </button>
              <button
                type="button"
                className="ask-session-delete"
                onClick={() => void deleteConversation(session.id)}
                disabled={busy}
                title="删除对话"
                aria-label={`删除 ${sessionLabel(session)}`}
              >
                <Trash2 size={15} aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      </aside>

      <main className="ask-main">
        <header className="ask-header">
          <button
            type="button"
            className="ask-icon-button ask-menu-button"
            aria-label="打开对话列表"
            title="打开对话列表"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu size={19} aria-hidden="true" />
          </button>
          <div className="ask-agent-mark" aria-hidden="true">
            <Bot size={20} />
          </div>
          <div className="ask-header-copy">
            <h1>知识库助手</h1>
            <div className={`ask-status is-${statusTone}`} role="status">
              <span className="ask-status-dot" />
              <span>{status}</span>
            </div>
          </div>
        </header>

        <div className="ask-thread" aria-live="polite">
          <div className="ask-thread-inner">
            {messages.length === 0 && !sessionLoading && (
              <div className="ask-empty-state">
                <div className="ask-empty-icon" aria-hidden="true">
                  <BookOpen size={26} />
                </div>
                <h2>从团队知识中找到答案</h2>
                <div className="ask-suggestions">
                  {['总结最近上传的文档', '比较两份方案的差异', '查找已有的项目决策'].map(
                    (suggestion) => (
                      <button
                        type="button"
                        key={suggestion}
                        onClick={() => setQuery(suggestion)}
                      >
                        {suggestion}
                      </button>
                    ),
                  )}
                </div>
              </div>
            )}

            {sessionLoading && messages.length === 0 && (
              <div className="ask-thread-loading">
                <LoaderCircle className="ask-spin" size={20} aria-hidden="true" />
                <span>正在恢复对话</span>
              </div>
            )}

            {messages.map((message) => (
              <article key={message.id} className={`ask-message is-${message.role}`}>
                <div className="ask-message-avatar" aria-hidden="true">
                  {message.role === 'assistant' ? <Bot size={18} /> : <UserRound size={17} />}
                </div>
                <div className="ask-message-body">
                  <div className="ask-message-author">
                    {message.role === 'assistant' ? '知识库助手' : '你'}
                  </div>
                  {message.role === 'assistant' ? (
                    message.text ? (
                      <div className="ask-markdown" data-color-mode="light">
                        <MDEditor.Markdown source={message.text} />
                      </div>
                    ) : loading ? (
                      <div className="ask-answer-loading">
                        <LoaderCircle className="ask-spin" size={17} aria-hidden="true" />
                        <span>正在组织答案</span>
                      </div>
                    ) : null
                  ) : (
                    <div className="ask-user-text">{message.text}</div>
                  )}
                </div>
              </article>
            ))}

            {citations.length > 0 && (
              <section className="ask-citations" aria-labelledby="ask-citations-title">
                <div className="ask-citations-title" id="ask-citations-title">
                  <BookOpen size={16} aria-hidden="true" />
                  <span>引用来源</span>
                </div>
                <div className="ask-citation-list">
                  {citations.map((citation) => (
                    <a
                      key={citation.docId}
                      href={`/documents/${encodeURIComponent(citation.docId)}`}
                      className="ask-citation-link"
                    >
                      <span>{citation.title}</span>
                      <small>{citation.docId}</small>
                    </a>
                  ))}
                </div>
              </section>
            )}

            {error && (
              <div className="ask-error" role="alert">
                <AlertCircle size={17} aria-hidden="true" />
                <span>{error}</span>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>

        <footer className="ask-composer-zone">
          <form
            className="ask-composer"
            onSubmit={(event) => {
              event.preventDefault()
              void run()
            }}
          >
            <textarea
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  void run()
                }
              }}
              disabled={busy}
              placeholder="向团队知识库提问"
              rows={1}
              aria-label="问题"
            />
            {loading ? (
              <button
                type="button"
                className="ask-submit-button is-stop"
                onClick={() => void stop()}
                title="停止生成"
                aria-label="停止生成"
              >
                <Square size={16} fill="currentColor" aria-hidden="true" />
              </button>
            ) : (
              <button
                type="submit"
                className="ask-submit-button"
                disabled={!query.trim() || sessionLoading}
                title="发送"
                aria-label="发送"
              >
                <Send size={17} aria-hidden="true" />
              </button>
            )}
          </form>
        </footer>
      </main>
    </section>
  )
}
