import { useRef, useState } from 'react'
import { api, type PiAgentEvent } from '../api/client'

interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  text: string
}

interface Citation {
  docId: string
  title: string
}

export function AskPage() {
  const [query, setQuery] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [citations, setCitations] = useState<Citation[]>([])
  const [status, setStatus] = useState('就绪')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const sessionRef = useRef<string | undefined>(undefined)
  const controllerRef = useRef<AbortController | undefined>(undefined)
  const nextMessageId = useRef(0)

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
      setStatus(`回答完成 · 调用工具 ${event.toolCalls} 次`)
    } else if (event.type === 'message.failed') {
      setError(event.error)
    }
  }

  const run = async () => {
    const prompt = query.trim()
    if (!prompt || loading) return

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
    controllerRef.current?.abort()
    if (sessionRef.current) void api.deleteAgentSession(sessionRef.current).catch(() => undefined)
    sessionRef.current = undefined
    setMessages([])
    setCitations([])
    setError('')
    setStatus('新会话')
  }

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 24, background: '#f7f8fa' }}>
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 20, fontWeight: 700 }}>知识库 Agent</div>
            <div style={{ color: '#777', fontSize: 13, marginTop: 4 }}>{status}</div>
          </div>
          <div style={{ flex: 1 }} />
          <button
            onClick={newConversation}
            disabled={loading}
            style={{ padding: '6px 12px', border: '1px solid #d9d9d9', borderRadius: 6, background: '#fff' }}
          >
            新会话
          </button>
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
            disabled={loading}
            placeholder="向知识库提问，Enter 发送，Shift+Enter 换行"
            rows={3}
            style={{ flex: 1, padding: '10px 12px', borderRadius: 6, border: '1px solid #d9d9d9', resize: 'vertical' }}
          />
          {loading ? (
            <button onClick={() => void stop()} style={{ padding: '6px 18px', borderRadius: 6, border: '1px solid #d92d20', color: '#d92d20', background: '#fff' }}>
              停止
            </button>
          ) : (
            <button onClick={() => void run()} disabled={!query.trim()} style={{ padding: '6px 18px', borderRadius: 6, border: 0, color: '#fff', background: '#1677ff' }}>
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
