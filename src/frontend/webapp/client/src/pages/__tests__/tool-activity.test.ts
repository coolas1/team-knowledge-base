import { describe, expect, it } from 'vitest'
import { appendActivity } from '../tool-activity'
describe('tool activity history', () => {
  it('retains failures across repair and success, deduplicates and isolates new turns', () => {
    const failure = { type: 'tool.result' as const, toolCallId: '1', toolName: 'execute_code', isError: true, activity: 'failure' }
    const failed = appendActivity([], failure)
    expect(appendActivity(failed, failure)).toBe(failed)
    const repaired = appendActivity(failed, { type: 'tool.start', toolCallId: '2', toolName: 'execute_code', args: {}, activity: 'execute' })
    const success = appendActivity(repaired, { ...failure, toolCallId: '2', isError: false, activity: 'execute' })
    expect(success.map(item => item.label)).toEqual(['工具失败', '修复程序', '执行程序'])
    expect(success[0].failed).toBe(true)
    expect(appendActivity([], { type: 'message.completed', sessionId: 'new', answer: '', toolCalls: 0 })).toEqual([])
    expect(appendActivity([], { ...failure, activity: 'future' })[0].label).toBe('工具活动')
  })
})
