import { describe, expect, it } from 'vitest'
import {
  clearedForRetry,
  detailView,
  latestFailureMessage,
  loadFailureMessage,
  shouldPoll,
  showsFailurePanel,
} from '../document-detail'
import type { Document } from '../../api/client'

function doc(overrides: Partial<Document> = {}): Document {
  return {
    id: 'd1',
    title: 'a.md',
    file_type: 'markdown',
    status: 'indexed',
    ...overrides,
  } as Document
}

describe('document detail view state', () => {
  it('shows the error state (not an eternal spinner) when loading fails', () => {
    expect(detailView(null, '加载失败：文档不存在')).toBe('error')
    expect(detailView(null, '')).toBe('loading')
    expect(detailView(doc(), '')).toBe('document')
  })

  it('names the failure cause', () => {
    expect(loadFailureMessage(new Error('文档不存在: abc'))).toBe(
      '加载失败：文档不存在: abc',
    )
  })
})

describe('document detail polling', () => {
  it('polls only while the document is still processing', () => {
    expect(shouldPoll('pending')).toBe(true)
    expect(shouldPoll('processing')).toBe(true)
    expect(shouldPoll('indexed')).toBe(false)
    // 版本化编辑的后台重索引死亡 → failed 是终态，必须停止轮询
    expect(shouldPoll('failed')).toBe(false)
  })

  it('renders the failed panel when a processing document reaches failed', () => {
    expect(showsFailurePanel('processing')).toBe(false)
    expect(showsFailurePanel('failed')).toBe(true)
  })

  it('surfaces a versioned edit whose background reindex died', () => {
    // 轮询中的新版本（processing）→ 后台重索引死亡 → failed
    const processing = doc({ status: 'processing' })
    expect(shouldPoll(processing.status)).toBe(true)
    expect(showsFailurePanel(processing.status)).toBe(false)

    const failed = doc({
      status: 'failed',
      error_msg: 'RuntimeError: reindex died',
    })
    expect(shouldPoll(failed.status)).toBe(false) // 轮询停止
    expect(showsFailurePanel(failed.status)).toBe(true) // 失败面板可见
    expect(latestFailureMessage('', failed.error_msg)).toContain('reindex died')
  })
})

describe('failed panel shows the latest error only', () => {
  it('prefers the fresh retry error over the stored one (no stacking)', () => {
    expect(latestFailureMessage('重试失败 · 稍后再试', '上一次失败')).toBe(
      '重试失败 · 稍后再试',
    )
  })

  it('falls back to the stored error, then to a generic message', () => {
    expect(latestFailureMessage('', '存储的错误')).toBe('存储的错误')
    expect(latestFailureMessage('', null)).toBe(
      '处理任务未完成，服务未返回具体原因。',
    )
  })

  it('clears the displayed stored error when a retry starts', () => {
    const cleared = clearedForRetry(
      doc({ status: 'failed', error_msg: '上一次失败' }),
    )
    expect(cleared.status).toBe('pending')
    expect(cleared.error_msg).toBeUndefined()
    // doc 对象不被就地修改
    const original = doc({ status: 'failed', error_msg: '上一次失败' })
    clearedForRetry(original)
    expect(original.error_msg).toBe('上一次失败')
  })
})
