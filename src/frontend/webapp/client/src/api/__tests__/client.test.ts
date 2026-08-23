import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '../client'

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

afterEach(() => mockFetch.mockReset())

describe('api client', () => {
  it('listDocuments calls /documents with query params', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ total: 0, page: 1, page_size: 20, items: [] }),
    })
    await api.listDocuments({ page: 2, page_size: 5 })
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/documents?page=2&page_size=5',
      undefined,
    )
  })

  it('search posts to /search', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ chunks: [], related_entities: [], related_docs: [] }),
    })
    await api.search('acme', 7)
    const [, init] = mockFetch.mock.calls[0]
    expect(init?.method).toBe('POST')
    expect(init?.body).toBe(JSON.stringify({ query: 'acme', top_k: 7 }))
  })

  it('edits document content with PUT', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 'doc-1', status: 'pending' }),
    })

    await api.editContent('doc-1', '# Updated')

    expect(mockFetch).toHaveBeenCalledWith('/api/documents/doc-1/content', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: '# Updated' }),
    })
  })

  it('throws on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, statusText: 'boom', json: async () => ({}) })
    await expect(api.getFullGraph()).rejects.toThrow()
  })

  it('creates an agent session through the BFF', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 's1', messageCount: 0, streaming: false }),
    })

    await api.createAgentSession()

    expect(mockFetch).toHaveBeenCalledWith('/api/agent/sessions', { method: 'POST' })
  })

  it('lists sessions and loads a session detail through the BFF', async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [{ id: 's1', messageCount: 2, streaming: false }] }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          id: 's1',
          messageCount: 2,
          streaming: false,
          messages: [{ role: 'user', text: 'hello' }],
        }),
      })

    const sessions = await api.listAgentSessions()
    const detail = await api.getAgentSession('session/1')

    expect(sessions.items[0].id).toBe('s1')
    expect(detail.messages).toEqual([{ role: 'user', text: 'hello' }])
    expect(mockFetch).toHaveBeenNthCalledWith(1, '/api/agent/sessions', undefined)
    expect(mockFetch).toHaveBeenNthCalledWith(2, '/api/agent/sessions/session%2F1', undefined)
  })

  it('parses SSE events split across arbitrary chunks', async () => {
    const encoder = new TextEncoder()
    const chunks = [
      'event: assistant.delta\ndata: {"type":"assistant.',
      'delta","delta":"你',
      '好"}\n\nevent: message.completed\ndata: {"type":"message.completed","sessionId":"s1",',
      '"answer":"你好","toolCalls":0}\n\n',
    ]
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
        controller.close()
      },
    })
    mockFetch.mockResolvedValueOnce(new Response(body, { status: 200 }))
    const events: string[] = []
    const controller = new AbortController()

    await api.streamAgentMessage('s1', 'hello', (event) => events.push(event.type), controller.signal)

    expect(events).toEqual(['assistant.delta', 'message.completed'])
    const [, init] = mockFetch.mock.calls[0]
    expect(init.signal).toBe(controller.signal)
    expect(init.body).toBe(JSON.stringify({ message: 'hello' }))
  })

  it('surfaces message.failed SSE events as errors', async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'event: message.failed\ndata: {"type":"message.failed","error":"model unavailable"}\n\n',
          ),
        )
        controller.close()
      },
    })
    mockFetch.mockResolvedValueOnce(new Response(body, { status: 200 }))

    await expect(api.streamAgentMessage('s1', 'hello', () => undefined)).rejects.toThrow(
      'model unavailable',
    )
  })

  it('cancels an active agent session', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ cancelled: true, sessionId: 's1' }),
    })

    await api.cancelAgentSession('s1')

    expect(mockFetch).toHaveBeenCalledWith('/api/agent/sessions/s1/cancel', { method: 'POST' })
  })
})
