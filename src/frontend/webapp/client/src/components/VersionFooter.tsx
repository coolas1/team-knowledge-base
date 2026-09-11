import { useEffect, useState } from 'react'
import { fetchVersion, type VersionInfo } from '../api/client'

/** `v0.2.0 (abc1234)`; the commit is omitted when the instance has none. */
export function formatVersionLabel(info: VersionInfo): string {
  return info.commit ? `v${info.version} (${info.commit})` : `v${info.version}`
}

/**
 * Shows which release is running, from the BFF's /version endpoint.
 * A failed fetch stays silent (no footer) — version display must never
 * surface as an error to the user.
 */
export function VersionFooter() {
  const [info, setInfo] = useState<VersionInfo | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchVersion()
      .then((v) => {
        if (!cancelled) setInfo(v)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  if (!info) return null
  return (
    <footer className="app-version-footer">
      <span>{formatVersionLabel(info)}</span>
    </footer>
  )
}
