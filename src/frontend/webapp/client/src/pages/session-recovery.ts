import { ApiError, SessionRequestTimeoutError } from '../api/client'

/**
 * Session-operation state for the Ask page.
 *
 * The page used to gate its composer on a single `sessionLoading` boolean that
 * every session operation shared. One operation that failed to clear it left
 * the textarea and the send button disabled with no error and no way out but a
 * page reload. This models the operation explicitly instead, so "a session
 * request is in flight" and "a submission is in flight" cannot be conflated,
 * and every outcome settles into a state the page can render.
 */

export type SessionOperationKind = 'restore' | 'switch' | 'delete'

export type SessionOperation =
  | { status: 'loading'; kind: SessionOperationKind; sessionId?: string }
  | { status: 'ready' }
  | {
      status: 'failed'
      kind: SessionOperationKind
      sessionId?: string
      message: string
      retryable: boolean
    }

export function startSessionOperation(
  kind: SessionOperationKind,
  sessionId?: string,
): SessionOperation {
  return { status: 'loading', kind, sessionId }
}

export function sessionOperationSucceeded(): SessionOperation {
  return { status: 'ready' }
}

export function isSessionOperationLoading(operation: SessionOperation): boolean {
  return operation.status === 'loading'
}

/**
 * True when the operation failed in a way worth offering a retry for. The
 * server's own retryable flag wins when it sent one; otherwise a deadline
 * expiry and any transport-level failure are retryable, and so is any 5xx.
 * A 4xx is the caller's fault, and retrying it unchanged would just fail again.
 */
function isRetryable(caught: unknown): boolean {
  if (caught instanceof SessionRequestTimeoutError) return true
  if (caught instanceof ApiError) {
    if (caught.retryable) return true
    return caught.status >= 500 || caught.status === 0
  }
  // A transport failure (offline, proxy down) reached us as a raw TypeError.
  return true
}

/**
 * Turn a caught value into the message shown to the user. A deadline expiry is
 * reported as a timeout rather than as a server error, so the two are
 * distinguishable when something goes wrong.
 */
export function sessionFailureMessage(caught: unknown): string {
  if (caught instanceof SessionRequestTimeoutError) {
    return `请求超时（${Math.round(caught.timeoutMs / 1000)} 秒无响应），请重试。`
  }
  if (caught instanceof ApiError) return caught.message
  if (caught instanceof Error) return caught.message
  return String(caught)
}

export function sessionOperationFailed(
  kind: SessionOperationKind,
  sessionId: string | undefined,
  caught: unknown,
): SessionOperation {
  return {
    status: 'failed',
    kind,
    ...(sessionId === undefined ? {} : { sessionId }),
    message: sessionFailureMessage(caught),
    retryable: isRetryable(caught),
  }
}

/**
 * The operation to re-run when the user asks to retry, if there is one.
 *
 * A switch or delete names its target; without an id there is nothing to
 * re-run, so offering a retry would put a button on screen that does nothing.
 * (A restore needs no target — it re-reads the session list.)
 */
export function retryTarget(
  operation: SessionOperation,
): { kind: SessionOperationKind; sessionId?: string } | undefined {
  if (operation.status !== 'failed' || !operation.retryable) return undefined
  if (operation.kind !== 'restore' && !operation.sessionId) return undefined
  return {
    kind: operation.kind,
    ...(operation.sessionId === undefined ? {} : { sessionId: operation.sessionId }),
  }
}

export function sessionOperationMessage(operation: SessionOperation): string {
  return operation.status === 'failed' ? operation.message : ''
}

/**
 * What the thread should show after a failed operation.
 *
 * A failed restore or switch means the transcript on screen no longer
 * corresponds to the session we intended to load, so it is cleared rather than
 * left as a half-applied thread the user might read as current. A failed
 * delete leaves the active conversation untouched.
 */
export function threadClearedAfterFailure(kind: SessionOperationKind): boolean {
  return kind !== 'delete'
}

/**
 * Decide which session a send should target.
 *
 * The composer is usable while a restore is still running, so a send can
 * arrive before the restore has settled `sessionRef`. Deciding on
 * `sessionRef.current` alone would read it as "no session" and create a
 * second one alongside the session the restore is about to install.
 *
 * So: wait for the in-flight restore to settle, then read the current session
 * again — reusing it if the restore installed one, and creating one only when
 * there genuinely is none (a failed restore, or a fresh conversation).
 *
 * ``currentSessionId`` is a callback, not a value, precisely because the
 * restore may set it during the await.
 */
export async function resolveSendSession(
  pendingRestore: Promise<unknown> | undefined,
  currentSessionId: () => string | undefined,
  createSession: () => Promise<string>,
): Promise<{ id: string; created: boolean }> {
  if (pendingRestore) {
    // Its failure is already surfaced by the restore's own state; a failed
    // restore just means there is no session to reuse.
    await pendingRestore.catch(() => undefined)
  }
  const existing = currentSessionId()
  if (existing) return { id: existing, created: false }
  return { id: await createSession(), created: true }
}
