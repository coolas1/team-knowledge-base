import { describe, expect, it } from 'vitest'
import {
  applyAcceptance,
  applyCompletion,
  applyFailure,
  optimisticMessages,
  reconcileMessages,
} from '../session-transcript'

describe('session transcript reconciliation', () => {
  it('keeps one row per stable id across repeated acceptance and completion', () => {
    let messages = optimisticMessages('question', 'client-1')
    const accepted = {
      type: 'message.accepted' as const,
      sessionId: 's1', turnId: 't1', messageId: 'u1', clientMessageId: 'client-1', status: 'accepted' as const,
    }
    messages = applyAcceptance(messages, accepted)
    messages = applyAcceptance(messages, { ...accepted, replayed: true })
    messages = applyCompletion(messages, {
      type: 'message.completed', sessionId: 's1', answer: 'answer', toolCalls: 0,
      turnId: 't1', messageId: 'a1', clientMessageId: 'client-1',
    })
    expect(messages.map((message) => [message.id, message.status])).toEqual([
      ['u1', 'completed'], ['a1', 'completed'],
    ])
  })

  it('distinguishes unsaved rejection from an accepted failed turn', () => {
    const optimistic = optimisticMessages('question', 'client-1')
    expect(applyFailure(optimistic, 'client-1', false)).toEqual([
      expect.objectContaining({ role: 'user', status: 'unsaved' }),
    ])
    const accepted = applyAcceptance(optimistic, {
      type: 'message.accepted', sessionId: 's1', turnId: 't1', messageId: 'u1',
      clientMessageId: 'client-1', status: 'accepted',
    })
    expect(applyFailure(accepted, 'client-1', true)).toEqual([
      expect.objectContaining({ role: 'user', status: 'failed' }),
    ])
    for (const status of ['cancelled', 'interrupted'] as const) {
      expect(applyFailure(accepted, 'client-1', true, status)).toEqual([
        expect.objectContaining({ role: 'user', status }),
      ])
    }
  })

  it('reconciles an accepted disconnected turn without duplication', () => {
    const local = applyAcceptance(optimisticMessages('question', 'client-1'), {
      type: 'message.accepted', sessionId: 's1', turnId: 't1', messageId: 'u1',
      clientMessageId: 'client-1', status: 'accepted',
    })
    const detail = {
      id: 's1', messageCount: 1, streaming: false,
      messages: [{ id: 'u1', turnId: 't1', clientMessageId: 'client-1', role: 'user' as const, text: 'question', status: 'interrupted' as const }],
    }
    expect(reconcileMessages(local, detail)).toEqual([
      expect.objectContaining({ id: 'u1', status: 'interrupted' }),
    ])
  })
})
