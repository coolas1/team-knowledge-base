import { describe, expect, it } from 'vitest'
import { relatedDocLabel } from '../related-docs'

describe('related document labels', () => {
  it('renders the relation type when present', () => {
    expect(relatedDocLabel({ title: '周报.md', relation_type: 'REFERENCES' })).toBe(
      '周报.md (REFERENCES)',
    )
  })

  it('never renders empty parentheses when the relation type is missing', () => {
    for (const relation of [undefined, null, '', '   ']) {
      const label = relatedDocLabel({ title: '周报.md', relation_type: relation })
      expect(label).toBe('周报.md')
      expect(label).not.toContain('()')
    }
  })
})
