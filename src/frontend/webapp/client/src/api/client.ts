const BASE = '/api'

export interface Document {
  id: string
  title: string
  file_type: string
  raw_text?: string
  overview?: string
  file_path?: string
  content_hash?: string
  status: string
  error_msg?: string | null
  memory_status?: string | null
  memory_error_msg?: string | null
  memory_count?: number
  memory_link_count?: number
  chunk_count?: number
  created_at?: string
  updated_at?: string
}

export interface DocumentList {
  total: number
  page: number
  page_size: number
  items: Document[]
}

export interface GraphNode {
  name: string
  type: string
  description: string
  sources: Array<{ doc_id: string; doc_title: string; chunk_index: number }>
}

export interface GraphLink {
  source: string
  target: string
  type: string
  description: string
}

export interface GraphData {
  nodes: GraphNode[]
  links: GraphLink[]
}

export interface AgentSession {
  id: string
  name?: string
  created?: string
  modified?: string
  messageCount: number
  streaming: boolean
}

export interface AgentConversationMessage {
  role: 'user' | 'assistant'
  text: string
}

export interface AgentSessionDetail extends AgentSession {
  messages: AgentConversationMessage[]
}

export interface AgentSessionList {
  items: AgentSession[]
}

export type PiAgentEvent =
  | { type: 'message.start'; sessionId: string; name?: string }
  | { type: 'assistant.delta'; delta: string }
  | { type: 'assistant.thinking'; delta: string }
  | { type: 'tool.start'; toolCallId: string; toolName: string; args: unknown }
  | { type: 'tool.result'; toolCallId: string; toolName: string; isError: boolean; result?: unknown }
  | { type: 'citation'; docId: string; title: string }
  | { type: 'limit.reached'; limit: 'tool_calls' | 'time'; maximum: number }
  | { type: 'message.completed'; sessionId: string; answer: string; toolCalls: number }
  | { type: 'message.failed'; error: string; code?: string }

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly suggestion?: string,
    readonly retryable = false,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function responseError(res: Response): Promise<Error> {
  const body = await res.json().catch(() => undefined)
  const detail = body?.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    return new ApiError(
      typeof detail.message === 'string' ? detail.message : res.statusText,
      res.status,
      typeof detail.code === 'string' ? detail.code : undefined,
      typeof detail.suggestion === 'string' ? detail.suggestion : undefined,
      detail.retryable === true,
    )
  }
  const message =
    (typeof detail === 'string' && detail) ||
    (typeof body?.error === 'string' && body.error) ||
    (typeof body?.message === 'string' && body.message) ||
    res.statusText ||
    `请求失败 (${res.status})`
  const suggestion =
    res.status === 413
      ? '请选择更小的文件，或压缩文件后重新上传。'
      : res.status >= 500
        ? '服务恢复后可直接重试；如果持续失败，请联系管理员检查服务日志。'
        : undefined
  return new ApiError(message, res.status, undefined, suggestion, res.status >= 500)
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, options)
  if (!res.ok) {
    throw await responseError(res)
  }
  return res.json()
}

export async function readSseEvents(
  response: Response,
  onEvent: (event: PiAgentEvent) => void,
): Promise<void> {
  if (!response.body) throw new Error('浏览器不支持流式响应')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const consume = () => {
    buffer = buffer.replace(/\r\n/g, '\n')
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const data = block
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (data) onEvent(JSON.parse(data) as PiAgentEvent)
      boundary = buffer.indexOf('\n\n')
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      consume()
    }
    buffer += decoder.decode()
    if (buffer.trim()) buffer += '\n\n'
    consume()
  } finally {
    reader.releaseLock()
  }
}

export const api = {
  // 文件
  listDocuments(params?: {
    page?: number
    page_size?: number
    file_type?: string
    status?: string
  }) {
    const qs = new URLSearchParams()
    if (params?.page) qs.set('page', String(params.page))
    if (params?.page_size) qs.set('page_size', String(params.page_size))
    if (params?.file_type) qs.set('file_type', params.file_type)
    if (params?.status) qs.set('status', params.status)
    return request<DocumentList>(`/documents?${qs}`)
  },

  getDocument(id: string) {
    return request<Document>(`/documents/${id}`)
  },

  async uploadFile(file: File) {
    const form = new FormData()
    form.append('file', file)
    try {
      return await request<Document>('/documents/upload', { method: 'POST', body: form })
    } catch (error) {
      if (error instanceof ApiError) throw error
      throw new ApiError(
        '无法连接上传服务',
        0,
        'network_error',
        '请检查网络或服务状态，恢复后可直接重试。',
        true,
      )
    }
  },

  editContent(id: string, content: string) {
    return request<Document>(`/documents/${id}/content`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    })
  },

  retryDocument(id: string) {
    return request<Document>(`/documents/${id}/retry`, { method: 'POST' })
  },

  deleteDocument(id: string) {
    return request<{ deleted: boolean }>(`/documents/${id}`, { method: 'DELETE' })
  },

  // 图谱
  getEntity(name: string) {
    return request<any>(`/graph/entity/${encodeURIComponent(name)}`)
  },

  getNeighbors(name: string, hops = 2) {
    return request<any>(`/graph/neighbors/${encodeURIComponent(name)}?hops=${hops}`)
  },

  getFullGraph() {
    return request<GraphData>('/graph/full')
  },

  // 搜索
  search(query: string, topK = 20) {
    return request<{ chunks: any[]; related_entities: any[]; related_docs: any[] }>(
      '/search',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query, top_k: topK }) },
    )
  },

  // Agent
  ask(query: string) {
    return request<{ query: string; answer: string; sources: any }>(
      '/agent/ask',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query }) },
    )
  },

  createAgentSession() {
    return request<AgentSession>('/agent/sessions', { method: 'POST' })
  },

  listAgentSessions() {
    return request<AgentSessionList>('/agent/sessions')
  },

  getAgentSession(sessionId: string) {
    return request<AgentSessionDetail>(
      `/agent/sessions/${encodeURIComponent(sessionId)}`,
    )
  },

  cancelAgentSession(sessionId: string) {
    return request<{ cancelled: boolean; sessionId: string }>(
      `/agent/sessions/${encodeURIComponent(sessionId)}/cancel`,
      { method: 'POST' },
    )
  },

  deleteAgentSession(sessionId: string) {
    return request<{ deleted: boolean; sessionId: string }>(
      `/agent/sessions/${encodeURIComponent(sessionId)}`,
      { method: 'DELETE' },
    )
  },

  async streamAgentMessage(
    sessionId: string,
    message: string,
    onEvent: (event: PiAgentEvent) => void,
    signal?: AbortSignal,
  ) {
    const res = await fetch(
      `${BASE}/agent/sessions/${encodeURIComponent(sessionId)}/messages`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ message }),
        signal,
      },
    )
    if (!res.ok) throw await responseError(res)
    let failure: string | undefined
    await readSseEvents(res, (event) => {
      onEvent(event)
      if (event.type === 'message.failed') failure = event.error
    })
    if (failure) throw new Error(failure)
  },
}
