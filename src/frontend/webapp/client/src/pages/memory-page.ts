import type { MemoryOperation } from '../api/client'

export function memorySourceHref(documentId: string): string {
  return `/documents/${encodeURIComponent(documentId)}`
}

const statusLabels: Record<string, string> = {
  pending: '等待处理',
  processing: '处理中',
  completed: '已完成',
  indexed: '已索引',
  failed: '失败',
  degraded: '部分失败',
  budget_exhausted: '预算已耗尽',
  cancelled: '已取消',
}

export function operationStatusLabel(status: string): string {
  return statusLabels[status] || status
}

export function operationSubject(operation: MemoryOperation): string {
  if (operation.kind === 'conversation') {
    return `对话 · ${operation.session_id || '未知会话'} / ${operation.turn_id || '未知轮次'}`
  }
  if (operation.kind === 'document') return `文件 · ${operation.subject || operation.document_id || '未知文件'}`
  if (operation.kind === 'consolidation') return `归纳 · ${operation.subject || '默认范围'}`
  if (operation.kind === 'mental_model') return `模型 · ${operation.subject || operation.model_id || '未知模型'}`
  return operation.subject || operation.id
}

export function operationActions(status: string): { retry: boolean; cancel: boolean } {
  return {
    retry: ['failed', 'degraded', 'budget_exhausted', 'cancelled'].includes(status),
    cancel: ['pending', 'processing', 'failed', 'degraded'].includes(status),
  }
}

export function formatOperationStages(stages: Record<string, string>): string {
  return Object.entries(stages).map(([key, value]) => `${key}:${value}`).join(' · ') || '—'
}

export function validateModelFields(id: string, name: string, sourceQuery: string): string | null {
  return id && name && sourceQuery ? null : '模型 ID、名称和来源问题不能为空'
}

export function validateDirectiveFields(id: string, name: string, content: string): string | null {
  return id && name && content ? null : '指令 ID、名称和内容不能为空'
}

export function parsePolicyText(text: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text)
  if (!value || Array.isArray(value) || typeof value !== 'object') {
    throw new Error('策略必须是 JSON 对象')
  }
  return value as Record<string, unknown>
}
