import { describe, expect, it } from 'vitest'

import { normalizeLatexDelimiters } from '../markdown-math'

describe('normalizeLatexDelimiters', () => {
  it('normalizes display and inline LaTeX delimiters', () => {
    expect(normalizeLatexDelimiters('before \\(x+y\\)\n\\[\nup[i] = 1\n\\]')).toBe(
      'before $x+y$\n$$\nup[i] = 1\n$$',
    )
  })

  it('does not rewrite delimiters inside fenced code', () => {
    const source = '```tex\n\\[x\\]\n```\n\\[y\\]'
    expect(normalizeLatexDelimiters(source)).toBe('```tex\n\\[x\\]\n```\n$$y$$')
  })
})
