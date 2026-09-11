import { describe, expect, it, vi } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

vi.mock('../../api/client', () => ({
  fetchVersion: () => Promise.resolve({ version: '0.2.0', commit: 'abc1234' }),
}))

import { VersionFooter, formatVersionLabel } from '../VersionFooter'

describe('formatVersionLabel', () => {
  it('renders version and commit as v<semver> (<commit>)', () => {
    expect(formatVersionLabel({ version: '0.2.0', commit: 'abc1234' })).toBe(
      'v0.2.0 (abc1234)',
    )
  })

  it('omits the parens when the instance has no commit', () => {
    expect(formatVersionLabel({ version: '0.2.0', commit: null })).toBe('v0.2.0')
  })
})

describe('VersionFooter', () => {
  it('renders nothing until /version resolves (no layout shift placeholder)', () => {
    // renderToStaticMarkup does not flush effects: the pre-fetch state is
    // exactly what a server-side or error render would show.
    const markup = renderToStaticMarkup(createElement(VersionFooter))
    expect(markup).toBe('')
  })
})
