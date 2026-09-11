import { describe, expect, it } from 'vitest'
import {
  formatOperationStages,
  memorySourceHref,
  operationActions,
  operationStatusLabel,
  operationSubject,
  parsePolicyText,
  validateDirectiveFields,
  validateModelFields,
} from '../memory-page'

describe('memory page view model', () => {
  it('builds an encoded document source link and formats operation stages', () => {
    expect(memorySourceHref('project plan/一')).toBe('/documents/project%20plan%2F%E4%B8%80')
    expect(formatOperationStages({ retain: 'done', consolidate: 'running' })).toBe(
      'retain:done · consolidate:running',
    )
    expect(formatOperationStages({})).toBe('—')
  })

  it('labels each operation kind without empty session placeholders', () => {
    const base = { id: 'op', status: 'indexed', stages: {}, attempts: 0, tokens: 0, cost_microusd: 0 }
    expect(operationSubject({ ...base, kind: 'document', subject: 'plan.md', document_id: 'doc' })).toBe(
      '文件 · plan.md',
    )
    expect(operationSubject({ ...base, kind: 'consolidation', subject: '默认归纳范围' })).toBe(
      '归纳 · 默认归纳范围',
    )
    expect(operationStatusLabel('pending')).toBe('等待处理')
    expect(operationActions('indexed')).toEqual({ retry: false, cancel: false })
    expect(operationActions('failed')).toEqual({ retry: true, cancel: true })
  })

  it('validates management forms and policy objects', () => {
    expect(validateModelFields('', 'Overview', 'What changed?')).toContain('模型 ID')
    expect(validateModelFields('overview', 'Overview', 'What changed?')).toBeNull()
    expect(validateDirectiveFields('safe', '', 'Never expose secrets')).toContain('名称')
    expect(validateDirectiveFields('safe', 'Safety', 'Never expose secrets')).toBeNull()
    expect(parsePolicyText('{"scope":"team"}')).toEqual({ scope: 'team' })
    expect(() => parsePolicyText('[]')).toThrow('JSON 对象')
  })
})
