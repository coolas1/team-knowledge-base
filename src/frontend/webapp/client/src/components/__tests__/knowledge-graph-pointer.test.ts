import { describe, expect, it, vi } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

// 捕获传给 ForceGraph2D 的 props（node 环境无 DOM，静态渲染即可）
const captured = vi.hoisted(() => ({ props: {} as any }))
vi.mock('react-force-graph-2d', () => ({
  default: (props: any) => {
    captured.props = props
    return null
  },
}))

import { KnowledgeGraph } from '../KnowledgeGraph'

function recordingCtx() {
  const arcs: number[] = []
  const labels: string[] = []
  return {
    arcs,
    labels,
    beginPath() {},
    arc(_x: number, _y: number, r: number) {
      arcs.push(r)
    },
    fill() {},
    stroke() {},
    fillText(value: string) { labels.push(value) },
    set fillStyle(_v: string) {},
    set strokeStyle(_v: string) {},
    set lineWidth(_v: number) {},
    set font(_v: string) {},
    set textAlign(_v: string) {},
    set textBaseline(_v: string) {},
  } as unknown as CanvasRenderingContext2D & { arcs: number[]; labels: string[] }
}

function renderGraph() {
  renderToStaticMarkup(
    createElement(KnowledgeGraph, {
      nodes: [{ name: 'Acme', type: 'Company', description: '', sources: [] }],
      links: [],
      searchQuery: '',
      onNodeClick: () => {},
      selectedNodeName: null,
    }),
  )
}

describe('graph node pointer hit area', () => {
  it('passes nodePointerAreaPaint to the force graph', () => {
    renderGraph()
    expect(typeof captured.props.nodePointerAreaPaint).toBe('function')
  })

  it('paints at least twice the rendered node radius', () => {
    renderGraph()
    const ctx = recordingCtx()

    captured.props.nodeCanvasObject(
      { x: 0, y: 0, name: 'Acme', type: 'Company' },
      ctx,
      1,
    )
    const nodeRadius = ctx.arcs[0]

    const pointerCtx = recordingCtx()
    captured.props.nodePointerAreaPaint(
      { x: 0, y: 0, name: 'Acme', type: 'Company' },
      'rgba(0,0,0,0)',
      pointerCtx,
      1,
    )
    const pointerRadius = pointerCtx.arcs[0]

    expect(nodeRadius).toBeGreaterThan(0)
    expect(pointerRadius).toBeGreaterThanOrEqual(2 * nodeRadius)
  })

  it('auto-fits once and limits default labels in a dense graph', () => {
    const nodes = Array.from({ length: 50 }, (_, index) => ({
      name: `Node ${index}`,
      type: 'Company',
      description: '',
      sources: [],
    }))
    const links = nodes.slice(1).map((node) => ({
      source: 'Node 0',
      target: node.name,
      type: 'related',
      description: '',
    }))
    renderToStaticMarkup(
      createElement(KnowledgeGraph, {
        nodes,
        links,
        searchQuery: '',
        onNodeClick: () => {},
        selectedNodeName: null,
      }),
    )
    expect(typeof captured.props.onEngineStop).toBe('function')
    const ctx = recordingCtx()
    for (const node of nodes) {
      captured.props.nodeCanvasObject({ ...node, x: 0, y: 0 }, ctx, 1)
    }
    expect(ctx.labels.length).toBe(12)
    expect(ctx.labels).toContain('Node 0')
  })
})
