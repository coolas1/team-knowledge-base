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
}

export interface DocumentList {
  total: number
  page: number
  page_size: number
  items: Document[]
}

export interface SearchSource {
  doc_id: string
  title: string
  chunk_text: string
  score: number
}

export interface SearchResult {
  answer: string
  sources: SearchSource[]
  related_entities: { name: string; type: string; relations?: any[] }[]
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, options)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.message || res.statusText)
  }
  return res.json()
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

  uploadFile(file: File) {
    const form = new FormData()
    form.append('file', file)
    return request<Document>('/documents/upload', { method: 'POST', body: form })
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

  // 搜索
  search(query: string) {
    return request<SearchResult>('/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    })
  },

  // 图谱
  getEntity(name: string) {
    return request<any>(`/graph/entity/${encodeURIComponent(name)}`)
  },

  getNeighbors(name: string, hops = 2) {
    return request<any>(`/graph/neighbors/${encodeURIComponent(name)}?hops=${hops}`)
  },
}
