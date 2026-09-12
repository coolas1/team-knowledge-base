import { afterEach, expect, it, vi } from 'vitest'
import { pptApi } from '../ppt'

afterEach(() => vi.unstubAllGlobals())

it('binds approval to the displayed revision and propagates stale rejection', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: 'Stale PPT revision; reload before approving' }) })
  vi.stubGlobal('fetch', fetch)
  await expect(pptApi.control('job', 3, 'approve_sample')).rejects.toThrow('Stale PPT revision')
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ revision: 3, action: 'approve_sample' })
})

it('reloads server job state on reconnection rather than local completion', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: 'job', status: 'paused_budget', pages: [{ number: 1, status: 'accepted' }] }) }))
  const state = await pptApi.get('job')
  expect(state.status).toBe('paused_budget')
  expect(state.pages[0].status).toBe('accepted')
})
