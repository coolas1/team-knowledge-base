const BASE = '/api'

export interface Document {
  id: string
  title: string
  file_type: string
  raw_text?: string
  overview?: string
  file_path?: string
  content_hash?: string
  source_type?: string
  source_path?: string
  index_status: string
  file_status: string
  error_msg?: string | null
  chunk_count?: number
  version_count?: number
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

export interface LogEntry {
  id: number
  timestamp: string
  level: string
  module: string
  message: string
  doc_id: string | null
  trace_id: string | null
  extra: Record<string, any> | null
}

export interface LogList {
  total: number
  page: number
  page_size: number
  items: LogEntry[]
}

export interface SearchChunk {
  doc_id: string
  title: string
  chunk_text: string
  reranker_score: number
  vector_score: number
  index_status: string
}

export interface SearchDebug {
  rewrite: {
    original: string
    rewritten: string
    keywords: string[]
    expanded_queries: string[]
    elapsed_ms: number
  }
  recall: {
    vector_main: number
    vector_expanded: number
    bm25: number
    rrf_merged: number
    elapsed_ms: number
  }
  iterative_expand: { added: number; elapsed_ms: number }
  reranker: {
    input: number
    survivors: number
    threshold: number
    top_n: number
    elapsed_ms: number
  }
  graph: {
    entities: number
    graph_chunks: number
    related_doc_chunks: number
    elapsed_ms: number
  }
  total_ms: number
  final_chunks: number
}

export interface SearchResult {
  chunks: SearchChunk[]
  related_entities: Array<{ name: string; type: string; relations: any[] }>
  related_docs: Array<{ doc_id: string; title: string; relation_type: string; reason: string }>
  debug: SearchDebug
}

export interface DiagnoseStage {
  name: string
  elapsed_ms: number
  target_hit: boolean
  target_rank: number
  target_score: number
  total_candidates: number
  path_hits: Record<string, { rank: number; score: number }>
  extra: Record<string, any>
}

export interface DiagnoseResult {
  query: string
  gold_filenames: string[]
  verdict: string
  final_rank: number
  total_ms: number
  stages: DiagnoseStage[]
  content_quality: Record<string, any>
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
    index_status?: string
    file_status?: string
  }) {
    const qs = new URLSearchParams()
    if (params?.page) qs.set('page', String(params.page))
    if (params?.page_size) qs.set('page_size', String(params.page_size))
    if (params?.file_type) qs.set('file_type', params.file_type)
    if (params?.index_status) qs.set('index_status', params.index_status)
    if (params?.file_status) qs.set('file_status', params.file_status)
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

  getOriginalFileUrl(id: string) {
    return `${BASE}/documents/${id}/file`
  },

  // 版本管理
  getVersions(docId: string) {
    return request<{ doc_id: string; current_version: number; versions: Array<{
      version: number; change_type: string; change_summary: string | null;
      content_hash: string; created_at: string;
    }> }>(`/documents/${docId}/versions`)
  },

  getVersion(docId: string, version: number) {
    return request<{
      version: number; raw_text: string; content_hash: string;
      file_path: string | null; change_type: string; change_summary: string | null;
      created_at: string;
    }>(`/documents/${docId}/versions/${version}`)
  },

  getVersionFileUrl(docId: string, version: number) {
    return `${BASE}/documents/${docId}/versions/${version}/file`
  },

  getVersionDiff(docId: string, fromVersion: number, toVersion: number) {
    return request<{
      from_version: number; to_version: number;
      diff: string; stats: { added: number; removed: number };
    }>(`/documents/${docId}/versions/diff?from=${fromVersion}&to=${toVersion}`)
  },

  rollbackVersion(docId: string, version: number) {
    return request<{
      new_version: number; rolled_back_from: number;
      rolled_back_to_content_of: number; index_status: string;
    }>(`/documents/${docId}/versions/${version}/rollback`, { method: 'POST' })
  },

  // 同步控制
  triggerSync() {
    return request<{ triggered: boolean; pending_count: number; message: string }>(
      '/sync', { method: 'POST' }
    )
  },

  getSyncStatus() {
    return request<{
      watch_enabled: boolean; watch_directories: string[];
      last_pipeline_at: string | null; pending_count: number;
      schedule_hours: number; next_scheduled_at: string | null;
    }>('/sync/status')
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

  // 日志
  listLogs(params?: {
    page?: number
    page_size?: number
    level?: string
    doc_id?: string
    trace_id?: string
    start_time?: string
    end_time?: string
  }) {
    const qs = new URLSearchParams()
    if (params?.page) qs.set('page', String(params.page))
    if (params?.page_size) qs.set('page_size', String(params.page_size))
    if (params?.level) qs.set('level', params.level)
    if (params?.doc_id) qs.set('doc_id', params.doc_id)
    if (params?.trace_id) qs.set('trace_id', params.trace_id)
    if (params?.start_time) qs.set('start_time', params.start_time)
    if (params?.end_time) qs.set('end_time', params.end_time)
    return request<LogList>(`/logs?${qs}`)
  },

  clearLogs(keepDays = 7) {
    return request<{ deleted_count: number; keep_days: number }>(
      `/logs?keep_days=${keepDays}`, { method: 'DELETE' }
    )
  },

  getLogStreamUrl() {
    return `${BASE}/logs/stream`
  },

  // 检索（含全链路调试信息）
  search(query: string) {
    return request<SearchResult>('/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    })
  },

  // 检索诊断（逐段追踪 + 耗时 + 内容质量）
  diagnose(query: string, goldFilenames: string[]) {
    return request<DiagnoseResult>('/search/diagnose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, gold_filenames: goldFilenames }),
    })
  },
}
