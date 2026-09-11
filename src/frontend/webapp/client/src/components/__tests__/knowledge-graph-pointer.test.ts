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
  return {
    arcs,
    beginPath() {},
    arc(_x: number, _y: number, r: number) {
      arcs.push(r)
    },
    fill() {},
    stroke() {},
    fillText() {},
    set fillStyle(_v: string) {},
    set strokeStyle(_v: string) {},
    set lineWidth(_v: number) {},
    set font(_v: string) {},
    set textAlign(_v: string) {},
    set textBaseline(_v: string) {},
  } as unknown as CanvasRenderingContext2D & { arcs: number[] }
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
})
