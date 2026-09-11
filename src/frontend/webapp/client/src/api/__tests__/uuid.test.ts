import { afterEach, describe, expect, it, vi } from 'vitest'

import { randomUUID } from '../uuid'

// Captured before any stubbing: the fallback path still needs real entropy,
// only the crypto surface the browser exposes is simulated.
const realCrypto = globalThis.crypto
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

afterEach(() => vi.unstubAllGlobals())

describe('randomUUID', () => {
  it('delegates to crypto.randomUUID when the browser exposes it', () => {
    vi.stubGlobal('crypto', { randomUUID: vi.fn(() => 'secure-context-uuid') })

    expect(randomUUID()).toBe('secure-context-uuid')
  })

  it('builds v4 UUIDs from getRandomValues in an insecure context', () => {
    // Insecure contexts expose getRandomValues but not randomUUID.
    vi.stubGlobal('crypto', {
      getRandomValues: <T extends ArrayBufferView>(array: T): T =>
        realCrypto.getRandomValues(array),
    })

    const ids = Array.from({ length: 100 }, () => randomUUID())

    for (const id of ids) {
      // The regex pins the version nibble to 4 and the variant nibble to 8/9/a/b.
      expect(id).toMatch(UUID_V4)
    }
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('throws a descriptive error when the browser exposes neither API', () => {
    vi.stubGlobal('crypto', {})

    expect(() => randomUUID()).toThrow(/crypto\.randomUUID.*crypto\.getRandomValues/)
  })
})
