import { useState } from 'react'
import { api, type SearchResult } from '../api/client'

// 单个流程阶段的徽标
function Stage({ label, detail, ms, accent }: { label: string; detail: string; ms?: number; accent?: string }) {
  return (
    <div style={{
      flex: 1, minWidth: 120, padding: '10px 12px', borderRadius: 8,
      background: '#fff', border: `1px solid ${accent || '#e8e8e8'}`,
    }}>
      <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 600, color: accent || '#333' }}>{detail}</div>
      {ms !== undefined && <div style={{ fontSize: 11, color: '#bbb', marginTop: 2 }}>{ms} ms</div>}
    </div>
  )
}

function scoreColor(score: number): string {
  if (score >= 0.1) return '#3fb950'
  if (score >= 0.03) return '#d29922'
  return '#f85149'
}

export function SearchPage() {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<SearchResult | null>(null)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const doSearch = async () => {
    if (!query.trim()) return
    setLoading(true)
    setError('')
    setResult(null)
    try {
      const r = await api.search(query.trim())
      setResult(r)
    } catch (e: any) {
      setError(e.message || '检索失败')
    } finally {
      setLoading(false)
    }
  }

  const toggle = (i: number) => {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(i) ? next.delete(i) : next.add(i)
      return next
    })
  }

  const d = result?.debug

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 24, background: '#fafafa' }}>
      {/* 搜索框 */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && doSearch()}
          placeholder="输入查询，查看检索全链路召回详情…"
          style={{
            flex: 1, padding: '10px 14px', fontSize: 15, borderRadius: 8,
            border: '1px solid #d9d9d9', outline: 'none',
          }}
        />
        <button
          onClick={doSearch}
          disabled={loading}
          style={{
            padding: '10px 28px', borderRadius: 8, border: 'none',
            background: '#1890ff', color: '#fff', fontSize: 15,
            cursor: loading ? 'wait' : 'pointer', opacity: loading ? 0.6 : 1,
          }}
        >{loading ? '检索中…' : '搜索'}</button>
      </div>

      {error && <div style={{ color: '#f85149', marginBottom: 16 }}>错误：{error}</div>}

      {d && (
        <>
          {/* 全链路流程概览 */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#666', marginBottom: 8 }}>
              检索全链路（总耗时 {d.total_ms} ms，最终 {d.final_chunks} 个 chunks）
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <Stage label="① Query 改写" detail={d.rewrite.rewritten ? '已改写' : '原样'} ms={d.rewrite.elapsed_ms} accent="#4493f8" />
              <Stage label="② L1 多路召回" detail={`${d.recall.vector_main}+${d.recall.vector_expanded}+${d.recall.bm25}`} ms={d.recall.elapsed_ms} accent="#4493f8" />
              <Stage label="③ RRF 融合" detail={`${d.recall.rrf_merged} 候选`} accent="#a371f7" />
              <Stage label="④ 迭代扩展" detail={`+${d.iterative_expand.added}`} ms={d.iterative_expand.elapsed_ms} accent="#a371f7" />
              <Stage label="⑤ Reranker" detail={`${d.reranker.input}→${d.reranker.survivors}`} ms={d.reranker.elapsed_ms} accent="#d29922" />
              <Stage label="⑥ 图谱增强" detail={`${d.graph.entities}实体+${d.graph.graph_chunks}chunks`} ms={d.graph.elapsed_ms} accent="#3fb950" />
            </div>
          </div>

          {/* Query 改写详情 */}
          <div style={{ background: '#fff', border: '1px solid #e8e8e8', borderRadius: 8, padding: 16, marginBottom: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#666', marginBottom: 10 }}>Query 改写详情</div>
            <div style={{ fontSize: 13, marginBottom: 6 }}>
              <span style={{ color: '#999' }}>原始：</span>{d.rewrite.original}
            </div>
            <div style={{ fontSize: 13, marginBottom: 6 }}>
              <span style={{ color: '#999' }}>改写：</span>
              <span style={{ color: d.rewrite.rewritten !== d.rewrite.original ? '#4493f8' : '#333' }}>
                {d.rewrite.rewritten}
              </span>
            </div>
            <div style={{ fontSize: 13, marginBottom: 6 }}>
              <span style={{ color: '#999' }}>关键词：</span>
              {d.rewrite.keywords.map((k, i) => (
                <span key={i} style={{
                  display: 'inline-block', margin: '0 4px 4px 0', padding: '1px 8px',
                  background: '#e6f4ff', color: '#1890ff', borderRadius: 10, fontSize: 12,
                }}>{k}</span>
              ))}
            </div>
            {d.rewrite.expanded_queries.length > 0 && (
              <div style={{ fontSize: 13 }}>
                <span style={{ color: '#999' }}>扩展查询：</span>
                {d.rewrite.expanded_queries.map((q, i) => (
                  <span key={i} style={{
                    display: 'inline-block', margin: '0 4px 4px 0', padding: '1px 8px',
                    background: '#f9f0ff', color: '#722ed1', borderRadius: 10, fontSize: 12,
                  }}>{q}</span>
                ))}
              </div>
            )}
          </div>

          {/* 召回结果列表 */}
          <div style={{ fontSize: 13, fontWeight: 600, color: '#666', marginBottom: 8 }}>
            召回结果（{result!.chunks.length} 个，按 reranker 分数排序）
          </div>
          {result!.chunks.map((c, i) => {
            const isOpen = expanded.has(i)
            return (
              <div key={i} style={{
                background: '#fff', border: '1px solid #e8e8e8', borderRadius: 8,
                padding: 14, marginBottom: 8,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }} onClick={() => toggle(i)}>
                  <span style={{
                    width: 26, height: 26, borderRadius: '50%', background: '#f0f0f0',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 12, fontWeight: 700, color: '#666', flexShrink: 0,
                  }}>{i + 1}</span>
                  <span style={{ fontWeight: 600, fontSize: 14, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {c.title || '(无标题)'}
                  </span>
                  <span style={{ fontSize: 12, color: '#999' }}>
                    reranker <b style={{ color: scoreColor(c.reranker_score) }}>{c.reranker_score.toFixed(3)}</b>
                  </span>
                  <span style={{ fontSize: 12, color: '#999' }}>
                    vector <b style={{ color: '#4493f8' }}>{c.vector_score.toFixed(3)}</b>
                  </span>
                  <span style={{ fontSize: 11, color: '#bbb' }}>{isOpen ? '▲' : '▼'}</span>
                </div>
                {isOpen && (
                  <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid #f0f0f0' }}>
                    <div style={{ fontSize: 11, color: '#bbb', marginBottom: 4 }}>doc_id: {c.doc_id}</div>
                    <pre style={{
                      fontSize: 12, color: '#444', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                      background: '#fafafa', padding: 10, borderRadius: 6, maxHeight: 300, overflow: 'auto',
                      fontFamily: 'inherit', margin: 0,
                    }}>{c.chunk_text}</pre>
                  </div>
                )}
              </div>
            )
          })}

          {/* 关联实体 & 文档 */}
          {(result!.related_entities.length > 0 || result!.related_docs.length > 0) && (
            <div style={{ display: 'flex', gap: 16, marginTop: 20, flexWrap: 'wrap' }}>
              {result!.related_entities.length > 0 && (
                <div style={{ flex: 1, minWidth: 280, background: '#fff', border: '1px solid #e8e8e8', borderRadius: 8, padding: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#666', marginBottom: 8 }}>
                    关联实体（{result!.related_entities.length}）
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {result!.related_entities.map((e, i) => (
                      <span key={i} style={{
                        padding: '2px 10px', background: '#f6ffed', color: '#389e0d',
                        borderRadius: 10, fontSize: 12,
                      }}>{e.name} <span style={{ color: '#bbb' }}>·{e.type}</span></span>
                    ))}
                  </div>
                </div>
              )}
              {result!.related_docs.length > 0 && (
                <div style={{ flex: 1, minWidth: 280, background: '#fff', border: '1px solid #e8e8e8', borderRadius: 8, padding: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#666', marginBottom: 8 }}>
                    关联文档（{result!.related_docs.length}）
                  </div>
                  {result!.related_docs.map((rd, i) => (
                    <div key={i} style={{ fontSize: 12, marginBottom: 4 }}>
                      <span style={{ color: '#333' }}>{rd.title}</span>
                      <span style={{ color: '#bbb' }}> ·{rd.relation_type}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {!result && !loading && !error && (
        <div style={{ color: '#999', textAlign: 'center', marginTop: 80 }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>🔍</div>
          <div style={{ fontSize: 16 }}>输入查询开始检索调试</div>
          <div style={{ fontSize: 13, marginTop: 8, color: '#bbb' }}>
            可查看 Query 改写、各路召回数量、Reranker 分数明细，排查召回问题
          </div>
        </div>
      )}
    </div>
  )
}
