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

  it('does not rewrite inline-code delimiters', () => {
    const source = 'use `\\(foo\\)` and `\\[bar\\]` as literals; render \\(x\\)'
    expect(normalizeLatexDelimiters(source)).toBe(
      'use `\\(foo\\)` and `\\[bar\\]` as literals; render $x$',
    )
  })

  it('honors matching backtick run lengths', () => {
    const source = '``code ` \\(literal\\)`` then \\[formula\\] and ```\\(three\\)```'
    expect(normalizeLatexDelimiters(source)).toBe(
      '``code ` \\(literal\\)`` then $$formula$$ and ```\\(three\\)```',
    )
  })

  it('preserves multiline inline code and differently sized fences', () => {
    const source = [
      '``first \\(literal\\)',
      'second \\[literal\\]`` and \\(formula\\)',
      '````tex',
      '\\[fenced\\]',
      '```',
      '\\(still fenced\\)',
      '````',
      '\\[display\\]',
    ].join('\n')
    expect(normalizeLatexDelimiters(source)).toBe([
      '``first \\(literal\\)',
      'second \\[literal\\]`` and $formula$',
      '````tex',
      '\\[fenced\\]',
      '```',
      '\\(still fenced\\)',
      '````',
      '$$display$$',
    ].join('\n'))
  })
})
