import { describe, expect, it, vi } from 'vitest'

import { ApiError, SessionRequestTimeoutError } from '../../api/client'
import {
  isSessionOperationLoading,
  resolveSendSession,
  retryTarget,
  sessionFailureMessage,
  sessionOperationFailed,
  sessionOperationMessage,
  sessionOperationSucceeded,
  startSessionOperation,
  threadClearedAfterFailure,
} from '../session-recovery'

describe('session recovery state', () => {
  it('reports loading only while an operation is in flight', () => {
    expect(isSessionOperationLoading(sessionOperationSucceeded())).toBe(false)

    const loading = startSessionOperation('restore')
    expect(loading).toEqual({ status: 'loading', kind: 'restore' })
    expect(isSessionOperationLoading(loading)).toBe(true)
  })

  it('surfaces a server error with the server message and offers a retry', () => {
    const operation = sessionOperationFailed(
      'switch',
      's2',
      new ApiError('Pi Agent 当前不可用', 503),
    )

    expect(operation).toMatchObject({
      status: 'failed',
      kind: 'switch',
      sessionId: 's2',
      message: 'Pi Agent 当前不可用',
      retryable: true,
    })
    expect(sessionOperationMessage(operation)).toBe('Pi Agent 当前不可用')
    expect(retryTarget(operation)).toEqual({ kind: 'switch', sessionId: 's2' })
  })

  it('reports a deadline expiry as a timeout, distinguishable from a server error', () => {
    const timeout = sessionOperationFailed(
      'restore',
      undefined,
      new SessionRequestTimeoutError(45_000),
    )
    const serverError = sessionOperationFailed(
      'restore',
      undefined,
      new ApiError('Pi Agent 响应超时', 504),
    )

    expect(sessionOperationMessage(timeout)).toBe('请求超时（45 秒无响应），请重试。')
    expect(sessionOperationMessage(timeout)).not.toBe(sessionOperationMessage(serverError))
    expect(retryTarget(timeout)).toEqual({ kind: 'restore' })
  })

  it('treats a transport failure as retryable and an unrecoverable 4xx as not', () => {
    const offline = sessionOperationFailed('delete', 's1', new TypeError('Failed to fetch'))
    expect(offline).toMatchObject({ retryable: true, message: 'Failed to fetch' })

    const badRequest = sessionOperationFailed(
      'delete',
      's1',
      new ApiError('invalid agent session id', 400),
    )
    expect(badRequest).toMatchObject({ retryable: false })
    expect(retryTarget(badRequest)).toBeUndefined()
  })

  it('lets a server retryable flag override the status-code default', () => {
    const operation = sessionOperationFailed(
      'switch',
      's1',
      new ApiError('稍后重试', 409, 'conflict', undefined, true),
    )
    expect(retryTarget(operation)).toEqual({ kind: 'switch', sessionId: 's1' })
  })

  it('offers no retry for a targeted operation that has no target', () => {
    // A send that could not reach any session reports a session failure with
    // no id. A retry button there would do nothing when clicked.
    const noTarget = sessionOperationFailed(
      'switch',
      undefined,
      new ApiError('Pi Agent 当前不可用', 503),
    )
    expect(retryTarget(noTarget)).toBeUndefined()
    // The message is still surfaced — only the useless affordance is dropped.
    expect(sessionOperationMessage(noTarget)).toBe('Pi Agent 当前不可用')

    // A restore needs no target, so it stays retryable.
    expect(
      retryTarget(sessionOperationFailed('restore', undefined, new TypeError('offline'))),
    ).toEqual({ kind: 'restore' })
  })

  it('has nothing to retry when no operation failed', () => {
    expect(retryTarget(startSessionOperation('restore'))).toBeUndefined()
    expect(retryTarget(sessionOperationSucceeded())).toBeUndefined()
    expect(sessionOperationMessage(startSessionOperation('restore'))).toBe('')
  })

  it('clears a half-applied thread after a failed restore or switch but not a delete', () => {
    expect(threadClearedAfterFailure('restore')).toBe(true)
    expect(threadClearedAfterFailure('switch')).toBe(true)
    expect(threadClearedAfterFailure('delete')).toBe(false)
  })

  it('falls back to a readable message for a non-Error rejection', () => {
    expect(sessionFailureMessage('boom')).toBe('boom')
  })
})

describe('send target resolution', () => {
  it('waits out an in-flight restore rather than creating a second session', async () => {
    let sessionId: string | undefined
    const createSession = vi.fn(async () => 'created')
    const restore = (async () => {
      await Promise.resolve()
      sessionId = 'restored'
    })()

    const target = await resolveSendSession(restore, () => sessionId, createSession)

    // Exactly one session exists, and it is the restored one.
    expect(target).toEqual({ id: 'restored', created: false })
    expect(createSession).not.toHaveBeenCalled()
  })

  it('creates one session when a send follows a failed restore', async () => {
    let sessionId: string | undefined
    const createSession = vi.fn(async () => 'created')
    const restore = Promise.reject(new Error('restore failed'))

    const target = await resolveSendSession(restore, () => sessionId, createSession)

    expect(target).toEqual({ id: 'created', created: true })
    expect(createSession).toHaveBeenCalledTimes(1)
    expect(sessionId).toBeUndefined()
  })

  it('reuses the active session when nothing is being restored', async () => {
    const createSession = vi.fn(async () => 'created')

    const target = await resolveSendSession(undefined, () => 'existing', createSession)

    expect(target).toEqual({ id: 'existing', created: false })
    expect(createSession).not.toHaveBeenCalled()
  })

  it('creates a session for a fresh conversation', async () => {
    let created = 0
    const createSession = async () => `created-${++created}`

    const target = await resolveSendSession(undefined, () => undefined, createSession)

    expect(target).toEqual({ id: 'created-1', created: true })
  })
})
