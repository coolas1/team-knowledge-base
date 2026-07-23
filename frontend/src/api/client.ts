import { clearSessionToken, getAuthorizationHeader, setSessionToken } from '../auth/teamAuth'

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
  chunk_count?: number
  created_at?: string
  updated_at?: string
  team_id?: string
  tags?: string[]
  scope?: string
}

export interface DocumentList {
  total: number
  page: number
  page_size: number
  items: Document[]
}

export interface GraphNode {
  id: string
  name: string
  namespace: string
  type: string
  description: string
  sources: Array<{ doc_id: string; doc_title: string; chunk_index: number }>
}

export interface GraphLink {
  source: string
  target: string
  type: string
  description: string
  namespace?: string
}

export interface GraphData {
  nodes: GraphNode[]
  links: GraphLink[]
}

export interface CurrentIdentity {
  team_id: string
  team_name: string
  subject: string
  roles: string[]
  auth_source: string
  knowledge_base: { id: string; name: string; document_count: number }
}

export interface AdminTeam {
  id: string
  name: string
  active: boolean
  document_count: number
  public_document_count: number
  managed_token_count: number
  trusted_ollama_account_count: number
  current_subject: string
  current_roles: string[]
  auth_source: string
}

export interface TrustedOllamaAccount {
  id: string
  team_id: string
  username: string
  display_name: string
  roles: string[]
  active: boolean
  last_used_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface AdminToken {
  id: string
  team_id: string
  name: string
  subject: string
  token_prefix: string
  roles: string[]
  active: boolean
  expires_at: string | null
  last_used_at: string | null
  created_at: string | null
  token?: string
  warning?: string
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers)
  const authorization = getAuthorizationHeader()
  if (authorization) headers.set('Authorization', authorization)

  const res = await fetch(`${BASE}${url}`, { ...options, headers })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    const message = res.status === 401
      ? '需要认证，请先选择已配置的团队'
      : err.detail || err.message || res.statusText
    if (res.status === 401) clearSessionToken()
    throw new ApiError(message, res.status)
  }
  return res.json()
}

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message)
  }
}

export async function authenticate(token: string): Promise<CurrentIdentity> {
  const value = token.trim()
  const res = await fetch(`${BASE}/auth/me`, {
    headers: { Authorization: `Bearer ${value}` },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: '认证失败' }))
    throw new ApiError(body.detail || 'Token 无效', res.status)
  }
  const identity = await res.json() as CurrentIdentity
  setSessionToken(value)
  return identity
}

export const api = {
  getCurrentIdentity() {
    return request<CurrentIdentity>('/auth/me')
  },

  listKnowledgeBases() {
    return request<{ current_team_id: string; items: Array<{ id: string; name: string; document_count: number; roles: string[] }> }>('/knowledge-bases')
  },

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

  uploadFile(file: File, scope: 'team' | 'public' = 'team') {
    const form = new FormData()
    form.append('file', file)
    return request<Document>(`/documents/upload?scope=${scope}`, { method: 'POST', body: form })
  },

  editContent(id: string, content: string) {
    return request<Document>(`/documents/${id}/content`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    })
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

  getAdminTeam() {
    return request<AdminTeam>('/admin/team')
  },

  listAdminTokens() {
    return request<{ team_id: string; items: AdminToken[] }>('/admin/tokens')
  },

  createAdminToken(body: { name: string; subject: string; roles: string[] }) {
    return request<AdminToken>('/admin/tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  },

  updateAdminToken(id: string, body: { active?: boolean; roles?: string[]; name?: string }) {
    return request<AdminToken>(`/admin/tokens/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  },

  revokeAdminToken(id: string) {
    return request<{ revoked: boolean; id: string; team_id: string }>(`/admin/tokens/${id}`, { method: 'DELETE' })
  },

  listTrustedOllamaAccounts() {
    return request<{ team_id: string; items: TrustedOllamaAccount[] }>('/admin/ollama-accounts')
  },

  createTrustedOllamaAccount(body: { username: string; display_name?: string; roles: string[] }) {
    return request<TrustedOllamaAccount>('/admin/ollama-accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  },

  updateTrustedOllamaAccount(id: string, body: { active?: boolean; roles?: string[]; display_name?: string }) {
    return request<TrustedOllamaAccount>(`/admin/ollama-accounts/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  },

  revokeTrustedOllamaAccount(id: string) {
    return request<{ revoked: boolean; id: string; team_id: string }>(`/admin/ollama-accounts/${id}`, { method: 'DELETE' })
  },
}
