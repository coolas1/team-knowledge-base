import { describe, expect, it } from 'vitest'
import {
  formatOperationStages,
  memorySourceHref,
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

  it('validates management forms and policy objects', () => {
    expect(validateModelFields('', 'Overview', 'What changed?')).toContain('模型 ID')
    expect(validateModelFields('overview', 'Overview', 'What changed?')).toBeNull()
    expect(validateDirectiveFields('safe', '', 'Never expose secrets')).toContain('名称')
    expect(validateDirectiveFields('safe', 'Safety', 'Never expose secrets')).toBeNull()
    expect(parsePolicyText('{"scope":"team"}')).toEqual({ scope: 'team' })
    expect(() => parsePolicyText('[]')).toThrow('JSON 对象')
  })
})
