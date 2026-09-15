import { describe, expect, it } from 'vitest'
import { buildArchiveTree } from '../ArchivePage'

describe('archive directory tree', () => {
  it('builds nested nodes and synthesizes missing parent directories', () => {
    const tree = buildArchiveTree([
      {
        candidate_id: '项目/凤凰 CRM',
        description: '凤凰项目资料',
        doc_count: 2,
        documents: [
          { id: 'roadmap', title: '路线图.md', file_type: 'markdown', status: 'indexed', overview: '', created_at: null },
          { id: 'risks', title: '风险.md', file_type: 'markdown', status: 'indexed', overview: '', created_at: null },
        ],
      },
      {
        candidate_id: '财务/报销',
        description: '报销材料',
        doc_count: 1,
        documents: [
          { id: 'invoice', title: '发票.md', file_type: 'markdown', status: 'indexed', overview: '', created_at: null },
        ],
      },
      { candidate_id: '待整理', description: '人工待整理', doc_count: 0, documents: [] },
    ])

    expect(tree.map((node) => node.candidate_id)).toEqual(['财务', '待整理', '项目'])
    expect(tree[0]).toMatchObject({ candidate_id: '财务', synthetic: true })
    expect(tree[0].children[0]).toMatchObject({
      candidate_id: '财务/报销',
      name: '报销',
      doc_count: 1,
      synthetic: false,
    })
    expect(tree[2].children[0].candidate_id).toBe('项目/凤凰 CRM')
    expect(tree[2].children[0].documents.map((document) => document.id)).toEqual([
      'roadmap',
      'risks',
    ])
  })
})
