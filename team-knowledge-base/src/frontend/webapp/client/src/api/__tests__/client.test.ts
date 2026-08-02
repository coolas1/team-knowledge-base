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

  it('throws on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, statusText: 'boom', json: async () => ({}) })
    await expect(api.getFullGraph()).rejects.toThrow()
  })
})
