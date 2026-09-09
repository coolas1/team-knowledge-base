export function memorySourceHref(documentId: string): string {
  return `/documents/${encodeURIComponent(documentId)}`
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
