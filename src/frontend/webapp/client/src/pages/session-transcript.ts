import type {
  AgentConversationMessage,
  AgentSessionDetail,
  AgentTurnStatus,
  PiAgentEvent,
} from '../api/client'
import type { ActivityRecord } from './tool-activity'

export type UiMessageStatus = AgentTurnStatus | 'optimistic' | 'unsaved'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  turnId?: string
  clientMessageId?: string
  timestamp?: string
  status?: UiMessageStatus
  activities?: ActivityRecord[]
}

export function messagesFromDetail(detail: AgentSessionDetail): ChatMessage[] {
  return (detail.messages || []).map((message, index) => ({
    id: message.id || `legacy-${index}-${message.role}`,
    role: message.role,
    text: message.text,
    turnId: message.turnId,
    clientMessageId: message.clientMessageId,
    timestamp: message.timestamp,
    status: message.status,
  }))
}

export function optimisticMessages(text: string, clientMessageId: string): ChatMessage[] {
  return [
    {
      id: `optimistic-${clientMessageId}`,
      role: 'user',
      text,
      clientMessageId,
      status: 'optimistic',
    },
    {
      id: `assistant-${clientMessageId}`,
      role: 'assistant',
      text: '',
      clientMessageId,
      status: 'optimistic',
    },
  ]
}

export function applyAcceptance(
  messages: ChatMessage[],
  event: Extract<PiAgentEvent, { type: 'message.accepted' }>,
): ChatMessage[] {
  return messages.map((message) => {
    if (message.clientMessageId !== event.clientMessageId) return message
    if (message.role === 'user') {
      return { ...message, id: event.messageId, turnId: event.turnId, status: event.status }
    }
    return { ...message, turnId: event.turnId, status: event.status === 'accepted' ? 'running' : event.status }
  })
}

export function applyCompletion(
  messages: ChatMessage[],
  event: Extract<PiAgentEvent, { type: 'message.completed' }>,
): ChatMessage[] {
  return messages.map((message) => {
    const sameTurn = Boolean(event.turnId && message.turnId === event.turnId)
    const sameClient = Boolean(event.clientMessageId && message.clientMessageId === event.clientMessageId)
    if (!sameTurn && !sameClient) return message
    if (message.role === 'assistant') {
      return {
        ...message,
        id: event.messageId || message.id,
        turnId: event.turnId || message.turnId,
        text: event.answer,
        status: 'completed',
      }
    }
    return { ...message, status: 'completed' }
  })
}

export function applyFailure(
  messages: ChatMessage[],
  clientMessageId: string,
  accepted: boolean,
  status: UiMessageStatus = accepted ? 'failed' : 'unsaved',
): ChatMessage[] {
  return messages
    .map((message) => message.clientMessageId === clientMessageId ? { ...message, status } : message)
    .filter((message) => message.role === 'user' || message.text ||
      !['unsaved', 'failed', 'cancelled', 'interrupted'].includes(message.status || ''))
}

export function reconcileMessages(
  local: ChatMessage[],
  detail: AgentSessionDetail,
): ChatMessage[] {
  const durable = messagesFromDetail(detail)
  const activitiesByTurn = new Map(
    local.filter((message) => message.turnId && message.activities?.length)
      .map((message) => [message.turnId!, message.activities!]),
  )
  return durable.map((message) => ({
    ...message,
    activities: message.turnId ? activitiesByTurn.get(message.turnId) : undefined,
  }))
}

export function conversationMessageToUi(message: AgentConversationMessage, index: number): ChatMessage {
  return {
    id: message.id || `legacy-${index}-${message.role}`,
    role: message.role,
    text: message.text,
    turnId: message.turnId,
    clientMessageId: message.clientMessageId,
    timestamp: message.timestamp,
    status: message.status,
  }
}
