import { describe, expect, it } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { NotFoundPage } from '../NotFoundPage'

describe('unknown client routes', () => {
  it('renders a 404 page with navigation home instead of a blank screen', () => {
    const html = renderToStaticMarkup(
      createElement(
        MemoryRouter,
        { initialEntries: ['/no-such-page'] },
        createElement(NotFoundPage),
      ),
    )

    expect(html).toContain('页面不存在')
    expect(html).toContain('/no-such-page')
    expect(html).toContain('href="/"') // 返回已知页面
  })
})
