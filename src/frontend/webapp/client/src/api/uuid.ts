/**
 * UUIDs without requiring a secure browser context.
 *
 * The deployment serves the SPA over plain HTTP on the LAN, which browsers
 * classify as an insecure context; `crypto.randomUUID` is exposed only in
 * secure ones (HTTPS or localhost), where it is `undefined`. There is no
 * such gate on `crypto.getRandomValues`, so fall back to assembling an
 * RFC 4122 v4 UUID by hand when the convenience method is missing.
 */
export function randomUUID(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  if (typeof crypto.getRandomValues === 'function') return v4FromRandomBytes()
  throw new Error(
    '无法生成消息 ID：浏览器未提供 crypto.randomUUID 或 crypto.getRandomValues',
  )
}

function v4FromRandomBytes(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  // Overwrite the version and variant nibbles before hex encoding.
  bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80 // RFC 4122 variant
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`
}
