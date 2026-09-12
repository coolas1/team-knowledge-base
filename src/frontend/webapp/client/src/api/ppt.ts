export interface SlideDraft {
  title: string; points: string[]; layout: string; notes: string; reference_document_ids: string[]
}
export interface DeckDraft {
  title: string; style: string; context: string; source_document_ids: string[]; pages: SlideDraft[]
}
export interface PPTJob {
  id: string; revision: number; status: string; spec: DeckDraft
  backend: { model: string }; budget: { image_attempts: number; tokens?: number }
  accounting: { image_attempts?: number; tokens?: number; unknown_usage?: number }
  error?: string; artifact?: { download_url: string }
  pages: { number: number; status: string; error?: string; preview_url?: string; reference_regions?: { document_id: string; box: number[] }[]; qa?: { passed: boolean; reason?: string } }[]
}
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/ppt/jobs${url}`, options)
  const body = await response.json()
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'PPT 请求未完成，请检查内容后重试')
  return body as T
}
export const pptApi = {
  get: (id: string, signal?: AbortSignal) => request<PPTJob>(`/${encodeURIComponent(id)}`, { signal }),
  create: (spec: DeckDraft) => request<{ id: string }>('', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ spec }) }),
  control: (id: string, revision: number, action: string, extra: object = {}) => request<PPTJob>(`/${encodeURIComponent(id)}/control`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision, action, ...extra }) }),
}
